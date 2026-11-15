"""pywebview 的 js_api：前端与本地数据之间的唯一桥梁。"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from . import (
    __version__, autostart, config, covers, db, embedded_player, everything, importer,
    library_tree, metadata, obsidian, potplayer, runtime, scanner,
)


# 文件浏览的排序白名单：用户输入只能选键，绝不直接拼进 SQL
_MEDIA_SORTS = {
    "newest": "f.id DESC",
    "name": "f.filename COLLATE NOCASE ASC",
    "cn": "COALESCE(f.title_cn, f.title_guess, f.filename) COLLATE NOCASE ASC",
    "size": "COALESCE(f.size, 0) DESC, f.id DESC",
    "progress": "COALESCE(p.percent, 0) DESC, f.id DESC",
}


class Api:
    def __init__(self):
        # 必须是私有属性：pywebview 会 dir() 深度反射 js_api，公开持有 Window
        # 会把 .NET WinForms 控件树整个遍历一遍（CoreWebView2 getter 即在此抛
        # E_NOINTERFACE 并刷屏报错），同时严重拖慢启动。下划线开头会被跳过。
        self._window = None
        self._last_scan: dict | None = None
        db.init_db()
        metadata.ensure_builtin_loaded()

    def attach_window(self, window) -> None:
        self._window = window

    def get_app_info(self) -> dict:
        return {
            "version": __version__,
            "app_dir": str(config.APP_DIR),
            "db_path": str(config.DB_PATH),
            "potplayer": potplayer.potplayer_exe(),
            "python": sys.version.split()[0],
            "theme": self._theme(),
        }

    @staticmethod
    def _theme() -> str:
        value = str(db.get_setting("theme", config.DEFAULTS["theme"])).strip().lower()
        return value if value in ("light", "dark") else "light"

    def set_theme(self, theme: str) -> dict:
        """切换黑/白展示风格；只接受两个值，避免非法输入。"""
        wanted = str(theme or "").strip().lower()
        if wanted not in ("light", "dark"):
            return {"ok": False, "error": "主题只支持 light / dark"}
        db.set_setting("theme", wanted)
        return {"ok": True, "theme": wanted}

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

    def get_watched_list(self, limit: int = 500, keyword: str = "") -> list[dict]:
        """看过的列表：按作品名 / 中文名 / 导入原文 / 备注搜索；limit <= 0 表示全部。

        LIKE 的 % / _ / \\ 一律按字面量处理，避免关键词把列表放大成全表。
        """
        try:
            size = int(limit)
        except (TypeError, ValueError):
            size = 500
        kw = str(keyword or "").strip()
        conditions = ["1=1"]
        params: list = []
        if kw:
            escaped = kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            conditions.append(
                "(title LIKE ? ESCAPE '\\' OR COALESCE(title_cn,'') LIKE ? ESCAPE '\\' "
                "OR COALESCE(raw_text,'') LIKE ? ESCAPE '\\' "
                "OR COALESCE(note,'') LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 4)
        sql = "SELECT * FROM watched_list WHERE " + " AND ".join(conditions) + " ORDER BY id DESC"
        if size > 0:
            sql += " LIMIT ?"
            params.append(size)
        return db.rows(sql, tuple(params))

    def update_watched(self, item_id: int, title: str | None = None,
                      title_cn: str | None = None) -> dict:
        """行内改名。None = 不改该字段；中文名传空串表示清空。

        用户手改的中文名按 ``source='user'`` 记住，下次导入同名作品自动命中；
        ``raw_text`` 是导入原文，始终保留不改。
        """
        try:
            rid = int(item_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "记录编号无效"}
        row = db.row("SELECT * FROM watched_list WHERE id = ?", (rid,))
        if not row:
            return {"ok": False, "error": "记录不存在"}
        new_title = row["title"] if title is None else str(title).strip()
        if not new_title:
            return {"ok": False, "error": "作品名不能为空"}
        if title_cn is None:
            new_cn = row["title_cn"]
            match_by = row["match_by"]
        else:
            new_cn = str(title_cn).strip() or None
            match_by = "user" if new_cn and new_cn != new_title else (
                "none" if not new_cn else "native")
        db.execute(
            "UPDATE watched_list SET title = ?, title_cn = ?, match_by = ? WHERE id = ?",
            (new_title, new_cn, match_by, rid),
        )
        learned = False
        if new_cn and new_cn != new_title and (title is not None or title_cn is not None):
            metadata.remember(new_title, new_cn, source="user")
            learned = True
        return {
            "ok": True, "learned": learned,
            "row": db.row("SELECT * FROM watched_list WHERE id = ?", (rid,)),
        }

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

    @staticmethod
    def _media_filters(options: dict | None) -> tuple[str, list]:
        """平铺列表与树状视图共用的筛选条件；用户输入只做参数绑定，绝不拼进 SQL。"""
        opt = options or {}
        keyword = str(opt.get("keyword") or "").strip()
        # LIKE 的 % / _ / \ 是通配符或转义符，用户输入的关键词一律按字面量处理
        escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        try:
            directory_id = int(opt.get("directory_id") or 0)
        except (TypeError, ValueError):
            directory_id = 0
        match_state = str(opt.get("match_state") or "").strip().lower()

        conditions = ["1=1"]
        params: list = []
        if keyword:
            conditions.append(
                "(f.filename LIKE ? ESCAPE '\\' OR COALESCE(f.title_guess,'') LIKE ? ESCAPE '\\' "
                "OR COALESCE(f.title_cn,'') LIKE ? ESCAPE '\\' "
                "OR COALESCE(f.title_raw,'') LIKE ? ESCAPE '\\' "
                "OR COALESCE(f.path,'') LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 5)
        if directory_id > 0:
            conditions.append("f.dir_id = ?")
            params.append(directory_id)
        if match_state == "matched":
            conditions.append("f.title_cn IS NOT NULL AND f.title_cn <> ''")
        elif match_state == "unmatched":
            conditions.append("COALESCE(f.title_cn,'') = ''")
        elif match_state == "progress":
            conditions.append("COALESCE(p.position_ms, 0) > 0")
        return " AND ".join(conditions), params

    @staticmethod
    def _matched_count() -> int:
        return int(db.scalar(
            "SELECT COUNT(*) FROM media_files WHERE title_cn IS NOT NULL AND title_cn <> ''"
        ) or 0)

    @staticmethod
    def _directory_options() -> list[dict]:
        return db.rows("SELECT id, path, file_count AS files FROM directories ORDER BY id")

    def get_media_files_page(self, options: dict | None = None) -> dict:
        """全量文件浏览：返回筛选后的总数与当前页数据；page_size <= 0 表示一次取全部。

        前端「扫描结果 / 媒体库」共用此接口，避免 400 条硬上限截掉扫描到的文件。
        """
        opt = options or {}
        try:
            page = max(1, int(opt.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            size = int(opt.get("page_size", 200))
        except (TypeError, ValueError):
            size = 200
        total_all = int(db.scalar("SELECT COUNT(*) FROM media_files") or 0)
        where, params = self._media_filters(opt)

        total = int(db.scalar(
            "SELECT COUNT(*) FROM media_files f "
            "LEFT JOIN watch_progress p ON p.file_id = f.id WHERE " + where,
            tuple(params),
        ) or 0)
        order = _MEDIA_SORTS.get(
            str(opt.get("sort") or "newest").strip().lower(), _MEDIA_SORTS["newest"]
        )
        sql = (
            "SELECT f.*, COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY " + order
        )
        query_params = list(params)
        if size > 0:
            sql += " LIMIT ? OFFSET ?"
            query_params.extend([size, (page - 1) * size])
        rows = db.rows(sql, tuple(query_params))
        if size > 0:
            pages = max(1, (total + size - 1) // size)
            offset = (page - 1) * size
            first = offset + 1 if offset < total else 0
            last = min(offset + size, total) if first else 0
        else:
            pages, first, last = 1, (1 if total else 0), total
        return {
            "ok": True, "rows": rows, "total": total, "total_all": total_all,
            "matched": self._matched_count(), "page": page, "page_size": size, "pages": pages,
            "from": first, "to": last,
            "directories": self._directory_options(),
        }

    def get_media_tree(self, options: dict | None = None) -> dict:
        """树状视图：按「作品 → 季 → 集」或「目录 → 子目录 → 文件」分组。

        与平铺列表共用同一套搜索 / 目录 / 匹配状态筛选，但**不受每页条数限制**：
        树是对当前筛选结果的全部文件做分组，节点默认折叠，展开才渲染文件行。
        """
        opt = options or {}
        where, params = self._media_filters(opt)
        rows = db.rows(
            "SELECT f.id, f.dir_id, f.path, f.filename, f.size, f.title_raw, "
            "f.title_guess, f.title_cn, f.match_by, f.season_no, f.episode_no, f.year, "
            "f.status, COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC",
            tuple(params),
        )
        directories = self._directory_options()
        tree = library_tree.build_tree(rows, str(opt.get("mode") or "series"), directories)
        return {
            "ok": True, **tree,
            "total_all": int(db.scalar("SELECT COUNT(*) FROM media_files") or 0),
            "matched": self._matched_count(),
            "directories": directories,
        }

    def get_poster_wall(self, options: dict | None = None) -> dict:
        """剧集墙：作品卡片（封面 + 季/集统计 + 续播集）。复用树分组，不另写一套搜索。"""
        opt = options or {}
        where, params = self._media_filters(opt)
        rows = db.rows(
            "SELECT f.id, f.dir_id, f.path, f.filename, f.title_raw, f.title_guess, "
            "f.title_cn, f.match_by, f.season_no, f.episode_no, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC",
            tuple(params),
        )
        tree = library_tree.build_tree(rows, "series", self._directory_options())
        cards = []
        for group in tree.get("groups", []):
            seasons = [c.get("label") for c in group.get("children", [])]
            cards.append({
                "title": group.get("label", ""),
                "file_count": group.get("file_count", 0),
                "watched_count": group.get("watched_count", 0),
                "playing_count": group.get("playing_count", 0),
                "next_file_id": group.get("next_file_id"),
                "season_count": len(seasons),
                "seasons": seasons[:12],
                "sub": group.get("sub", ""),
            })
        keyword = str(opt.get("keyword") or "").strip().lower()
        if keyword:
            cards = [c for c in cards if keyword in c["title"].lower()]
        try:
            max_cards = max(1, min(int(opt.get("limit") or 300), 2000))
        except (TypeError, ValueError):
            max_cards = 300
        total = len(cards)
        cards = cards[:max_cards]
        return {
            "ok": True, "cards": cards, "total": total, "shown": len(cards),
            "covers": covers.cover_map([c["title"] for c in cards]),
            "cover_status": covers.status(),
        }

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
            "everything_es_path", "everything_http_url", "embed_player",
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

    # ---------- Everything 快速检索 ----------
    def everything_capabilities(self) -> dict:
        return everything.capabilities()

    def everything_search(self, keyword: str, limit: int = 200) -> dict:
        return everything.search(keyword, limit)

    # ---------- 动漫封面 ----------
    def cover_status(self) -> dict:
        return covers.status()

    def start_cover_fetch(self, limit: int = 0, force: bool = False) -> dict:
        """用户手动触发的一次性后台任务；不是定时任务，也不自动随启动运行。"""
        return covers.start_job(limit=limit, force=force)

    def cancel_cover_fetch(self) -> dict:
        return covers.cancel_job()

    def cover_data_uris(self, titles: list[str]) -> dict:
        """把已缓存的封面转成 data URI（WebView2 无法直接 file:// 读程序数据目录）。

        逐张限制大小，单张异常跳过；返回 {title: data:image/...;base64,...}。
        """
        import base64
        import mimetypes
        out: dict[str, str] = {}
        for title in (titles or [])[:400]:
            name = covers.local_cover(str(title))
            if not name:
                continue
            path = config.COVER_DIR / name
            try:
                if not path.is_file() or path.stat().st_size > 3 * 1024 * 1024:
                    continue
                mime = mimetypes.guess_type(name)[0] or "image/jpeg"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                out[str(title)] = f"data:{mime};base64,{encoded}"
            except Exception:
                continue
        return out

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
            if not self._window:
                return {"ok": False, "error": "窗口尚未就绪，请直接粘贴路径"}
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG)
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

    @staticmethod
    def _play_target(file_id: int, from_start: bool) -> tuple[str, int, str]:
        """解析播放目标：(路径, 断点毫秒, 展示标题)；不合法直接抛 ValueError。"""
        record = db.row("SELECT * FROM media_files WHERE id = ?", (int(file_id),))
        if not record:
            raise ValueError("找不到该文件记录")
        path = record["path"]
        if not Path(path).exists():
            raise ValueError("文件不在扫描库中或已被移动，请先扫描所在目录")
        position = 0
        if not from_start:
            progress = db.row("SELECT position_ms, completed FROM watch_progress WHERE file_id = ?", (record["id"],))
            if progress and not progress["completed"]:
                position = progress["position_ms"] or 0
        title = record["title_cn"] or record["title_guess"] or record["filename"]
        return path, position, title

    def play_file(self, file_id: int, from_start: bool = False) -> dict:
        try:
            path, position, _title = self._play_target(file_id, from_start)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = potplayer.play(path, position)
        if result.get("ok"):
            result["position_ms"] = position
        return result

    def play_external(self, path: str) -> dict:
        """播放一个不在扫描库里的本地文件（Everything 全盘检索结果用）。"""
        p = Path(str(path or "").strip())
        if not p.is_file():
            return {"ok": False, "error": "文件不存在或已被移动"}
        if p.suffix.lower() not in config.VIDEO_EXTS:
            return {"ok": False, "error": "不是受支持的视频文件"}
        return potplayer.play(str(p), 0)

    # ---------- 软件内嵌播放（失败自动降级为外部播放） ----------
    def embed_status(self) -> dict:
        state = embedded_player.status()
        state["enabled"] = bool(db.get_int("embed_player", config.DEFAULTS["embed_player"]))
        return state

    def set_embed_player(self, enabled: bool) -> dict:
        wanted = 1 if enabled in (True, 1, "1", "true", "on") else 0
        db.set_setting("embed_player", wanted)
        return {"ok": True, "enabled": bool(wanted)}

    def play_embedded(self, file_id: int, from_start: bool = False, rect: dict | None = None) -> dict:
        try:
            path, position, title = self._play_target(file_id, from_start)
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        result = embedded_player.play(path, position, rect)
        if result.get("ok"):
            result["title"] = title
        return result

    def play_embedded_path(self, path: str, rect: dict | None = None) -> dict:
        """全盘快搜结果（不在扫描库）也走软件内播放。"""
        p = Path(str(path or "").strip())
        if not p.is_file():
            return {"ok": False, "error": "文件不存在或已被移动"}
        if p.suffix.lower() not in config.VIDEO_EXTS:
            return {"ok": False, "error": "不是受支持的视频文件"}
        result = embedded_player.play(str(p), 0, rect)
        if result.get("ok"):
            result["title"] = p.name
        return result

    def embed_sync(self, rect: dict | None = None) -> dict:
        return embedded_player.sync(embedded_player.rect_from_css(rect))

    def embed_stop(self) -> dict:
        return embedded_player.detach()

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
        try:
            subprocess.Popen(
                runtime.daemon_command(),
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
