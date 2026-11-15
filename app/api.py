"""pywebview 的 js_api：前端与本地数据之间的唯一桥梁。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

import psutil

from . import (
    __version__, autostart, blacklist, config, covers, db, embedded_player, everything,
    file_ops, importer, library_tree, metadata, obsidian, potplayer, runtime, scanner,
    textmatch, theme_native, watchlist,
)


# 单次人工删除的文件数上限：一部剧通常十几到几十集，给足余量又防误传整库
_MAX_DELETE_FILES = 300

# 用户手工修改的作品名：存在 settings 里的一份 JSON 映射（分组 id -> 名称）。
# 只影响展示标签，不改 media_files 的任何字段；不引入新表、不需要迁移。
_WORK_LABELS_KEY = "work_labels_json"

# 剧集墙「查看分集」的搜索词清洗：树分组偶尔把 [1.36G] 这类文件大小当成
# 作品名，此时退回文件名方括号里的真实中文片名（例：[爱的躯壳].La.Belle...）。
_CARD_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_CARD_BRACKET_RE = re.compile(r"[\[【《（(]([^\[\]【】》（）()]{2,24})[\]】》）)]")
_CARD_NOISE_WORDS = (
    "字幕", "中字", "双语", "国配", "粤配", "无修", "高清", "合集",
    "下载", "预览", "未删减", "完整版", "先行", "特典",
)

# 封面 data URI 缓存：key 是缓存文件名，值为 (mtime_ns, size, data_uri)。
# 切视图时同一批封面不必重复读盘 + base64 编码，这是「切到媒体库卡顿」的主因之一；
# 文件被人工封面覆盖后 mtime 变化会自动失效，不需要手动清。
_COVER_URI_CACHE: dict[str, tuple[int, int, str]] = {}

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
        # 仅记录「本进程已成功拉起过守护」，避免窗口就绪 + 前端 init 重复调用时
        # 在进程刚 spawn、psutil 还看不到的极短窗口里拉起第二个托盘图标。
        # 绝不在这里直接启动守护：Api() 会被单元测试大量实例化。
        self._daemon_auto_started = False
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
        # 网页顶栏由 CSS 变量控制，原生标题栏得靠 DWM；失败也不影响主题切换
        try:
            theme_native.apply(wanted)
        except Exception:
            pass
        return {"ok": True, "theme": wanted}

    def sync_native_theme(self) -> dict:
        """窗口就绪后同步一次原生标题栏（重启后深色不会又变成一条白）。"""
        try:
            return theme_native.apply(self._theme())
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_stats(self) -> dict:
        ban_sql, ban_params = blacklist.sql_clause("f")
        files_where = f" WHERE {ban_sql}" if ban_sql else ""
        prog_where = f" AND {ban_sql}" if ban_sql else ""
        return db.row(
            "SELECT "
            f"(SELECT COUNT(*) FROM media_files f{files_where}) AS files, "
            "(SELECT COUNT(*) FROM watched_list) AS watched, "
            "(SELECT COUNT(*) FROM watch_sessions) AS sessions, "
            "(SELECT COUNT(*) FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
            f"WHERE p.completed = 0 AND p.position_ms > 0{prog_where}) AS in_progress, "
            "(SELECT COUNT(*) FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
            f"WHERE p.completed = 1{prog_where}) AS completed",
            tuple(ban_params) * 3,
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
        rows = db.rows(
            "SELECT p.*, f.filename, f.path, f.title_guess, f.deleted_at, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "f.season_no, f.episode_no "
            "FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
            "WHERE p.completed = 0 AND p.position_ms > 0 AND p.dismissed_at IS NULL "
            "ORDER BY p.last_played_at DESC LIMIT ?",
            (int(limit),),
        )
        return blacklist.filter_rows(rows, keys=("display_title", "filename", "title_guess"))

    def get_completed(self, limit: int = 50) -> list[dict]:
        """已看完的条目，供「继续观看」页做纠错（标记未看 / 重看）。"""
        rows = db.rows(
            "SELECT p.*, f.filename, f.path, f.title_guess, f.deleted_at, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "f.season_no, f.episode_no "
            "FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
            "WHERE p.completed = 1 ORDER BY p.last_played_at DESC LIMIT ?",
            (int(limit),),
        )
        return blacklist.filter_rows(rows, keys=("display_title", "filename", "title_guess"))

    def dismiss_continue_watching(self, file_id: int, restore: bool = False) -> dict:
        """「继续观看」人工移除 / 恢复：**软删除，只 UPDATE 标记**。

        进度、断点、观看会话、本地文件一律不动；再次播放该文件时
        ``_play_target`` 会自动清标记，让它重新回到继续观看列表。
        """
        try:
            fid = int(file_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "无效的文件 ID"}
        if not db.scalar("SELECT 1 FROM watch_progress WHERE file_id = ?", (fid,)):
            return {"ok": False, "error": "找不到该条播放进度"}
        stamp = None if restore in (True, 1, "1", "true", "on") else db.now()
        db.execute(
            "UPDATE watch_progress SET dismissed_at = ?, updated_at = ? WHERE file_id = ?",
            (stamp, db.now(), fid),
        )
        return {"ok": True, "file_id": fid, "dismissed": stamp is not None,
                "kept_progress": True, "kept_sessions": True}

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
        """历史记录按「每个作品只显示最新一次会话」去重。

        只是展示层收拢：``watch_sessions`` 里所有旧会话**原样保留**，
        人工删除入口仍只作用于用户点中的那一条。分组键优先用作品标题
        （中文名 > 推断名），同一部剧的不同集因此只留最新一条；没有标题
        的（如外部播放）退回 ``file_id`` / ``-id``，避免彼此被误合并。
        """
        return blacklist.filter_rows(
            db.rows(
                "SELECT s.*, f.filename, "
                "COALESCE(f.title_cn, f.title_guess) AS display_title, "
                "f.season_no, f.episode_no, f.path "
                "FROM watch_sessions s LEFT JOIN media_files f ON f.id = s.file_id "
                "WHERE s.id IN ("
                "  SELECT MAX(t.id) FROM ("
                "    SELECT s2.id AS id, COALESCE("
                "      NULLIF(TRIM(COALESCE(f2.title_cn, f2.title_guess)), ''),"
                "      'file::' || COALESCE(CAST(s2.file_id AS TEXT), CAST(-s2.id AS TEXT))"
                "    ) AS work_key"
                "    FROM watch_sessions s2 LEFT JOIN media_files f2 ON f2.id = s2.file_id"
                "  ) t GROUP BY t.work_key"
                ") ORDER BY s.started_at DESC LIMIT ?",
                (int(limit),),
            ),
            keys=("display_title", "filename"),
        )

    def delete_history_session(self, session_id) -> dict:
        """人工删除单条观看会话；只删这一行，绝不触碰文件、进度或看过的列表。

        历史记录没有「自动清理」：本方法是 watch_sessions 唯一的删除入口，
        只能由用户在历史页点「删除」并二次确认后调用。
        """
        try:
            sid = int(session_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "记录编号无效", "deleted": 0}
        row = db.row("SELECT id FROM watch_sessions WHERE id = ?", (sid,))
        if not row:
            return {"ok": False, "error": "记录不存在或已删除", "deleted": 0}
        db.execute("DELETE FROM watch_sessions WHERE id = ?", (sid,))
        return {"ok": True, "deleted": 1, "kept_progress": True, "kept_files": True}

    def get_watched_list(self, limit: int = 500, keyword: str = "") -> list[dict]:
        """看过的列表：按作品名 / 中文名 / 导入原文 / 备注搜索；limit <= 0 表示全部。

        LIKE 的 % / _ / \\ 一律按字面量处理，避免关键词把列表放大成全表。
        黑名单在 Python 侧过滤（这张表只有几百行）：命中名称的条目不展示，
        但**不删行**——关掉开关立刻全部回来。
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
        rows = blacklist.filter_rows(
            db.rows(sql, tuple(params)), keys=("title", "title_cn", "raw_text")
        )
        return rows[:size] if size > 0 else rows

    # ---- 看过的列表 → 本地目录：只读匹配 + 资源管理器跳转 ------------------
    @staticmethod
    def _existing_folder(folder: str, roots: list[str]) -> str:
        """发布目录若已被移走/改名，退到最近一层仍存在的上级作品目录。

        目录本身恰好就是扫描根（文件直接放在根里）是合法的，仍原样返回；
        **向上回退时不会退到扫描根**，也不会跨出扫描根，避免打开整个大盘。
        最多向上找 2 层，不存在的分支一律返回空串（按钮就不显示）。
        """
        safe, _why = file_ops.is_safe_directory(folder, roots)
        if safe:
            return folder
        try:
            p = Path(folder)
            root_paths = [Path(r).resolve() for r in roots if str(r or "").strip()]
            parent = p.parent
        except (OSError, ValueError):
            return ""
        for _ in range(2):
            if parent == p:
                return ""
            try:
                resolved_parent = parent.resolve()
            except OSError:
                return ""
            # 比较时才 resolve（Windows 可能给短名），返回给调用方的始终是原始长路径
            if any(resolved_parent == root for root in root_paths):
                return ""
            if not any(resolved_parent.is_relative_to(root) for root in root_paths):
                return ""
            safe, _why = file_ops.is_safe_directory(str(parent), roots)
            if safe:
                return str(parent)
            p, parent = parent, parent.parent
        return ""

    @staticmethod
    def _watched_folder_for(row: dict, media: list[dict], roots: list[str]) -> str:
        """这部作品在本地最可能的发布目录；匹配不到或目录不安全时返回空串。

        复用追剧列表同一套「关键词优先级首个命中即停」匹配，避免另写一套搜索。
        一部剧偶尔散在多个目录，取命中文件最多的那个。
        """
        # dict.fromkeys 保序去重：从播放记录同步的条目 title_cn 会回退成 title，
        # 两者相同；不去重就会对每一行白跑一整轮全库匹配。
        titles = dict.fromkeys((str(row.get("title") or "").strip(),
                                str(row.get("title_cn") or "").strip()))
        for title in titles:
            if not title:
                continue
            _count, files, _keyword = watchlist.match_files(title, media)
            if not files:
                continue
            counts: dict[str, int] = {}
            for f in files:
                folder = os.path.dirname(str(f.get("path") or ""))
                if folder:
                    counts[folder] = counts.get(folder, 0) + 1
            for folder, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower())):
                existing = Api._existing_folder(folder, roots)
                if existing:
                    return existing
        return ""

    @staticmethod
    def _scan_roots() -> list[str]:
        return [str(r["path"]) for r in db.rows("SELECT path FROM directories")]

    def watched_folders(self) -> dict:
        """看过的列表每行 -> 匹配到的本地目录（未匹配的行不出现在字典里）。

        只用来决定「打开文件夹」按钮是否出现；真正的打开动作仍由
        ``open_watched_folder`` 按 id 重新计算并二次校验，**前端不传路径**。
        """
        rows = db.rows("SELECT id, title, title_cn FROM watched_list")
        media = watchlist.media_index()          # 整批只建一次索引，不是每行一次
        roots = self._scan_roots()
        folders: dict[int, str] = {}
        # 「从播放记录同步」按集写入，一整部剧可能有几十条同名/同中文名记录。
        # 一次调用内按 (title, title_cn) 复用匹配结果，避免 N 条重复扫全库；
        # 只缓存在本次调用里，数据变更后下次调用自然重算，不会有陈旧按钮。
        by_title: dict[tuple[str, str], str] = {}
        for row in rows:
            key = (str(row.get("title") or "").strip(),
                   str(row.get("title_cn") or "").strip())
            if key not in by_title:
                by_title[key] = self._watched_folder_for(row, media, roots)
            folder = by_title[key]
            if folder:
                folders[int(row["id"])] = folder
        return {"ok": True, "folders": folders}

    def open_watched_folder(self, item_id: int) -> dict:
        """用文件资源管理器打开该作品所在的本地目录；只读打开，不改任何文件。"""
        try:
            rid = int(item_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "条目编号无效"}
        row = db.row("SELECT id, title, title_cn FROM watched_list WHERE id = ?", (rid,))
        if not row:
            return {"ok": False, "error": "这条记录已不存在"}
        roots = self._scan_roots()
        folder = self._watched_folder_for(row, watchlist.media_index(), roots)
        if not folder:
            return {"ok": False, "error": "没有匹配到扫描目录里的对应作品，无法跳转"}
        safe, why = file_ops.is_safe_directory(folder, roots)
        if not safe:
            return {"ok": False, "error": why or "目录校验未通过"}
        if not hasattr(os, "startfile"):
            return {"ok": False, "error": "当前系统不支持打开资源管理器"}
        try:
            os.startfile(folder)           # 不是 shell 命令，无法被拼接注入
            return {"ok": True, "path": folder}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

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

    def set_watched_season(self, item_id: int, season_no=None) -> dict:
        """行内改「第几季」。只动 season_no，不改作品名/中文名/原文/集数。

        留空、0、— 表示清空（NULL）；允许「2」「S2」「第2季」这类输入，抽数字。
        改名走 ``update_watched``，两个入口互不干扰。
        """
        try:
            rid = int(item_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "记录编号无效"}
        if not db.row("SELECT id FROM watched_list WHERE id = ?", (rid,)):
            return {"ok": False, "error": "记录不存在"}
        raw = str(season_no if season_no is not None else "").strip()
        if not raw or raw in ("0", "—", "-", "无"):
            value = None
        else:
            # 负数/带符号数字不是合法季数，别让 \d+ 把 "-3" 削成 3
            if re.search(r"[-+]", raw):
                return {"ok": False, "error": "季数请填正整数，留空表示清空"}
            m = re.search(r"\d+", raw)
            if not m:
                return {"ok": False, "error": "季数请填数字，留空表示清空"}
            value = int(m.group())
            if not 1 <= value <= 999:
                return {"ok": False, "error": "季数需在 1–999 之间"}
        db.execute("UPDATE watched_list SET season_no = ? WHERE id = ?", (value, rid))
        return {
            "ok": True,
            "row": db.row("SELECT * FROM watched_list WHERE id = ?", (rid,)),
        }

    def delete_watched(self, item_id: int) -> dict:
        db.execute("DELETE FROM watched_list WHERE id = ?", (int(item_id),))
        return {"ok": True}

    def clean_watched_list(self, dry_run: bool = False) -> dict:
        """手动清洗旧版解析器留下的脏标题；dry_run=True 只预览不写库。"""
        flag = dry_run in (True, 1, "1", "true", "on")
        return importer.clean_watched_rows(dry_run=flag)

    def sync_watched_from_history(self) -> dict:
        """手动把「已看完」的播放记录回补进看过的列表；幂等，非定时任务。"""
        return importer.sync_watched_from_progress()

    def get_media_files(self, keyword: str = "", limit: int = 300) -> list[dict]:
        like = f"%{keyword.strip()}%"
        ban_sql, ban_params = blacklist.sql_clause("f")
        sql = (
            "SELECT f.*, COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE (f.filename LIKE ? OR COALESCE(f.title_guess,'') LIKE ? "
            "OR COALESCE(f.title_cn,'') LIKE ?)"
        )
        if ban_sql:
            sql += " AND " + ban_sql
        sql += " ORDER BY f.id DESC LIMIT ?"
        return db.rows(sql, (like, like, like, *ban_params, int(limit)))

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
        # 视频黑名单：媒体库 / 剧集墙 / 剧集展示 / 扫描结果共用这一处筛选，
        # 因此开关一关就整体恢复，不需要每个视图各写一遍。
        ban_sql, ban_params = blacklist.sql_clause("f")
        if ban_sql:
            conditions.append(ban_sql)
            params.extend(ban_params)
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
        ban_sql, ban_params = blacklist.sql_clause("f")
        total_all = int(db.scalar(
            "SELECT COUNT(*) FROM media_files f" + (f" WHERE {ban_sql}" if ban_sql else ""),
            tuple(ban_params),
        ) or 0)
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
            "f.status, f.deleted_at, COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC",
            tuple(params),
        )
        directories = self._directory_options()
        tree = library_tree.build_tree(rows, str(opt.get("mode") or "series"),
                                       directories, self._work_labels())
        return {
            "ok": True, **tree,
            "total_all": int(db.scalar("SELECT COUNT(*) FROM media_files") or 0),
            "matched": self._matched_count(),
            "directories": directories,
        }

    # ---- 作品改名：只改展示名，绝不碰媒体文件 / 播放记录 -------------------
    @staticmethod
    def _work_labels() -> dict:
        """读用户手改的作品名映射；内容损坏时静默降级为空，不影响浏览。"""
        try:
            data = json.loads(db.get_setting(_WORK_LABELS_KEY, "{}") or "{}")
        except (TypeError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): str(v).strip()[:60] for k, v in data.items()
                if str(k).strip() and str(v).strip()}

    def rename_work(self, group_id: str, new_label: str, reset: bool = False) -> dict:
        """给一个作品分组改名（或清空恢复自动名称）。

        ``group_id`` 必须是树里返回的 ``series::...`` 节点 id；改名只写一份
        settings 映射，不 UPDATE media_files，也不影响封面缓存与搜索词兜底。
        """
        gid = str(group_id or "").strip()
        if not gid.startswith("series::"):
            return {"ok": False, "error": "只能修改作品分组的名称"}
        label = str(new_label or "").strip()[:60]
        labels = self._work_labels()
        if reset or not label:
            labels.pop(gid, None)
        elif not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", label):
            return {"ok": False, "error": "名称里至少要有一个文字或数字"}
        else:
            labels[gid] = label
        db.set_setting(_WORK_LABELS_KEY, json.dumps(labels, ensure_ascii=False))
        return {"ok": True, "group_id": gid, "label": label, "labels": labels}

    # ---- 人工清理：历史记录 / 已看完剧集的本地文件 -------------------------
    @staticmethod
    def _normalize_ids(file_ids) -> list[int]:
        if file_ids is None:
            return []
        if isinstance(file_ids, (int, float, str)):
            file_ids = [file_ids]
        out: list[int] = []
        try:
            iterator = iter(file_ids)
        except TypeError:
            return out
        for value in iterator:
            try:
                fid = int(value)
            except (TypeError, ValueError):
                continue
            if fid > 0 and fid not in out:
                out.append(fid)
        return out

    @staticmethod
    def _full_series_tree() -> dict:
        """不带筛选地重建「作品 → 季 → 集」分组，用于后端独立校验整部剧是否看完。"""
        rows = db.rows(
            "SELECT f.id, f.dir_id, f.path, f.filename, f.size, f.title_raw, "
            "f.title_guess, f.title_cn, f.match_by, f.season_no, f.episode_no, f.year, "
            "f.status, f.deleted_at, COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC"
        )
        return library_tree.build_tree(rows, "series", Api._directory_options(),
                                      Api._work_labels())

    @classmethod
    def _find_series_group(cls, group_id: str) -> dict | None:
        for group in cls._full_series_tree().get("groups", []):
            if group.get("id") == group_id:
                return group
        return None

    @staticmethod
    def _delete_candidate(fid: int) -> dict:
        """列出一个待删文件并给出能否删除的理由；只读，不改任何东西。"""
        row = db.row(
            "SELECT f.id, f.filename, f.path, f.size, f.status, f.deleted_at, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "COALESCE(p.completed, 0) AS completed "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE f.id = ?",
            (fid,),
        )
        if not row:
            return {"id": fid, "filename": "", "path": "", "size": 0,
                    "display_title": "", "deletable": False, "reason": "记录不存在"}
        path = str(row["path"] or "")
        safe, why = file_ops.is_safe_video_path(path)
        exists = False
        if safe:
            try:
                exists = Path(path).is_file()
            except OSError:
                exists = False
        if row["deleted_at"]:
            reason = "本地文件已移入回收站"
        elif str(row["status"] or "") == "external":
            reason = "外部打开记录没有本地文件路径"
        elif int(row["completed"] or 0) != 1:
            reason = "这一集还没看完"
        elif not safe:
            reason = why
        elif not exists:
            reason = "文件不存在或已被移动"
        else:
            reason = ""
        return {
            "id": int(row["id"]), "filename": row["filename"], "path": path,
            "size": int(row["size"] or 0), "display_title": row["display_title"],
            "deletable": not reason, "reason": reason,
        }

    def get_delete_candidates(self, file_ids=None, group_id: str = "") -> dict:
        """删除前的清单预览：给界面确认框列出完整路径，并标出哪些能删。

        group_id 非空时按整部作品取文件，并强制要求「全部集都已看完」。
        """
        gid = str(group_id or "").strip()
        title = ""
        if gid:
            group = self._find_series_group(gid)
            if not group:
                return {"ok": False, "error": "找不到该作品分组，请刷新后重试", "files": []}
            if not group.get("all_done"):
                return {"ok": False, "error": "这部作品还有未看完的集数，不能删除本地文件", "files": []}
            ids = list(group.get("file_ids") or [])
            title = str(group.get("label") or "")
        else:
            ids = self._normalize_ids(file_ids)
        if not ids:
            return {"ok": False, "error": "没有可删除的本地文件", "files": []}
        if len(ids) > _MAX_DELETE_FILES:
            return {"ok": False, "error": f"一次最多处理 {_MAX_DELETE_FILES} 个文件，请分批操作",
                    "files": []}
        files = [self._delete_candidate(fid) for fid in ids]
        return {
            "ok": True, "group_id": gid, "title": title, "files": files,
            "deletable_count": sum(1 for f in files if f["deletable"]),
            "keeps_records": True,
        }

    def delete_local_files(self, file_ids=None, group_id: str = "") -> dict:
        """人工把已看完的本地视频文件移入回收站；**数据库零 DELETE**。

        只有界面二次确认后才会调用。成功的文件只 ``UPDATE deleted_at``，
        因此 watch_progress / watch_sessions / watched_list / media_files 全部保留，
        观看记录不会因为文件被删而自动消失。
        """
        ids = self._normalize_ids(file_ids)
        if not ids:
            return {"ok": False, "error": "没有选中文件", "deleted": 0,
                    "skipped": 0, "failed": 0, "errors": [], "kept_records": True}
        if len(ids) > _MAX_DELETE_FILES:
            return {"ok": False, "error": f"一次最多处理 {_MAX_DELETE_FILES} 个文件，请分批操作",
                    "deleted": 0, "skipped": 0, "failed": 0, "errors": [], "kept_records": True}
        gid = str(group_id or "").strip()
        if gid:
            group = self._find_series_group(gid)
            if not group:
                return {"ok": False, "error": "找不到该作品分组，请刷新后重试", "deleted": 0,
                        "skipped": 0, "failed": 0, "errors": [], "kept_records": True}
            if not group.get("all_done"):
                return {"ok": False, "error": "这部作品还有未看完的集数，已全部拒绝", "deleted": 0,
                        "skipped": 0, "failed": 0, "errors": [], "kept_records": True}
            allowed = set(group.get("file_ids") or [])
            if not set(ids).issubset(allowed):
                return {"ok": False, "error": "包含不属于该作品的文件，已全部拒绝", "deleted": 0,
                        "skipped": 0, "failed": 0, "errors": [], "kept_records": True}

        deleted = skipped = failed = 0
        errors: list[str] = []
        removed: list[int] = []
        for fid in ids:
            cand = self._delete_candidate(fid)
            if not cand["deletable"]:
                skipped += 1
                continue
            result = file_ops.trash_file(cand["path"])
            if not result.get("ok"):
                failed += 1
                if len(errors) < 8:
                    errors.append(f"{cand['filename']}：{result.get('error') or '删除失败'}")
                continue
            # 只打标记，绝不 DELETE：外键是 ON DELETE CASCADE，删行会连带清掉观看记录
            db.execute(
                "UPDATE media_files SET deleted_at = ?, updated_at = ? WHERE id = ?",
                (db.now(), db.now(), fid),
            )
            deleted += 1
            removed.append(fid)
        return {
            "ok": deleted > 0 and failed == 0, "deleted": deleted,
            "skipped": skipped, "failed": failed, "errors": errors,
            "file_ids": removed, "kept_records": True,
        }

    @staticmethod
    def _group_cover_titles(group: dict) -> list[str]:
        """取分组内文件已匹配的中文名 / 解析原名，作为封面标题回退候选（去重、限量）。"""
        seen, out = set(), []

        def walk(node: dict) -> None:
            for f in node.get("files") or []:
                for key in ("title_cn", "title_guess"):
                    value = str((f or {}).get(key) or "").strip()
                    if not value or value in seen:
                        continue
                    seen.add(value)
                    out.append(value)
                    if len(out) >= 12:
                        return
            for child in node.get("children") or []:
                walk(child)
                if len(out) >= 12:
                    return

        walk(group)
        return out

    @staticmethod
    def _card_clean_keyword(raw: str) -> str:
        """候选串 -> 可搜索的中文作品名；不像标题就返回空串。"""
        text = str(raw or "").strip()
        if not text or any(word in text for word in _CARD_NOISE_WORDS):
            return ""
        variants = textmatch.query_variants(text, 1)
        value = variants[0] if variants else text
        return value if _CARD_CJK_RE.search(value) else ""

    @staticmethod
    def _group_bracket_titles(group: dict) -> list[str]:
        """脏分组名时，从组内文件名 / 原始标题的括号里找真正的中文片名。"""
        seen, out = set(), []

        def walk(node: dict) -> None:
            for f in node.get("files") or []:
                for key in ("filename", "title_raw"):
                    raw = str((f or {}).get(key) or "")
                    for hit in _CARD_BRACKET_RE.findall(raw):
                        hit = hit.strip()
                        if hit and hit not in seen:
                            seen.add(hit)
                            out.append(hit)
                            if len(out) >= 12:
                                return
            for child in node.get("children") or []:
                walk(child)
                if len(out) >= 12:
                    return

        walk(group)
        return out

    @staticmethod
    def _card_search_keyword(group: dict) -> str:
        """「查看分集」带去媒体库的搜索词。

        树分组偶尔会把 ``1.36G`` 这类文件大小当成分组名，直接用卡片标题
        会把媒体库搜成一片空白。依次退回：组内已匹配的中文名 ->
        文件名括号里的中文片名 -> 原始分组名。
        """
        label = str(group.get("label") or "").strip()
        variants = textmatch.query_variants(label, 1)
        first = variants[0] if variants else label
        for source in ([label], Api._group_cover_titles(group),
                       Api._group_bracket_titles(group)):
            for candidate in source:
                cleaned = Api._card_clean_keyword(candidate)
                if cleaned:
                    return cleaned
        return first or label

    def get_poster_wall(self, options: dict | None = None) -> dict:
        """剧集墙：作品卡片（封面 + 季/集统计 + 续播集）。复用树分组，不另写一套搜索。"""
        opt = options or {}
        where, params = self._media_filters(opt)
        rows = db.rows(
            "SELECT f.id, f.dir_id, f.path, f.filename, f.title_raw, f.title_guess, "
            "f.title_cn, f.match_by, f.season_no, f.episode_no, f.deleted_at, "
            "COALESCE(f.title_cn, f.title_guess) AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC",
            tuple(params),
        )
        tree = library_tree.build_tree(rows, "series", self._directory_options(),
                                      self._work_labels())
        cover_entries = covers.subject_covers()
        cards = []
        for group in tree.get("groups", []):
            seasons = [c.get("label") for c in group.get("children", [])]
            cards.append({
                "title": group.get("label", ""),
                "search_keyword": self._card_search_keyword(group),
                "file_count": group.get("file_count", 0),
                "watched_count": group.get("watched_count", 0),
                "playing_count": group.get("playing_count", 0),
                "next_file_id": group.get("next_file_id"),
                "season_count": len(seasons),
                "seasons": seasons[:12],
                "sub": group.get("sub", ""),
                "group_id": group.get("id", ""),
                "all_done": bool(group.get("all_done")),
                "removable_count": int(group.get("removable_count") or 0),
                "_cover_titles": self._group_cover_titles(group),
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
        # 卡片标题（树分组 label）与抓取封面时用的标题可能不同：
        # 先用卡片标题，再退到该分组内文件的中文名 / 解析原名，命中就复用同一张封面。
        resolved = {}
        for card in cards:
            raw_label = card["title"]
            # 脏分组名（如 1.36G）展示也换成推断出的中文片名；封面仍先用
            # 原分组名匹配，避免已经缓存好的封面突然失效。
            if (not _CARD_CJK_RE.search(raw_label)
                    and _CARD_CJK_RE.search(card["search_keyword"])):
                card["title"] = card["search_keyword"]
            for candidate in [raw_label, card["title"], *card.pop("_cover_titles")]:
                name = covers.resolve_cover(candidate, cover_entries)
                if name:
                    resolved[card["title"]] = name
                    break
        return {
            "ok": True, "cards": cards, "total": total, "shown": len(cards),
            "covers": resolved,
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
            "everything_es_path", "everything_http_url", "everything_app_path",
            "everything_http_user", "everything_http_pass",
            "embed_player", "daemon_auto_start",
            # 视频黑名单：名称包含任一关键词的视频默认隐藏，开关关闭后恢复全部
            "blacklist_enabled", "video_blacklist",
            # 播放快捷键绑定与倍速步进：PotPlayer 各版本键位不同，允许在设置里改
            "pot_key_play_pause", "pot_key_stop", "pot_key_faster", "pot_key_slower",
            "pot_key_reset_speed", "pot_key_fullscreen", "pot_speed_step",
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
        if "daemon_auto_start" in (data or {}):
            self._apply_daemon_auto_start()
        return self.get_settings()

    # ---------- Everything 快速检索 ----------
    def everything_capabilities(self) -> dict:
        return everything.capabilities()

    def everything_search(self, keyword: str, limit: int = 200) -> dict:
        result = everything.search(keyword, limit)
        rows = result.get("rows") if isinstance(result, dict) else None
        if rows:
            result["rows"] = blacklist.filter_rows(
                rows, keys=("name", "filename", "title", "display_title", "path")
            )
            result["hidden"] = len(rows) - len(result["rows"])
        return result

    def everything_open(self) -> dict:
        """打开本机 Everything 主程序（快捷方式）；路径由后端固定配置，前端不传参。"""
        return everything.open_app()

    # ---------- 追剧列表 ----------
    def watchlist_preview(self, text: str) -> dict:
        """导入前预览：按「已下载：/ 未下载：」分段统计，不写库。"""
        return watchlist.preview(text or "")

    def watchlist_import(self, text: str, replace: bool = False) -> dict:
        """导入清单；默认幂等追加，replace=True 由界面二次确认后清空重建。"""
        flag = replace in (True, 1, "1", "true", "on")
        return watchlist.import_text(text or "", replace=flag)

    def get_watchlist(self, keyword: str = "") -> dict:
        return watchlist.items(keyword or "")

    def watchlist_set_downloaded(self, item_id, downloaded: bool) -> dict:
        """未下载 ↔ 已下载；只改标记，不复制、不删除、不改顺序。"""
        return watchlist.set_downloaded(item_id, downloaded)

    def watchlist_move(self, item_id, delta: int) -> dict:
        """上移 / 下移一条，与同栏相邻条目交换顺序。"""
        return watchlist.move(item_id, int(delta))

    def watchlist_top(self, item_id) -> dict:
        """置顶：一下移到同栏最上面，不重排其它行、不跨栏、不改状态。"""
        return watchlist.move_top(item_id)

    def watchlist_add(self, title: str, downloaded: bool = False) -> dict:
        return watchlist.add(title, downloaded)

    def watchlist_rename(self, item_id, title: str) -> dict:
        return watchlist.rename(item_id, title)

    def watchlist_remove(self, item_id) -> dict:
        return watchlist.remove(item_id)

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
        entries = covers.subject_covers()      # 一次查库，避免每张卡片一次 SQL
        for title in (titles or [])[:400]:
            name = covers.resolve_cover(str(title), entries)
            if not name:
                continue
            path = config.COVER_DIR / name
            try:
                stat = path.stat()
                if not path.is_file() or stat.st_size > 3 * 1024 * 1024:
                    continue
                stamp = (int(stat.st_mtime_ns), int(stat.st_size))
                cached = _COVER_URI_CACHE.get(name)
                if cached and cached[0] == stamp[0] and cached[1] == stamp[1]:
                    out[str(title)] = cached[2]
                    continue
                mime = mimetypes.guess_type(name)[0] or "image/jpeg"
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                uri = f"data:{mime};base64,{encoded}"
                _COVER_URI_CACHE[name] = (stamp[0], stamp[1], uri)
                out[str(title)] = uri
            except Exception:
                continue
        return out

    def set_cover(self, title: str, image: str = "", src_type: str = "datauri") -> dict:
        """人工设定封面：支持粘贴的 base64 data URI / 本地图片路径 / 图片 URL。

        前端只传字符串，路径与 URL 都由后端读取/下载并校验魔数与大小，
        不信任任何前端内容；成功后写 subjects(source='manual')，自动抓取永不覆盖。
        """
        import base64
        text = str(title or "").strip()
        if not text:
            return {"ok": False, "error": "作品名为空"}
        payload = str(image or "").strip()
        kind = str(src_type or "datauri").lower()
        origin = f"manual:{kind}"
        try:
            if kind == "datauri":
                if not payload.startswith("data:image/") or ";base64," not in payload:
                    return {"ok": False, "error": "不是图片 data URI"}
                raw = payload.split(";base64,", 1)[1]
                data = base64.b64decode(raw, validate=True)
            elif kind == "path":
                p = Path(payload)
                exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
                if not p.is_absolute() or p.suffix.lower() not in exts or not p.is_file():
                    return {"ok": False, "error": "本地图片路径无效"}
                if p.stat().st_size > 3 * 1024 * 1024:
                    return {"ok": False, "error": "图片超过 3 MB"}
                data = p.read_bytes()
                origin = f"manual:path:{p.name}"[:200]
            elif kind == "url":
                if not payload.startswith(("http://", "https://")):
                    return {"ok": False, "error": "图片地址必须是 http(s)"}
                resp = covers._get(payload, timeout=covers.IMAGE_TIMEOUT, retries=1)
                if resp.status_code != 200:
                    return {"ok": False, "error": f"下载失败 HTTP {resp.status_code}"}
                data = resp.content
                origin = f"manual:url:{payload}"[:200]
            else:
                return {"ok": False, "error": "不支持的图片来源"}
        except Exception as exc:
            return {"ok": False, "error": f"读取图片失败：{type(exc).__name__}"}
        result = covers.set_manual_cover(text, data, origin)
        if result.get("ok"):
            result["is_manual"] = True
        return result

    def clear_cover(self, title: str) -> dict:
        """撤销人工封面，恢复自动抓取。"""
        return covers.clear_manual_cover(title)

    def cover_is_manual(self, titles: list[str]) -> dict:
        """批量标记哪些作品用了人工封面，供剧集墙卡片显示角标。"""
        return {str(t): covers.is_manual(str(t)) for t in (titles or [])[:400]}

    def choose_image_file(self) -> dict:
        """系统文件对话框选一张本地图片；返回绝对路径，由 set_cover 读取。"""
        try:
            import webview
            if not self._window:
                return {"ok": False, "error": "窗口尚未就绪，请改用粘贴或图片地址"}
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("图片文件 (*.jpg;*.jpeg;*.png;*.webp;*.gif;*.bmp)",),
            )
            if result and len(result):
                return {"ok": True, "path": str(result[0])}
            return {"ok": False, "error": "已取消"}
        except Exception as exc:
            return {"ok": False, "error": f"对话框不可用：{exc}"}

    # ---------- 后台进程管理 ----------
    def process_status(self) -> dict:
        """一次拿到守护进程 + 封面缓存任务的状态，供「后台进程」页轮询。

        只读，不启动任何进程；封面抓取仍然是用户点按钮才发生的一次性任务。
        """
        daemon = self.daemon_running()
        return {
            "ok": True,
            "daemon": {
                "running": bool(daemon.get("running")),
                "pid": daemon.get("pid"),
                "auto_start": bool(self._daemon_auto_start_enabled()),
            },
            "covers": covers.status(),
            "cover_dir": str(config.COVER_DIR),
        }

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
            if record["deleted_at"]:
                raise ValueError("本地文件已移入回收站；观看记录已保留，恢复文件后再播放")
            raise ValueError("文件不在扫描库中或已被移动，请先扫描所在目录")
        position = 0
        if not from_start:
            progress = db.row("SELECT position_ms, completed FROM watch_progress WHERE file_id = ?", (record["id"],))
            if progress and not progress["completed"]:
                position = progress["position_ms"] or 0
        # 用户之前把这条从「继续观看」里移除过（软删）；重新点播放就让它回来
        db.execute(
            "UPDATE watch_progress SET dismissed_at = NULL WHERE file_id = ? AND dismissed_at IS NOT NULL",
            (record["id"],),
        )
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

    # ---------- 播放控制（保留 PotPlayer：快捷键注入 + 命令行换片） ----------
    PLAYER_ACTIONS = ("play_pause", "stop", "fullscreen")

    def player_control(self, action: str) -> dict:
        """播放/暂停、停止、全屏：白名单动作才允许投键，防止前端传任意键码。"""
        action = str(action or "").strip()
        if action not in self.PLAYER_ACTIONS:
            return {"ok": False, "error": f"不支持的播放控制：{action}"}
        return potplayer.send_key(action)

    def player_speed(self, target: float) -> dict:
        try:
            value = float(target)
        except (TypeError, ValueError):
            return {"ok": False, "error": "倍速必须是数字"}
        if value not in potplayer.SPEED_PRESETS:
            return {"ok": False, "error": "只支持 1.0 / 1.25 / 1.5 / 2.0 倍速"}
        return potplayer.apply_speed(value)

    def player_bindings(self) -> dict:
        """设置页读取当前键位与步进（不暴露任意配置，只读播放控制相关项）。"""
        return {
            "ok": True,
            "keys": {name: potplayer.binding(name) for name in potplayer.DEFAULT_KEY_BINDINGS},
            "step": potplayer.speed_step(),
            "presets": list(potplayer.SPEED_PRESETS),
            "steps": list(potplayer.SPEED_STEPS),
            "key_choices": sorted(potplayer.VK_CODES),
        }

    def player_adjacent(self, file_id: int, direction: str = "next") -> dict:
        """按「作品 → 季 → 集」找上一集/下一集；只返回目标，播放仍由前端统一发起。

        复用 get_media_tree 的分组结果，保证与媒体库/剧集墙看到的是同一套顺序。
        """
        try:
            fid = int(file_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "文件 id 无效"}
        step = -1 if str(direction) == "prev" else 1
        tree = self.get_media_tree({"mode": "series"})
        for group in tree.get("groups", []):
            files: list[dict] = []

            def walk(node: dict) -> None:
                files.extend(node.get("files") or [])
                for child in node.get("children") or []:
                    walk(child)

            walk(group)
            if not any(int(f.get("id") or 0) == fid for f in files):
                continue
            ordered = sorted(files, key=library_tree._file_order)
            ids = [int(f["id"]) for f in ordered if f.get("id") is not None]
            if fid not in ids:
                return {"ok": False, "error": "该文件不在剧集序列中"}
            index = ids.index(fid) + step
            if index < 0 or index >= len(ids):
                return {"ok": False, "error": "已经是第一集" if step < 0 else "已经是最后一集"}
            target = ordered[index]
            return {
                "ok": True, "file_id": ids[index],
                "title": target.get("display_title") or target.get("title_guess") or target.get("filename"),
                "direction": "next" if step > 0 else "prev",
                "index": index + 1, "total": len(ids),
            }
        return {"ok": False, "error": "找不到该文件所属的剧集分组"}

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

    @staticmethod
    def _daemon_processes() -> list:
        """列出托盘守护进程。

        源码版命令行是 ``pythonw.exe daemon.py``；打包版是同一个 exe 带
        ``--daemon``。打包版必须靠这个参数区分，否则会把主窗口也算成守护。

        判定必须精确到「可执行文件是 python/pythonw，且某个参数的路径名恰好是
        daemon.py」：早期用整条命令行做子串匹配，会把 ``python -c "…daemon.py…"``
        这类诊断脚本、CLI 命令误判成守护，让状态显示与实际不符。

        每个进程单独容错：单个进程无权限 / 已退出只跳过它，绝不中断整轮扫描
        （否则排在守护之前的系统进程一抛异常，就会漏报成「守护未启动」）。
        psutil 整体枚举失败时静默降级为空列表。
        """
        found = []
        try:
            own_exe = str(runtime.executable()).replace("\\", "/").lower()
            frozen = runtime.is_frozen()
            for proc in psutil.process_iter(["cmdline"]):
                try:
                    parts = proc.info.get("cmdline") or []
                    if not parts:
                        continue
                    first = str(parts[0]).replace("\\", "/").lower()
                    exe_name = first.rsplit("/", 1)[-1]
                    is_python = exe_name in ("python.exe", "pythonw.exe")
                    # 参数里存在路径末段恰好为 daemon.py 的脚本才算源码版守护
                    script_arg = any(
                        str(part).replace("\\", "/").rsplit("/", 1)[-1].lower() == "daemon.py"
                        for part in parts[1:]
                    )
                    source_mode = is_python and script_arg
                    frozen_mode = frozen and "--daemon" in parts and first == own_exe
                    if source_mode or frozen_mode:
                        found.append(proc)
                except Exception:
                    continue
        except Exception:
            pass
        return found

    def daemon_running(self) -> dict:
        """检查托盘守护进程是否已经在运行（避免重复拉起多个托盘图标）。"""
        procs = self._daemon_processes()
        if procs:
            return {"running": True, "pid": procs[0].pid}
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

    def stop_daemon(self) -> dict:
        """停止当前托盘守护进程（设置里关掉自动启动时立即生效）。

        先优雅 terminate，超时再 kill；单个进程异常不影响其余进程，也绝不抛到前端。
        """
        stopped = 0
        for proc in self._daemon_processes():
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
                stopped += 1
            except Exception:
                continue
        self._daemon_auto_started = False
        return {"ok": True, "stopped": stopped}

    @staticmethod
    def _daemon_auto_start_enabled() -> bool:
        return db.get_int("daemon_auto_start", config.DEFAULTS["daemon_auto_start"]) == 1

    def ensure_daemon_auto_start(self) -> dict:
        """窗口就绪后调用：默认自动拉起后台记录，设置里关闭后什么也不做。

        刻意不放在 ``Api.__init__``——单测会大量实例化 Api，绝不能在那里
        真的拉起进程。这里只被 create_window() / 前端 init() 显式调用。
        """
        if not self._daemon_auto_start_enabled():
            return {"ok": True, "started": False, "disabled": True}
        if self._daemon_auto_started or self.daemon_running()["running"]:
            return {"ok": True, "started": False, "already": True}
        result = self.start_daemon()
        if result.get("ok") and not result.get("already"):
            self._daemon_auto_started = True
        return {
            "ok": bool(result.get("ok")),
            "started": bool(result.get("ok")),
            "error": result.get("error", ""),
        }

    def _apply_daemon_auto_start(self) -> dict:
        """设置页保存后立刻生效：开 -> 马上补拉起，关 -> 停掉当前守护。"""
        self._daemon_auto_started = False
        if self._daemon_auto_start_enabled():
            return self.start_daemon()
        return self.stop_daemon()

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
