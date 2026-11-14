"""pywebview 的 js_api：前端与本地数据之间的唯一桥梁。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from . import (
    __version__, autostart, config, db, importer, metadata, obsidian, potplayer, scanner,
)


class Api:
    def __init__(self):
        self.window = None
        self._last_scan: dict | None = None
        db.init_db()
        metadata.ensure_builtin_loaded()

    def attach_window(self, window) -> None:
        self.window = window

    def get_app_info(self) -> dict:
        return {
            "version": __version__,
            "app_dir": str(config.APP_DIR),
            "db_path": str(config.DB_PATH),
            "potplayer": potplayer.potplayer_exe(),
            "python": sys.version.split()[0],
        }

    def get_stats(self) -> dict:
        return db.row(
            "SELECT "
            "(SELECT COUNT(*) FROM media_files) AS files, "
            "(SELECT COUNT(*) FROM watched_list) AS watched, "
            "(SELECT COUNT(*) FROM watch_sessions) AS sessions, "
            "(SELECT COUNT(*) FROM watch_progress WHERE completed = 0 AND position_ms > 0) AS in_progress, "
            "(SELECT COUNT(*) FROM watch_progress WHERE completed = 1) AS completed"
        ) or {}

    def get_player_status(self) -> dict:
        status = potplayer.read_status()
        if status.get("available") and status.get("media_name"):
            hit = potplayer.resolve_media(status["media_name"])
            status["file_id"] = hit["id"] if hit else None
            status["title_cn"] = (
                (hit or {}).get("title_cn")
                or (hit or {}).get("title_guess")
                or status["media_name"]
            )
        return status

    def get_continue_watching(self, limit: int = 50) -> list[dict]:
        return db.rows(
            "SELECT p.*, f.filename, f.path, f.title_guess, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "f.season_no, f.episode_no "
            "FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
            "WHERE p.completed = 0 AND p.position_ms > 0 "
            "ORDER BY p.last_played_at DESC LIMIT ?",
            (int(limit),),
        )

    def get_completed(self, limit: int = 50) -> list[dict]:
        """已看完的条目，供「继续观看」页做纠错（标记未看 / 重看）。"""
        return db.rows(
            "SELECT p.*, f.filename, f.path, f.title_guess, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "f.season_no, f.episode_no "
            "FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
            "WHERE p.completed = 1 ORDER BY p.last_played_at DESC LIMIT ?",
            (int(limit),),
        )

    def _ensure_progress(self, file_id: int) -> None:
        stamp = db.now()
        db.execute(
            "INSERT OR IGNORE INTO watch_progress"
            "(file_id, position_ms, duration_ms, percent, completed, "
            "first_played_at, last_played_at, updated_at) VALUES (?, 0, 0, 0, 0, ?, ?, ?)",
            (file_id, stamp, stamp, stamp),
        )

    def _set_watch_state(self, file_id: int, completed: int) -> dict:
        """标记已看/未看；两种操作都清空续播点，但绝不删除 watch_sessions 历史。"""
        try:
            fid = int(file_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效的文件 ID"}
        if not db.scalar("SELECT 1 FROM media_files WHERE id = ?", (fid,)):
            return {"ok": False, "error": "找不到该文件记录"}
        self._ensure_progress(fid)
        db.execute(
            "UPDATE watch_progress SET completed = ?, position_ms = 0, percent = 0, "
            "last_played_at = COALESCE(last_played_at, ?), updated_at = ? WHERE file_id = ?",
            (completed, db.now(), db.now(), fid),
        )
        return {"ok": True, "file_id": fid, "completed": completed}

    def mark_watched(self, file_id: int) -> dict:
        return self._set_watch_state(file_id, 1)

    def mark_unwatched(self, file_id: int) -> dict:
        """Kodi 语义：标记未看会同时清除 resume point，历史条目保留。"""
        return self._set_watch_state(file_id, 0)

    def reset_progress(self, file_id: int) -> dict:
        """一键重置进度（等价于「标记未看」），用于纠错与重新观看。"""
        return self._set_watch_state(file_id, 0)

    def get_history(self, limit: int = 100) -> list[dict]:
        return db.rows(
            "SELECT s.*, f.filename, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "f.season_no, f.episode_no, f.path "
            "FROM watch_sessions s LEFT JOIN media_files f ON f.id = s.file_id "
            "ORDER BY s.started_at DESC LIMIT ?",
            (int(limit),),
        )

    def get_watched_list(self, limit: int = 500) -> list[dict]:
        return db.rows("SELECT * FROM watched_list ORDER BY id DESC LIMIT ?", (int(limit),))

    def delete_watched(self, item_id: int) -> dict:
        db.execute("DELETE FROM watched_list WHERE id = ?", (int(item_id),))
        return {"ok": True}

    def get_media_files(self, keyword: str = "", limit: int = 300) -> list[dict]:
        like = f"%{keyword.strip()}%"
        return db.rows(
            "SELECT f.*, COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE f.filename LIKE ? OR COALESCE(f.title_guess,'') LIKE ? "
            "OR COALESCE(f.title_cn,'') LIKE ? "
            "ORDER BY f.id DESC LIMIT ?",
            (like, like, like, int(limit)),
        )

    def preview_import(self, text: str) -> dict:
        """解析预览时就完成中文名自动匹配，结果直接展示，不留待办队列。"""
        return importer.preview(text or "")

    def commit_import(self, rows: list[dict]) -> dict:
        return importer.commit(rows or [])

    def get_settings(self) -> dict:
        settings = db.all_settings()
        settings["db_path"] = str(config.DB_PATH)
        settings["autostart_enabled"] = autostart.is_enabled()
        settings["autostart_command"] = autostart.command()
        settings["potplayer_exists"] = Path(potplayer.potplayer_exe()).exists()
        settings["dictionary"] = metadata.stats()
        settings["obsidian"] = obsidian.status()
        return settings

    def save_settings(self, data: dict) -> dict:
        allowed = {
            "potplayer_path", "poll_interval_sec", "complete_percent", "recording_enabled",
            "http_proxy", "metadata_source", "bangumi_base", "window_width", "window_height",
            "minimize_to_tray", "obsidian_folder", "obsidian_auto_sync",
        }
        for key, value in (data or {}).items():
            if key not in allowed:
                continue
            if key == "obsidian_folder":
                try:
                    value = obsidian.safe_folder(str(value))
                except ValueError:
                    continue  # 非法路径直接忽略，sync 时也会二次拦截
            db.set_setting(key, value)
        if any(k in (data or {}) for k in ("http_proxy", "bangumi_base", "metadata_source")):
            metadata.reset_online()  # 代理或策略变了，下次导入重新探测在线源
        if "autostart" in (data or {}):
            if data["autostart"] in (True, 1, "1", "true", "on"):
                autostart.enable()
            else:
                autostart.disable()
        if "obsidian_vault" in (data or {}):
            obsidian.set_options(vault=str(data["obsidian_vault"]).strip())
        return self.get_settings()

    def get_directories(self) -> list[dict]:
        return scanner.list_directories()

    def add_directory(self, path: str) -> dict:
        result = scanner.add_directory(path or "")
        if result.get("ok"):
            self.scan_now()
        return result

    def remove_directory(self, dir_id: int) -> dict:
        return scanner.remove_directory(int(dir_id))

    def set_directory_enabled(self, dir_id: int, enabled: bool) -> dict:
        return scanner.set_directory_enabled(int(dir_id), bool(enabled))

    def choose_directory(self) -> dict:
        try:
            import webview
            if not self.window:
                return {"ok": False, "error": "窗口尚未就绪，请直接粘贴路径"}
            result = self.window.create_file_dialog(webview.FOLDER_DIALOG)
            if result and len(result):
                return {"ok": True, "path": result[0]}
            return {"ok": False, "error": "已取消"}
        except Exception as exc:
            return {"ok": False, "error": f"对话框不可用，请直接粘贴路径（{exc}）"}

    def scan_now(self) -> dict:
        def work():
            try:
                # scanner.scan() 内部已包含中文名自动匹配，结果当场入库
                self._last_scan = scanner.scan()
            except Exception as exc:
                self._last_scan = {"ok": False, "error": str(exc)}
        threading.Thread(target=work, name="mv-scan", daemon=True).start()
        return {"ok": True, "started": True}

    def get_last_scan(self) -> dict | None:
        return self._last_scan

    def play_file(self, file_id: int, from_start: bool = False) -> dict:
        record = db.row("SELECT * FROM media_files WHERE id = ?", (int(file_id),))
        if not record:
            return {"ok": False, "error": "找不到该文件记录"}
        path = record["path"]
        if not Path(path).exists():
            return {"ok": False, "error": "文件不在扫描库中或已被移动，请先扫描所在目录"}
        position = 0
        if not from_start:
            progress = db.row("SELECT position_ms, completed FROM watch_progress WHERE file_id = ?", (record["id"],))
            if progress and not progress["completed"]:
                position = progress["position_ms"] or 0
        result = potplayer.play(path, position)
        if result.get("ok"):
            result["position_ms"] = position
        return result

    def open_data_folder(self) -> dict:
        config.ensure_dirs()
        try:
            os.startfile(str(config.DATA_DIR))
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def daemon_running(self) -> dict:
        """检查托盘守护进程是否已经在运行（避免重复拉起多个托盘图标）。"""
        try:
            for proc in psutil.process_iter(["cmdline"]):
                cmd = " ".join(proc.info.get("cmdline") or [])
                if "daemon.py" in cmd and "python" in cmd.lower():
                    return {"running": True, "pid": proc.pid}
        except Exception:
            pass
        return {"running": False}

    def start_daemon(self) -> dict:
        state = self.daemon_running()
        if state["running"]:
            return {"ok": True, "already": True}
        exe = Path(sys.executable).with_name("pythonw.exe")
        if not exe.exists():
            exe = Path(sys.executable)
        try:
            subprocess.Popen(
                [str(exe), str(config.APP_DIR / "daemon.py")],
                cwd=str(config.APP_DIR), close_fds=True,
            )
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def dictionary_status(self) -> dict:
        return metadata.stats()

    def rematch_all(self) -> dict:
        """手动重新匹配所有未识别标题（改了代理或补了词典后可点）。"""
        result = metadata.rematch_all()
        return {k: v for k, v in result.items() if k != "results"}

    def import_alias_file(self) -> dict:
        """可选：手动批量补充 data/alias_import.json 后导入。"""
        return metadata.import_alias_file()

    # ---- Obsidian 知识沉淀 -------------------------------------------------
    def obsidian_status(self) -> dict:
        return obsidian.status()

    def obsidian_sync(self, note: str = "") -> dict:
        """手动同步：把开发流程、自测结果、路线图写进 vault 的独立命名空间。"""
        return obsidian.sync(reason="界面手动同步", kind="manual", note=note or "")

    def obsidian_set_vault(self, path: str) -> dict:
        return obsidian.set_options(vault=path)

    def obsidian_open_folder(self) -> dict:
        return obsidian.open_notes_folder()
