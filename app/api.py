"""pywebview 的 js_api：前端与本地数据之间的唯一桥梁。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import psutil

from . import (
    __version__, autostart, backup, blacklist, config, covers, db, embedded_player,
    everything, file_ops, importer, library_tree, listimport, metadata, obsidian,
    potplayer, runtime, scanner, textmatch, theme_native, watchlist, websearch,
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

# 媒体库 / 剧集墙的整份结果缓存。这两个接口每次都要扫全表 + 分组 + 解析封面，
# 实测 2100 个文件要 1.1 秒；来回切视图时完全没必要重算。
# 失效靠「数据库文件签名」而不是时间：任何写库（扫描 / 改名 / 改设置）都会让
# WAL 的 mtime/size 变化，签名一变缓存自动作废，所以看到的数据永远不是旧的。
_MEDIA_CACHE_TTL = 20.0
_MEDIA_CACHE: dict[tuple, tuple[float, dict]] = {}


def _display_title_expr() -> str:
    """列表展示用标题表达式，按 ``title_lang_pref`` 决定中文名与原名谁优先。

    ``cn``（默认）：优先中文名，没有则用原名；``orig``：优先原名（日文/英文），
    没有则回退中文名。两种都不会改变数据库里存的值，纯展示偏好。
    """
    pref = str(db.get_setting("title_lang_pref", "cn") or "cn").lower()
    if pref in ("orig", "original", "en", "jp", "ja"):
        return "COALESCE(f.title_guess, f.title_cn)"
    return "COALESCE(f.title_cn, f.title_guess)"

# 多版本（P2-3）：同一 (作品, 季, 集) 存在多份文件时，平铺列表给一个版本下拉。
# 索引按数据指纹缓存，不在每次翻页时重扫全表。
def _version_index() -> dict:
    key = ("versions", _db_signature())
    cached = _cache_get(key)
    if cached is not None:
        return cached
    rows = db.rows(
        "SELECT COALESCE(NULLIF(TRIM(COALESCE(title_cn, title_guess)), ''), filename) AS t, "
        "COALESCE(season_no, 0) AS s, episode_no AS e, id, filename "
        "FROM media_files WHERE COALESCE(deleted_at, '') = '' AND episode_no IS NOT NULL "
        "ORDER BY COALESCE(size, 0) DESC, id"
    )
    index: dict[tuple, list[dict]] = {}
    for row in rows:
        index.setdefault((row["t"], int(row["s"]), int(row["e"])), []).append(
            {"file_id": row["id"], "filename": row["filename"]})
    # 只有真的多份才附加，单版本行多带一个字段只会让前端多判断
    return _cache_put(key, {k: v for k, v in index.items() if len(v) > 1})


def _attach_versions(rows: list[dict]) -> None:
    """给平铺列表的行补 ``versions``（仅当同一集有多份文件时）。"""
    if not rows:
        return
    index = _version_index()
    if not index:
        return
    for row in rows:
        episode = row.get("episode_no")
        if episode is None:
            continue
        title = str(row.get("title_cn") or row.get("title_guess")
                    or row.get("filename") or "").strip()
        if not title:
            continue
        versions = index.get((title, int(row.get("season_no") or 0), int(episode)))
        if versions and len(versions) > 1:
            row["versions"] = versions


# 文件浏览的排序白名单：用户输入只能选键，绝不直接拼进 SQL
_MEDIA_SORTS = {
    "newest": "f.id DESC",
    "name": "f.filename COLLATE NOCASE ASC",
    "cn": "COALESCE(f.title_cn, f.title_guess, f.filename) COLLATE NOCASE ASC",
    "size": "COALESCE(f.size, 0) DESC, f.id DESC",
    "progress": "COALESCE(p.percent, 0) DESC, f.id DESC",
}


def _data_fingerprint() -> tuple:
    """与「写库次数」无关的纯数据指纹：媒体行统计 + 看过的列表行统计。

    播放进度写入（守护每 3 秒一次）不会改变它，所以基于它的缓存不会被正常
    播放打断；导入 / 同步看过的列表、改名、增删文件则一定会变。
    """
    values = (0, 0, 0, 0, 0)
    try:
        row = db.row(
            "SELECT COUNT(*) AS n, COALESCE(MAX(id), 0) AS max_id, "
            "COALESCE(SUM(LENGTH(filename)), 0) AS fn, "
            "COALESCE(SUM(LENGTH(COALESCE(title_cn, ''))), 0) AS cn, "
            "COALESCE(SUM(CASE WHEN COALESCE(deleted_at, '') <> '' THEN 1 ELSE 0 END), 0) AS del "
            "FROM media_files"
        ) or {}
        values = tuple(int(row.get(k) or 0) for k in ("n", "max_id", "fn", "cn", "del"))
    except Exception:
        pass
    watched = (0, 0)
    try:
        row = db.row(
            "SELECT COUNT(*) AS n, COALESCE(SUM(LENGTH(COALESCE(title, '')) "
            "+ LENGTH(COALESCE(title_cn, ''))), 0) AS wl FROM watched_list"
        ) or {}
        watched = (int(row.get("n") or 0), int(row.get("wl") or 0))
    except Exception:
        pass
    return values + watched


def _db_signature() -> tuple:
    """媒体数据的指纹，用来判断缓存是否还有效。

    刻意**不用**数据库文件 mtime：守护进程每 3 秒写一次播放进度，mtime 一直在变，
    缓存会永远失效。这里只统计「影响分组的字段」：行数、最大 id、
    文件名 / 中文名的总长度、软删除行数、看过的列表行数与标题长度，
    再叠加本进程的写库次数（覆盖改设置这类不改变上述统计的写）。
    播放进度的具体位置不影响分组与标记，故意不纳入。

    「看过的列表」单独算一份：剧集墙卡片的「看过」标记跟着它走，
    同步 / 导入 / 清空之后必须失效，否则标记会停在旧状态。
    """
    # 键里带库路径：测试会在临时库之间来回切，只看计数会把上一个库的结果带过来
    return (str(config.DB_PATH), db.write_count()) + _data_fingerprint()


def _cache_get(key: tuple):
    item = _MEDIA_CACHE.get(key)
    if item and (time.monotonic() - item[0]) < _MEDIA_CACHE_TTL:
        return item[1]
    return None


def _cache_put(key: tuple, value: dict) -> dict:
    # 只留最近若干条，避免长时间运行后无限增长（键里带选项，理论上可枚举）
    if len(_MEDIA_CACHE) > 24:
        _MEDIA_CACHE.clear()
    _MEDIA_CACHE[key] = (time.monotonic(), value)
    return value


# ---- 剧集墙「是否看过」标记 -------------------------------------------------
# 两个数据源合起来才完整：
#   1) watch_progress：本软件里真正播过的集（精确到集，能算 3/12）；
#   2) watched_list：导入 / 同步来的「看过的列表」（作品级，本地没有播放记录
#      也应当算看过，否则用户导了几百部之后剧集墙全是「未看」，标记就没意义）。
# 匹配沿用 watchlist 现成的规则（首个命中即停、短英文词只认独立单词），
# 不另写一套模糊匹配，避免误标。

def _group_file_ids(node: dict) -> list[int]:
    """递归收集分组（含季子节点）里的全部文件 id，用来和看过的列表求交集。"""
    ids: list[int] = []
    stack = [node]
    while stack:
        current = stack.pop()
        for item in current.get("files") or []:
            fid = item.get("id")
            if fid is not None:
                ids.append(int(fid))
        stack.extend(current.get("children") or [])
    return ids


_WATCHED_CACHE: dict = {"key": None, "items": []}


def _title_contains(card_names: list[str], wanted: str) -> bool:
    """卡片名与看过的列表标题是否确实是同一部作品。

    ``watchlist.match_files`` 是给追剧列表用的**宽松**匹配（首个命中即停，
    还会退化到「作品名里最具体的一段」），直接用会把
    「斯巴达300勇士」算到「斯巴达克斯第二季」头上、「After Story」算到
    「内情 The inside story」头上、`real真实` 算到一部英文片头上。
    标记是二元判断，错标比漏标更伤信任，所以这里只认：

    * 归一化后完全相等；或
    * 一方完整包含另一方，且**短的那一侧至少 4 个字符**。

    2~3 字的通用前缀（「魔王」「斯巴达」）永远不认，那些交给完全相等。
    """
    target = textmatch.normalize(wanted)
    if len(target) < 2:
        return False
    for name in card_names:
        current = textmatch.normalize(name)
        if len(current) < 2:
            continue
        if current == target:
            return True
        short, long = (current, target) if len(current) <= len(target) else (target, current)
        if len(short) >= 4 and short in long:
            return True
    return False


def _watched_list_matches() -> list[tuple[frozenset[int], str]]:
    """「看过的列表」里每条记录 -> (它匹配到的本地文件 id, 原始标题)。

    建索引 + 匹配一次约 200ms，所以按数据指纹缓存。这里**刻意不含写库次数**：
    守护每 3 秒写一次播放进度，写计数一直在涨，带上它等于每 3 秒重算一遍。
    指纹里的「看过的列表行数 + 标题长度」已经能覆盖导入 / 同步 / 清空。
    """
    key = (str(config.DB_PATH),) + _data_fingerprint()
    if _WATCHED_CACHE["key"] == key:
        return _WATCHED_CACHE["items"]
    items: list[tuple[frozenset[int], str]] = []
    rows = db.rows("SELECT title, title_cn FROM watched_list")
    if rows:
        media = watchlist.media_index()      # 整批只建一次索引，不是每行一次
        for row in rows:
            for candidate in (row.get("title_cn"), row.get("title")):
                name = str(candidate or "").strip()
                if not name:
                    continue
                count, hits, _keyword = watchlist.match_files(name, media)
                if count:
                    ids = frozenset(int(h["id"]) for h in hits if h.get("id") is not None)
                    items.append((ids, name))
                    break
    _WATCHED_CACHE.update(key=key, items=items)
    return items


def _watch_state(all_done: bool, watched: int, playing: int, listed: bool) -> str:
    """卡片状态，前端只按这个字符串渲染，不在 JS 里重算优先级。

    优先级：整部看完 > 看过一部分 > 看了一半 > 在「看过的列表」里 > 未看。
    ``watching`` 排在 ``listed`` 前面：有真实进度时以进度为准。
    """
    if all_done:
        return "done"
    if watched:
        return "partial"
    if playing:
        return "watching"
    if listed:
        return "listed"
    return "none"


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
            f"{_display_title_expr()} AS display_title, "
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
            f"{_display_title_expr()} AS display_title, "
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

    def batch_mark(self, file_ids: list, action: str = "watched") -> dict:
        """批量标记已看 / 未看 / 重置进度（v0.1.6）。

        复用单条 ``_set_watch_state``，语义完全一致：只改 ``watch_progress``，
        绝不删 ``watch_sessions`` 历史。逐条容错，单条失败不影响其余。
        """
        mapping = {
            "watched": 1, "mark_watched": 1, "done": 1,
            "unwatched": 0, "mark_unwatched": 0, "reset": 0, "reset_progress": 0,
        }
        key = str(action or "watched").strip().lower()
        if key not in mapping:
            return {"ok": False, "error": f"不支持的操作：{action}"}
        completed = mapping[key]
        updated = 0
        failed = 0
        seen: list[int] = []
        for raw in (file_ids or []):
            try:
                fid = int(raw)
            except (TypeError, ValueError):
                continue
            if fid in seen:
                continue
            seen.append(fid)
            result = self._set_watch_state(fid, completed)
            if result.get("ok"):
                updated += 1
            else:
                failed += 1
        return {"ok": True, "action": key, "completed": completed,
                "updated": updated, "failed": failed, "total": len(seen)}

    # ---------- 数据备份 / 恢复（v0.1.6，见 app/backup.py） ----------
    def export_backup(self, path: str = "") -> dict:
        """导出 9 张表到 JSON；path 为空时写到 <数据目录>/backups/。"""
        return backup.export_to_json(path or None)

    def import_backup(self, path: str, mode: str = "merge") -> dict:
        """从 JSON 恢复；mode=merge 仅新增 / overwrite 整库覆盖，恢复前自动备份。"""
        return backup.restore_from_json(path, mode)

    def list_backups(self) -> list[dict]:
        return backup.list_backups()

    def export_table(self, kind: str = "history") -> dict:
        """把历史 / 看过的列表 / 媒体库导成 CSV 到 <数据目录>/exports/。"""
        return backup.export_csv(kind)

    def export_nfo(self) -> dict:
        """按作品导出 Kodi tvshow.nfo 到 <数据目录>/exports/nfo/（不写视频目录）。"""
        return backup.export_nfo()

    def open_export_folder(self) -> dict:
        """打开导出目录，方便用户取走 CSV / NFO。"""
        path = Path(config.DATA_DIR) / backup.EXPORT_DIR_NAME
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(str(path))
            return {"ok": True, "path": str(path)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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
                f"{_display_title_expr()} AS display_title, "
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
            f"SELECT f.*, {_display_title_expr()} AS display_title, "
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
        cache_key = ("files", json.dumps(opt, sort_keys=True, ensure_ascii=False),
                     _db_signature())
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
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
            f"SELECT f.*, {_display_title_expr()} AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY " + order
        )
        query_params = list(params)
        if size > 0:
            sql += " LIMIT ? OFFSET ?"
            query_params.extend([size, (page - 1) * size])
        rows = db.rows(sql, tuple(query_params))
        # SQL 粗筛对连接符不敏感（FC2-PPV 匹配不到词表里的 FC2PPV），
        # 这一层再按归一化名称精确过一遍；条数同步扣减，避免「本页只剩 197 条」这类错位。
        kept = blacklist.filter_rows(rows)
        dropped = len(rows) - len(kept)
        rows = kept
        if size > 0:
            total = max(0, total - dropped)
            pages = max(1, (total + size - 1) // size)
            offset = (page - 1) * size
            first = offset + 1 if offset < total else 0
            last = min(offset + size, total) if first else 0
        else:
            pages, first, last = 1, (1 if total else 0), total
        _attach_versions(rows)
        return _cache_put(cache_key, {
            "ok": True, "rows": rows, "total": total, "total_all": total_all,
            "matched": self._matched_count(), "page": page, "page_size": size, "pages": pages,
            "from": first, "to": last,
            "directories": self._directory_options(),
        })

    def get_media_tree(self, options: dict | None = None) -> dict:
        """树状视图：按「作品 → 季 → 集」或「目录 → 子目录 → 文件」分组。

        与平铺列表共用同一套搜索 / 目录 / 匹配状态筛选，但**不受每页条数限制**：
        树是对当前筛选结果的全部文件做分组，节点默认折叠，展开才渲染文件行。
        结果按「选项 + 数据库签名」缓存：来回切视图不再重算分组（实测省 0.3~0.5s）。
        """
        opt = options or {}
        cache_key = ("tree", json.dumps(opt, sort_keys=True, ensure_ascii=False),
                     _db_signature())
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        where, params = self._media_filters(opt)
        rows = db.rows(
            "SELECT f.id, f.dir_id, f.path, f.filename, f.size, f.title_raw, "
            "f.title_guess, f.title_cn, f.match_by, f.season_no, f.episode_no, f.year, "
            f"f.status, f.deleted_at, {_display_title_expr()} AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC",
            tuple(params),
        )
        # 目录名派生的分组标题只能在这一层判断：文件名里可能没有黑名单词，
        # 但发布目录名里有（用户反馈的「剧集墙还能看到」就是这么漏的）。
        rows = blacklist.filter_rows(rows)
        directories = self._directory_options()
        tree = library_tree.build_tree(rows, str(opt.get("mode") or "series"),
                                       directories, self._work_labels())
        groups = self._prune_blacklisted_groups(tree.get("groups") or [])
        if len(groups) != len(tree.get("groups") or []):
            tree = {**tree, "groups": groups}
        return _cache_put(cache_key, {
            "ok": True, **tree,
            "total_all": int(db.scalar("SELECT COUNT(*) FROM media_files") or 0),
            "matched": self._matched_count(),
            "directories": directories,
        })

    @staticmethod
    def _prune_blacklisted_groups(groups: list[dict]) -> list[dict]:
        """按分组标题（作品名 / 目录名 / 季目录名）再筛一遍黑名单。

        只筛标题，不动组内文件：文件行已经被 SQL + :func:`blacklist.filter_rows`
        过滤过；这里补的是「文件名里没有、目录名里才有」那一类。

        季子节点也要看：作品目录可能叫 ``合集``、季目录才叫 ``三上悠亜 精选``，
        此时卡片标题干净、子标题带词，漏看就等于没过滤。
        """
        if not blacklist.active():
            return groups
        kept: list[dict] = []
        for group in groups:
            names: list[object] = [group.get("label"), group.get("sub")]
            for child in group.get("children") or []:
                if isinstance(child, dict):
                    names.append(child.get("label"))
            if blacklist.hit(*names):
                continue
            kept.append(group)
        return kept

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
            f"f.status, f.deleted_at, {_display_title_expr()} AS display_title, "
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
            f"{_display_title_expr()} AS display_title, "
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
    def _listed_in_watched_list(group: dict, cover_titles: list[str],
                                matches: list[tuple[frozenset[int], str]]) -> bool:
        """该分组是否在「看过的列表」里被记为看过。

        两道条件缺一不可：① 列表条目确实匹配到了这个分组里的文件；
        ② 标题也对得上（见 :func:`_title_contains`）。只满足①会把
        《斯巴达克斯》标成《斯巴达300勇士》；只满足②在重名作品上会误标。
        """
        if not matches:
            return False
        ids = set(_group_file_ids(group))
        if not ids:
            return False
        names = [str(group.get("label") or ""), *cover_titles]
        for matched_ids, name in matches:
            if ids.isdisjoint(matched_ids):
                continue
            if _title_contains(names, name):
                return True
        return False

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
        """剧集墙：作品卡片（封面 + 季/集统计 + 续播集）。复用树分组，不另写一套搜索。

        结果按「选项 + 数据库签名」缓存：这两步（全表分组 + 195 张封面解析）
        原本要 1.1 秒，切一次视图就重算一遍是「打开剧集墙很慢」的主因。
        """
        opt = options or {}
        cache_key = ("wall", json.dumps(opt, sort_keys=True, ensure_ascii=False),
                     _db_signature())
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        where, params = self._media_filters(opt)
        rows = db.rows(
            "SELECT f.id, f.dir_id, f.path, f.filename, f.title_raw, f.title_guess, "
            "f.title_cn, f.match_by, f.season_no, f.episode_no, f.deleted_at, "
            f"{_display_title_expr()} AS display_title, "
            "p.position_ms, p.percent, p.completed, p.last_played_at "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE " + where + " ORDER BY f.filename COLLATE NOCASE ASC, f.id ASC",
            tuple(params),
        )
        rows = blacklist.filter_rows(rows)
        tree = library_tree.build_tree(rows, "series", self._directory_options(),
                                      self._work_labels())
        cover_entries = covers.subject_covers()
        # 「看过的列表」命中本地文件的记录：只在有数据时建一次索引（200ms），
        # 剧集墙本体有 20s 缓存，不会每次切视图都付这笔钱。
        watched_matches = _watched_list_matches()
        cards = []
        season_cards = []
        groups = self._prune_blacklisted_groups(tree.get("groups") or [])
        for group in groups:
            seasons = [c.get("label") for c in group.get("children", [])]
            watched_count = int(group.get("watched_count") or 0)
            playing_count = int(group.get("playing_count") or 0)
            all_done = bool(group.get("all_done"))
            cover_titles = self._group_cover_titles(group)
            listed = self._listed_in_watched_list(group, cover_titles, watched_matches)
            # 按季展开的卡片（P2-2）：同一部作品的每一季单独一张，封面沿用作品封面
            for node in group.get("children", []):
                s_files = int(node.get("file_count") or 0)
                s_watched = int(node.get("watched_count") or 0)
                s_playing = int(node.get("playing_count") or 0)
                if not s_files:
                    continue
                s_done = s_watched == s_files
                season_cards.append({
                    "title": f"{group.get('label', '')} · {node.get('label', '')}",
                    "work_title": group.get("label", ""),
                    "search_keyword": self._card_search_keyword(group),
                    "file_count": s_files,
                    "watched_count": s_watched,
                    "playing_count": s_playing,
                    "next_file_id": node.get("next_file_id"),
                    "season_no": int(node.get("season_no") or 0),
                    "season_count": len(seasons),
                    "seasons": seasons[:12],
                    "sub": node.get("sub", ""),
                    "group_id": node.get("id", ""),
                    # 评分/标签挂在作品上：季卡片共用作品的 key
                    "work_key": group.get("id", ""),
                    "all_done": s_done,
                    "watch_state": _watch_state(s_done, s_watched, s_playing, listed),
                    "watched_listed": listed,
                    "removable_count": sum(
                        1 for f in (node.get("files") or [])
                        if int(f.get("completed") or 0) == 1 and not f.get("deleted_at")
                    ),
                    "_cover_titles": cover_titles,
                })
            cards.append({
                "title": group.get("label", ""),
                "search_keyword": self._card_search_keyword(group),
                "file_count": group.get("file_count", 0),
                "watched_count": watched_count,
                "playing_count": playing_count,
                "next_file_id": group.get("next_file_id"),
                "season_count": len(seasons),
                "seasons": seasons[:12],
                "sub": group.get("sub", ""),
                "group_id": group.get("id", ""),
                "work_key": group.get("id", ""),
                "all_done": all_done,
                # 卡片「是否看过」标记：前端只认这个字符串，不在 JS 里重算优先级
                "watch_state": _watch_state(all_done, watched_count, playing_count, listed),
                "watched_listed": listed,
                "removable_count": int(group.get("removable_count") or 0),
                "_cover_titles": cover_titles,
            })
        keyword = str(opt.get("keyword") or "").strip().lower()
        if keyword:
            cards = [c for c in cards if keyword in c["title"].lower()]
            season_cards = [c for c in season_cards if keyword in c["title"].lower()]
        try:
            max_cards = max(1, min(int(opt.get("limit") or 300), 2000))
        except (TypeError, ValueError):
            max_cards = 300
        total = len(cards)
        cards = cards[:max_cards]
        season_total = len(season_cards)
        season_cards = season_cards[:max_cards]
        # 卡片标题（树分组 label）与抓取封面时用的标题可能不同：
        # 先用卡片标题，再退到该分组内文件的中文名 / 解析原名，命中就复用同一张封面。
        pending: list[tuple[str, list[str]]] = []
        for card in cards:
            raw_label = card["title"]
            # 脏分组名（如 1.36G）展示也换成推断出的中文片名；封面仍先用
            # 原分组名匹配，避免已经缓存好的封面突然失效。
            if (not _CARD_CJK_RE.search(raw_label)
                    and _CARD_CJK_RE.search(card["search_keyword"])):
                card["title"] = card["search_keyword"]
            pending.append((card["title"], [raw_label, card["title"],
                                            *card.pop("_cover_titles")]))
        for card in season_cards:
            pending.append((card["title"], [card["work_title"], *card.pop("_cover_titles")]))
        resolved = covers.resolve_cover_many(pending, cover_entries)
        return _cache_put(cache_key, {
            "ok": True, "cards": cards, "total": total, "shown": len(cards),
            # 按季展开的卡片（P2-2）：前端「按季 / 按作品」切换用，默认展示 cards
            "season_cards": season_cards, "season_total": season_total,
            "covers": resolved,
            "cover_status": covers.status(),
        })

    # ---- 统计 / 日历 / 标签 / 多版本（P2-1 ~ P2-4） ------------------------
    def get_watch_stats(self, days: int = 30) -> dict:
        """看片时长统计。

        **统计口径**（写进 FEATURES，改之前先想清楚）：

        - 只用 ``watch_sessions.watched_ms``：只累加「连续播放」的位置增量，
          快进 / 拖进度条 / 回拖都不计入（P0-5 的口径）。
        - 按会话的**开始日期**归日；跨零点的长会话整段算在开始那天。
        - 同一集看两遍算两次（真实发生过的观看），不做去重。
        - 黑名单里的作品不计入。
        """
        try:
            span = max(1, min(int(days or 30), 365))
        except (TypeError, ValueError):
            span = 30
        since = (datetime.now() - timedelta(days=span - 1)).strftime("%Y-%m-%d %H:%M:%S")
        rows = blacklist.filter_rows(
            db.rows(
                "SELECT s.started_at, COALESCE(s.watched_ms, 0) AS watched_ms, s.file_id, "
                f"{_display_title_expr()} AS title, f.season_no, f.episode_no "
                "FROM watch_sessions s LEFT JOIN media_files f ON f.id = s.file_id "
                "WHERE COALESCE(s.started_at, '') >= ?",
                (since,),
            ),
            keys=("title",),
        )

        daily: dict[str, dict] = {}
        works: dict[str, dict] = {}
        total_ms = 0
        for row in rows:
            day = str(row.get("started_at") or "")[:10]
            ms = int(row.get("watched_ms") or 0)
            total_ms += ms
            if day:
                bucket = daily.setdefault(day, {"date": day, "minutes": 0.0, "sessions": 0})
                bucket["minutes"] += ms / 60000
                bucket["sessions"] += 1
            title = str(row.get("title") or "").strip() or "（未关联文件）"
            work = works.setdefault(title, {"title": title, "minutes": 0.0, "sessions": 0,
                                            "files": set(), "last_at": ""})
            work["minutes"] += ms / 60000
            work["sessions"] += 1
            if row.get("file_id"):
                work["files"].add(int(row["file_id"]))
            started = str(row.get("started_at") or "")
            if started > work["last_at"]:
                work["last_at"] = started

        series = []
        for offset in range(span - 1, -1, -1):
            day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
            bucket = daily.get(day)
            series.append({"date": day,
                           "minutes": round(bucket["minutes"], 1) if bucket else 0.0,
                           "sessions": bucket["sessions"] if bucket else 0})

        by_work = sorted(works.values(), key=lambda w: w["minutes"], reverse=True)[:20]
        for work in by_work:
            work["minutes"] = round(work["minutes"], 1)
            work["files"] = len(work["files"])

        active_days = sum(1 for d in series if d["minutes"] > 0)
        totals = {
            "minutes": round(total_ms / 60000, 1),
            "hours": round(total_ms / 3600000, 2),
            "sessions": len(rows),
            "active_days": active_days,
            "span_days": span,
            "avg_minutes_per_active_day": round(
                (total_ms / 60000) / active_days, 1) if active_days else 0.0,
            "works": len(works),
        }
        progress = db.row(
            "SELECT COUNT(*) AS total, "
            "COALESCE(SUM(CASE WHEN completed = 1 THEN 1 ELSE 0 END), 0) AS done "
            "FROM watch_progress"
        ) or {}
        total_files = int(progress.get("total") or 0)
        done_files = int(progress.get("done") or 0)
        return {
            "ok": True,
            "days": span,
            "since": since,
            "daily": series,
            "by_work": by_work,
            "totals": totals,
            "completion": {
                "files": total_files,
                "completed": done_files,
                "rate": round(done_files / total_files * 100, 1) if total_files else 0.0,
            },
            "note": "时长只计连续播放，快进与拖进度条不计入；同一集重看会累计。",
        }

    def get_continue_row(self, limit: int = 12) -> dict:
        """剧集墙顶部常驻的「继续观看」行：同一作品只留最新一条。"""
        try:
            size = max(1, min(int(limit or 12), 60))
        except (TypeError, ValueError):
            size = 12
        rows = blacklist.filter_rows(
            db.rows(
                "SELECT p.file_id, p.position_ms, p.percent, p.last_played_at, "
                f"{_display_title_expr()} AS display_title, f.title_guess, f.filename, "
                "f.season_no, f.episode_no "
                "FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
                "WHERE p.completed = 0 AND p.position_ms > 0 AND p.dismissed_at IS NULL "
                "AND COALESCE(f.deleted_at, '') = '' "
                "ORDER BY p.last_played_at DESC",
            ),
            keys=("display_title", "filename", "title_guess"),
        )
        seen: set[str] = set()
        items: list[dict] = []
        for row in rows:
            title = str(row.get("display_title") or row.get("filename") or "").strip()
            if not title or title in seen:
                continue
            seen.add(title)
            items.append({
                "work_title": title,
                "file_id": row.get("file_id"),
                "filename": row.get("filename"),
                "season_no": row.get("season_no"),
                "episode_no": row.get("episode_no"),
                "percent": row.get("percent") or 0,
                "position_ms": row.get("position_ms") or 0,
                "last_played_at": row.get("last_played_at"),
            })
            if len(items) >= size:
                break
        return {"ok": True, "items": items}

    def get_versions(self, file_id: int) -> dict:
        """同一 (作品, 季, 集) 的全部文件版本，供播放时下拉选择（P2-3）。

        「同作品」用中文名/猜测名 + 季 + 集判定，与展示层合并口径一致；
        不做任何删改，只是把同一集的多份文件列出来。
        """
        base = db.row(
            "SELECT id, COALESCE(NULLIF(TRIM(COALESCE(title_cn, title_guess)), ''), filename) AS title, "
            "season_no, episode_no FROM media_files WHERE id = ?",
            (int(file_id),),
        )
        if not base:
            return {"ok": False, "error": "文件不存在", "versions": []}
        if base["episode_no"] is None:
            return {"ok": True, "versions": [{"file_id": base["id"], "filename": "",
                                              "current": True}]}
        rows = db.rows(
            "SELECT f.id AS file_id, f.filename, f.path, f.size, f.deleted_at, "
            "COALESCE(p.percent, 0) AS percent, COALESCE(p.completed, 0) AS completed "
            "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
            "WHERE COALESCE(NULLIF(TRIM(COALESCE(f.title_cn, f.title_guess)), ''), f.filename) = ? "
            "AND COALESCE(f.season_no, 0) = COALESCE(?, 0) AND f.episode_no = ? "
            "ORDER BY COALESCE(f.size, 0) DESC, f.id",
            (base["title"], base["season_no"], base["episode_no"]),
        )
        for row in rows:
            row["current"] = int(row["file_id"]) == int(file_id)
        return {"ok": True, "versions": rows}

    def get_watch_calendar(self, month: str = "") -> dict:
        """观看日历：某个月每天看了多少分钟（P2-4）。不落表，按会话实时算。"""
        text = str(month or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}", text):
            text = datetime.now().strftime("%Y-%m")
        prefix = text + "-"
        rows = blacklist.filter_rows(
            db.rows(
                "SELECT s.started_at, COALESCE(s.watched_ms, 0) AS watched_ms, "
                f"{_display_title_expr()} AS title "
                "FROM watch_sessions s LEFT JOIN media_files f ON f.id = s.file_id "
                "WHERE COALESCE(s.started_at, '') LIKE ?",
                (prefix + "%",),
            ),
            keys=("title",),
        )
        days: dict[str, dict] = {}
        titles: dict[str, Counter] = {}
        for row in rows:
            day = str(row.get("started_at") or "")[8:10]
            if not day:
                continue
            bucket = days.setdefault(day, {"day": day, "minutes": 0.0, "sessions": 0})
            bucket["minutes"] += int(row.get("watched_ms") or 0) / 60000
            bucket["sessions"] += 1
            titles.setdefault(day, Counter())[str(row.get("title") or "")] += 1
        out = []
        for day, bucket in sorted(days.items()):
            top = titles.get(day) or Counter()
            out.append({"day": day, "date": prefix + day,
                        "minutes": round(bucket["minutes"], 1),
                        "sessions": bucket["sessions"],
                        "top_title": top.most_common(1)[0][0] if top else ""})
        return {"ok": True, "month": text, "days": out,
                "total_minutes": round(sum(d["minutes"] for d in out), 1)}

    def get_work_meta(self, work_keys: list | None = None) -> dict:
        """批量取作品标注（评分 / 标签），前端卡片一次取齐。"""
        keys = [str(k) for k in (work_keys or []) if str(k)]
        if not keys:
            return {"ok": True, "items": {}}
        marks = ",".join("?" for _ in keys)
        rows = db.rows(f"SELECT work_key, rating, tags, updated_at FROM work_meta "
                       f"WHERE work_key IN ({marks})", tuple(keys))
        return {"ok": True, "items": {r["work_key"]: r for r in rows}}

    def save_work_meta(self, work_key: str, rating=None, tags=None) -> dict:
        """保存作品评分 / 标签。``tags`` 传字符串（逗号分隔），空串即清空。"""
        key = str(work_key or "").strip()
        if not key:
            return {"ok": False, "error": "缺少作品标识"}
        try:
            score = None if rating in (None, "", "-") else int(rating)
        except (TypeError, ValueError):
            return {"ok": False, "error": "评分必须是 0~10 的整数"}
        if score is not None and not 0 <= score <= 10:
            return {"ok": False, "error": "评分必须在 0~10 之间"}
        text = ",".join(t.strip() for t in str(tags or "").replace("，", ",").split(",") if t.strip())
        db.execute(
            "INSERT INTO work_meta(work_key, rating, tags, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(work_key) DO UPDATE SET rating = excluded.rating, "
            "tags = excluded.tags, updated_at = excluded.updated_at",
            (key, score, text, db.now()),
        )
        return {"ok": True, "work_key": key, "rating": score, "tags": text}

    def preview_import(self, text: str) -> dict:
        """解析预览时就完成中文名自动匹配，结果直接展示，不留待办队列。"""
        return importer.preview(text or "")

    def commit_import(self, rows: list[dict], skip_unmatched: bool = False) -> dict:
        """确认导入。``skip_unmatched=True`` 时不导入未匹配中文名的行。"""
        return importer.commit(rows or [], skip_unmatched=bool(skip_unmatched))

    # ---- 外部清单导入（P1-2）：MAL XML / Bangumi Takeout JSON ---------------
    def read_import_file(self, path: str) -> dict:
        """读取清单文件内容（限制 20MB）。不解析，只把原文交给前端预览。"""
        return listimport.read_file(path)

    def choose_import_file(self) -> dict:
        try:
            import webview
            if not self._window:
                return {"ok": False, "error": "窗口尚未就绪，请直接粘贴路径"}
            result = self._window.create_file_dialog(
                webview.OPEN_DIALOG, allow_multiple=False,
                file_types=("清单文件 (*.xml;*.json;*.txt)", "所有文件 (*.*)"),
            )
            if result and len(result):
                return {"ok": True, "path": result[0]}
            return {"ok": False, "error": "已取消"}
        except Exception as exc:
            return {"ok": False, "error": f"对话框不可用，请直接粘贴路径（{exc}）"}

    def preview_import_file(self, filename: str = "", content: str = "") -> dict:
        """解析 MAL XML / Bangumi JSON 并返回预览 + 匹配结果 + 差异报告。"""
        return listimport.preview_file(filename or "", content or "")

    def commit_import_list(self, rows: list[dict], skip_unmatched: bool = False) -> dict:
        """按「只增不减 / 不降级 / 不覆盖手改」合并清单记录。"""
        return listimport.commit(rows or [], skip_unmatched=bool(skip_unmatched))

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
            # 完成判定四阈值（v0.1.6）：片头忽略秒数 / 最低续播百分比 / 片尾忽略百分比
            "min_resume_percent", "ignore_seconds_at_start", "ignore_percent_at_end",
            "http_proxy", "metadata_source", "bangumi_base", "window_width", "window_height",
            # 刮削限速与缓存（v0.1.6）：请求间隔 / 429 退避秒数 / 负缓存 TTL / 成功缓存天数
            "bangumi_rate_ms", "bangumi_backoff_sec", "online_negative_ttl_sec",
            "online_cache_ttl_days",
            # 列表展示标题偏好（P1-4）：cn 中文名优先 / orig 原名优先
            "title_lang_pref",
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
        if any(k in (data or {}) for k in (
                "http_proxy", "bangumi_base", "metadata_source",
                "bangumi_rate_ms", "bangumi_backoff_sec")):
            metadata.reset_online()  # 代理 / 策略 / 限速变了，刷新在线配置缓存与退避窗口
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

    # ---------- 网页搜索：抓 AGE / Seeduck 的搜索结果 ----------
    def web_search(self, keyword: str, limit: int = 0) -> dict:
        """把两个站点的搜索结果抓回来渲染成卡片；任一站点失败只影响它自己。"""
        return websearch.search(keyword or "", limit)

    def open_url(self, url: str) -> dict:
        """用系统默认浏览器打开链接。

        只放行 http/https：前端传来的字符串一律当不可信输入，
        禁止 ``file:`` / ``javascript:`` / ``ms-*`` 之类协议，
        也不拼 shell 命令（走 webbrowser 模块）。
        """
        text = str(url or "").strip()
        if not text:
            return {"ok": False, "error": "链接为空"}
        parsed = urllib.parse.urlsplit(text)
        if parsed.scheme not in ("http", "https"):
            return {"ok": False, "error": "只支持 http / https 链接"}
        if not parsed.netloc:
            return {"ok": False, "error": "链接缺少域名"}
        try:
            opened = webbrowser.open(text, new=2)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        if not opened:
            return {"ok": False, "error": "没能唤起默认浏览器"}
        return {"ok": True, "url": text}


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

    # ---- 媒体库体检与路径重定位（P1-1）----------------------------------
    def diagnose_library(self) -> dict:
        """体检：目录是否可达、有多少文件找不到了。只读，不改任何数据。"""
        return scanner.diagnose_library()

    def preview_relocate(self, old_prefix: str, new_prefix: str) -> dict:
        """路径重定位预览：算出会改哪些行、有多少冲突，不写库。"""
        return scanner.preview_relocate(old_prefix, new_prefix)

    def apply_relocate(self, old_prefix: str, new_prefix: str) -> dict:
        """确认执行路径重定位：只 UPDATE 路径与缺失标记，不删除任何记录。

        播放进度、观看历史、看过的列表全部原样保留（主键不变）。
        缓存靠写库次数自动失效，无需手动清。
        """
        return scanner.apply_relocate(old_prefix, new_prefix)

    def dismiss_missing(self, file_ids: list | None = None) -> dict:
        """用户确认后隐藏「缺失」文件：软删（可恢复），不真删行、不动进度。"""
        return scanner.dismiss_missing(file_ids)

    # ---------- 集数待确认（v0.1.6，OPTIMIZATION #6 尾巴） ----------
    def get_episode_review(self, limit: int = 200) -> dict:
        """列出集数解析**低置信度**的文件，交给用户确认。

        只有 ``episode_confidence='low'`` 的才进来：解析器对这类写法（`[01]`、
        `01-02`）本身没有把握，静默猜一个不如让用户点一下。用户确认后
        ``episode_source`` 记为 ``user``，重新扫描不会再覆盖。
        """
        rows = db.rows(
            "SELECT f.id, f.filename, "
            f"{_display_title_expr()} AS display_title, f.season_no, f.episode_no, "
            "f.episode_source, f.path "
            "FROM media_files f "
            "WHERE f.episode_confidence = 'low' AND COALESCE(f.deleted_at, '') = '' "
            "ORDER BY f.filename COLLATE NOCASE LIMIT ?",
            (max(1, min(int(limit or 200), 1000)),),
        )
        return {"ok": True, "rows": blacklist.filter_rows(rows, keys=("display_title", "filename")),
                "total": len(rows)}

    def set_episode(self, file_id: int, season_no=None, episode_no=None) -> dict:
        """用户确认某文件的季/集，并把它标成 ``user`` 来源（重新扫描不再覆盖）。"""
        rec = db.row("SELECT id FROM media_files WHERE id = ?", (int(file_id),))
        if not rec:
            return {"ok": False, "error": "文件不存在"}
        try:
            season = None if season_no in (None, "", "-") else int(season_no)
            episode = None if episode_no in (None, "", "-") else int(episode_no)
        except (TypeError, ValueError):
            return {"ok": False, "error": "季 / 集必须是数字，留空表示不区季"}
        if season is not None and not 0 <= season <= 99:
            return {"ok": False, "error": "季数超出范围（0~99）"}
        if episode is not None and not 0 <= episode <= 9999:
            return {"ok": False, "error": "集数超出范围（0~9999）"}
        db.execute(
            "UPDATE media_files SET season_no = ?, episode_no = ?, episode_source = 'user', "
            "episode_confidence = 'high', updated_at = ? WHERE id = ?",
            (season, episode, db.now(), int(file_id)),
        )
        return {"ok": True, "file_id": int(file_id), "season_no": season, "episode_no": episode}

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
            if record.get("missing_since"):
                raise ValueError(
                    "文件所在目录当前不可达（移动硬盘没插 / 网络盘掉线 / 盘符变了）。"
                    "观看进度已完整保留——插回硬盘后在设置页用「路径重定位」修正路径即可继续播放")
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

    # ---------- 播放控制（保留 PotPlayer：WM_COMMAND 指令 + 命令行换片） ----------
    PLAYER_ACTIONS = ("play_pause", "play", "pause", "stop", "fullscreen")

    def player_control(self, action: str) -> dict:
        """播放/暂停、停止、全屏：白名单动作才允许下发，防止前端传任意命令码。

        播放类动作走 PotPlayer 的 WM_COMMAND 指令并复查状态（会返回实际生效的那条路径），
        全屏没有对应命令码，仍然用快捷键通道。
        """
        action = str(action or "").strip()
        if action not in self.PLAYER_ACTIONS:
            return {"ok": False, "error": f"不支持的播放控制：{action}"}
        if action == "fullscreen":
            return potplayer.send_key("fullscreen")
        return potplayer.control(action)


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

    def rematch_all(self, force: bool = False) -> dict:
        """手动重新匹配所有未识别标题（改了代理或补了词典后可点）。

        ``force=True`` 即「强制重刮」：清空刮削缓存后重查，但仍保护用户手改
        （``match_by='user'`` 的标题不会被覆盖）。
        """
        result = metadata.rematch_all(force=bool(force))
        return {k: v for k, v in result.items() if k != "results"}

    def clear_scrape_cache(self) -> dict:
        """只清缓存、不立即重刮（用户想省流量时用）。"""
        result = metadata.clear_scrape_cache()
        result["stats"] = metadata.scrape_cache_stats()
        return result

    def scrape_cache_stats(self) -> dict:
        return metadata.scrape_cache_stats()

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
