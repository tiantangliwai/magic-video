"""JSON 全量备份 / 恢复。

备份文件是纯 JSON，包含 ``TABLES`` 里 9 张表的全部行，结构稳定便于用户
自己查看或跨机器搬运。恢复支持两种模式：

* ``merge``：只做新增（``INSERT OR IGNORE``），主键/唯一键冲突的行跳过；
* ``overwrite``：先按 ``TABLES`` 反序清空，再按正序写回，等于整库替换。

两种模式都**保留原始 id**：``watch_progress`` / ``watch_sessions`` 通过
``file_id`` 关联 ``media_files``，重新分配 id 会让播放记录错位。
恢复前一律自动备份当前库，避免误操作把数据洗掉。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config, db

APP_NAME = "magic-video"
FORMAT_VERSION = 1
BACKUP_PREFIX = "magicvideo-backup-"
BACKUP_SUFFIX = ".json"
MODES = ("merge", "overwrite")

# 按外键依赖排序：被引用的表在前，引用的表在后。恢复时正序插入、
# 反序删除（先删依赖方），否则会撞外键约束。
TABLES: tuple[str, ...] = (
    "settings",
    "directories",
    "subjects",
    "media_files",
    "watched_list",
    "watchlist",
    "watch_progress",
    "watch_sessions",
    "name_alias",
)


def _backup_dir() -> Path:
    """备份目录：``DATA_DIR/backups``（每次现取，测试会替换 DATA_DIR）。"""
    return Path(config.DATA_DIR) / "backups"


def _default_path() -> Path:
    """默认备份路径；同一秒内多次导出时自动加后缀，不覆盖已有文件。"""
    directory = _backup_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    index = 2
    while path.exists():
        path = directory / f"{BACKUP_PREFIX}{stamp}-{index}{BACKUP_SUFFIX}"
        index += 1
    return path


def export_to_json(path: str | Path | None = None) -> dict:
    """把 9 张表导出成一个 JSON 文件。

    ``path`` 为空时写到 ``DATA_DIR/backups/magicvideo-backup-<时间戳>.json``。
    成功返回 ``{"ok": True, "path": 绝对路径, "counts": {表: 行数}, "total": 总行数}``；
    失败返回 ``{"ok": False, "error": ...}``，不抛异常。
    """
    try:
        tables: dict[str, list[dict]] = {}
        conn = db.get_conn()
        try:
            # 显式开启一个读事务：9 张表读的是**同一个 WAL 快照**。否则每条
            # SELECT 各自 autocommit，导出文件内部可能不自洽（media_files 与
            # watch_progress 差一拍，恢复后出现「有进度没文件」的行）。
            # WAL 下读事务不阻塞写，只会推迟 auto-checkpoint，导出后补收一次。
            conn.isolation_level = None
            conn.execute("BEGIN")
            for table in TABLES:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                tables[table] = [dict(r) for r in rows]
            conn.execute("COMMIT")
        finally:
            conn.close()

        counts = {table: len(rows) for table, rows in tables.items()}
        payload: dict[str, Any] = {
            "app": APP_NAME,
            "format_version": FORMAT_VERSION,
            "exported_at": db.now(),
            "counts": counts,
            "tables": tables,
        }

        target = Path(path).expanduser().resolve() if path else _default_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        db.checkpoint()

        return {
            "ok": True,
            "path": str(target),
            "counts": counts,
            "total": sum(counts.values()),
        }
    except Exception as e:  # noqa: BLE001 - 任何失败都转成结构化结果给上层
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _table_columns(conn: Any, table: str) -> list[str]:
    """表的实际列名（用于过滤备份文件里多出来的未知列）。"""
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _normalize_rows(raw: Any, columns: list[str]) -> tuple[list[str], list[tuple]]:
    """把备份里的行整理成 (列名, 值元组列表)，只保留表里真实存在的列。"""
    if not isinstance(raw, list):
        return [], []
    rows = [item for item in raw if isinstance(item, dict)]
    used = [c for c in columns if any(c in row for row in rows)]
    values = [tuple(row.get(c) for c in used) for row in rows]
    return used, values


def restore_from_json(path: str | Path, mode: str = "merge") -> dict:
    """从 JSON 备份恢复数据。

    ``mode`` 只接受 ``merge``（仅新增）和 ``overwrite``（整库覆盖）。
    恢复前会自动把当前库导出到 ``backups/``，路径见返回值的 ``auto_backup``。
    """
    if mode not in MODES:
        return {
            "ok": False,
            "error": f"不支持的恢复模式：{mode}（只支持 merge / overwrite）",
        }

    try:
        source = Path(path)
        try:
            with open(source, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except FileNotFoundError:
            return {"ok": False, "error": f"备份文件不存在：{source}"}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

        if not isinstance(payload, dict) or not isinstance(payload.get("tables"), dict):
            return {"ok": False, "error": "备份文件格式不正确：缺少 tables 字段"}
        tables: dict[str, Any] = payload["tables"]

        # 先自动备份当前库，失败就不动数据。
        auto = export_to_json()
        if not auto.get("ok"):
            return {
                "ok": False,
                "error": f"自动备份失败，已取消恢复：{auto.get('error')}",
            }
        auto_backup = str(auto["path"])

        inserted: dict[str, int] = {}
        skipped: dict[str, int] = {}
        # 恢复是最长的写事务（DELETE 9 表 + 全量插入，可达数千行）。两处加固：
        # ① 临时关掉守护的记录开关，避免它每 3 秒来抢写锁（守护下一轮就会看到）；
        # ② 用 BEGIN IMMEDIATE 开头就拿写锁，避免 DEFERRED 读到一半升级失败
        #    抛 SQLITE_BUSY_SNAPSHOT（这个错误不吃 busy_timeout，会瞬间失败）。
        # 开关必须直接改 value，不能用 set_setting：后者会刷新 updated_at，
        # 导致「导出→恢复→再导出」的结果与原文不一致（用例会红）。
        def _set_recording(value: int) -> None:
            db.execute(
                "UPDATE settings SET value = ? WHERE key = 'recording_enabled'",
                (str(value),),
            )

        was_recording = db.get_int("recording_enabled", 1)
        if was_recording:
            _set_recording(0)
        try:
            with db.session(immediate=True) as conn:
                conn.execute("PRAGMA busy_timeout=30000")
                if mode == "overwrite":
                    for table in reversed(TABLES):
                        conn.execute(f"DELETE FROM {table}")

                prefix = "INSERT OR IGNORE" if mode == "merge" else "INSERT"
                for table in TABLES:
                    columns = _table_columns(conn, table)
                    used, values = _normalize_rows(tables.get(table), columns)
                    before = conn.total_changes
                    if used and values:
                        marks = ",".join("?" for _ in used)
                        sql = f"{prefix} INTO {table}({','.join(used)}) VALUES ({marks})"
                        conn.executemany(sql, values)
                    written = conn.total_changes - before
                    inserted[table] = written
                    skipped[table] = len(values) - written
        finally:
            if was_recording:
                _set_recording(1)

        return {
            "ok": True,
            "mode": mode,
            "inserted": inserted,
            "skipped": skipped,
            "auto_backup": auto_backup,
        }
    except Exception as e:  # noqa: BLE001 - with db.session() 已回滚，这里只报错
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def list_backups() -> list[dict]:
    """列出备份目录里的 JSON 备份，按修改时间倒序（新的在前）。"""
    directory = _backup_dir()
    if not directory.is_dir():
        return []

    files: list[tuple[Path, float]] = []
    for candidate in directory.glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}"):
        try:
            if not candidate.is_file():
                continue
            files.append((candidate, candidate.stat().st_mtime))
        except OSError:
            continue
    files.sort(key=lambda item: item[1], reverse=True)

    return [
        {
            "path": str(path),
            "name": path.name,
            "size": path.stat().st_size,
            "mtime": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
        }
        for path, mtime in files
    ]


# ---------------------------------------------------------------------------
# 表格 / NFO 导出（v0.1.6 补充，OPTIMIZATION #5 的下半句「可选 NFO/CSV 导出」）
#
# 两条硬约束：
# 1. **只写导出目录**，绝不写进视频所在目录。项目铁律是「扫描、解析、刮削一律只读
#    视频文件，不写同目录 .nfo」——所以 NFO 也导出成独立目录树，由用户自己搬走。
# 2. 导出是**只读查询**，不写库、不动进度，导完对现有缓存无影响。
# ---------------------------------------------------------------------------
EXPORT_DIR_NAME = "exports"
EXPORT_KINDS = ("history", "watched", "media")


def _export_dir() -> Path:
    return Path(config.DATA_DIR) / EXPORT_DIR_NAME


def _export_path(prefix: str, suffix: str) -> Path:
    """导出目录下的带时间戳文件名；同名自动加序号，不覆盖已有导出。"""
    directory = _export_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = directory / f"{prefix}-{stamp}{suffix}"
    index = 2
    while path.exists():
        path = directory / f"{prefix}-{stamp}-{index}{suffix}"
        index += 1
    return path


def _csv_rows(kind: str) -> tuple[list[str], list[list]]:
    """导出三类表格；列顺序固定，方便用户用 Excel 直接做透视。"""
    if kind == "history":
        rows = db.rows(
            "SELECT s.started_at, s.ended_at, COALESCE(f.title_cn, f.title_guess, s.file_id) AS title, "
            "f.season_no, f.episode_no, f.filename, "
            "COALESCE(s.watched_ms, 0) AS watched_ms, "
            "COALESCE(p.percent, 0) AS percent "
            "FROM watch_sessions s LEFT JOIN media_files f ON f.id = s.file_id "
            "LEFT JOIN watch_progress p ON p.file_id = s.file_id "
            "ORDER BY s.started_at DESC"
        )
        header = ["开始时间", "结束时间", "作品", "季", "集", "文件名", "观看分钟", "进度%"]
        body = [[r["started_at"], r["ended_at"], r["title"], r["season_no"], r["episode_no"],
                 r["filename"], round((r["watched_ms"] or 0) / 60000, 1), round(r["percent"] or 0, 1)]
                for r in rows]
        return header, body

    if kind == "watched":
        rows = db.rows(
            "SELECT title, title_cn, season_no, episode_from, episode_to, status, source, "
            "match_by, imported_at, note FROM watched_list ORDER BY id"
        )
        header = ["原名", "中文名", "季", "起始集", "结束集", "状态", "来源", "匹配方式", "导入时间", "备注"]
        body = [[r["title"], r["title_cn"], r["season_no"], r["episode_from"], r["episode_to"],
                 r["status"], r["source"], r["match_by"], r["imported_at"], r["note"]]
                for r in rows]
        return header, body

    rows = db.rows(
        "SELECT f.path, f.filename, COALESCE(f.title_cn, f.title_guess) AS title, "
        "f.season_no, f.episode_no, f.size, COALESCE(f.duration_ms, 0) AS duration_ms, "
        "COALESCE(p.total_watched_ms, 0) AS total_watched_ms, "
        "COALESCE(p.completed, 0) AS completed, p.last_played_at "
        "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
        "ORDER BY f.filename COLLATE NOCASE"
    )
    header = ["路径", "文件名", "作品", "季", "集", "大小MB", "时长分钟", "累计观看分钟", "已看完", "最后播放"]
    body = [[r["path"], r["filename"], r["title"], r["season_no"], r["episode_no"],
             round((r["size"] or 0) / 1048576, 1), round((r["duration_ms"] or 0) / 60000, 1),
             round((r["total_watched_ms"] or 0) / 60000, 1),
             "是" if r["completed"] else "否", r["last_played_at"]]
            for r in rows]
    return header, body


def export_csv(kind: str = "history", path: str | Path | None = None) -> dict:
    """把历史 / 看过的列表 / 媒体库导成 CSV（UTF-8 BOM，Excel 直接双击不乱码）。"""
    import csv

    kind = str(kind or "").strip().lower()
    if kind not in EXPORT_KINDS:
        return {"ok": False, "error": f"不支持的导出类型：{kind}（可选 {', '.join(EXPORT_KINDS)}）"}
    try:
        header, body = _csv_rows(kind)
        target = Path(path) if path else _export_path(f"magicvideo-{kind}", ".csv")
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(body)
        return {"ok": True, "kind": kind, "path": str(target), "rows": len(body)}
    except Exception as e:  # noqa: BLE001 - 导出失败只返回错误，不影响主流程
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def export_nfo(path: str | Path | None = None) -> dict:
    """按作品导出 Kodi 风格 ``tvshow.nfo``，写到独立导出目录树。

    目录结构：``<数据目录>/exports/nfo/<作品名>/tvshow.nfo``。
    **不写进视频目录**（项目铁律），用户需要 Kodi 识别时自己把目录树搬过去。
    """
    from xml.sax.saxutils import escape

    try:
        rows = db.rows(
            "SELECT COALESCE(NULLIF(TRIM(COALESCE(title_cn, title_guess)), ''), filename) AS title, "
            "MIN(year) AS year, COUNT(*) AS episodes, "
            "COUNT(DISTINCT COALESCE(season_no, 0)) AS seasons "
            "FROM media_files WHERE COALESCE(deleted_at, '') = '' "
            "GROUP BY title ORDER BY title COLLATE NOCASE"
        )
        root = Path(path) if path else (_export_dir() / "nfo")
        root.mkdir(parents=True, exist_ok=True)
        written = 0
        for row in rows:
            title = str(row["title"] or "").strip()
            if not title:
                continue
            folder = root / _safe_folder(title)
            folder.mkdir(parents=True, exist_ok=True)
            lines = [
                '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                "<tvshow>",
                f"  <title>{escape(title)}</title>",
                f"  <originaltitle>{escape(title)}</originaltitle>",
            ]
            if row["year"]:
                lines.append(f"  <year>{int(row['year'])}</year>")
            lines.append(f"  <season>{int(row['seasons'] or 1)}</season>")
            lines.append(f"  <episode>{int(row['episodes'] or 0)}</episode>")
            lines.append("  <status>Continuing</status>")
            lines.append("</tvshow>")
            (folder / "tvshow.nfo").write_text("\n".join(lines) + "\n", encoding="utf-8")
            written += 1
        return {"ok": True, "path": str(root), "works": written}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


_WINDOWS_BAD = str.maketrans({c: "_" for c in '\\/:*?"<>|'})


def _safe_folder(title: str) -> str:
    """作品名 → 合法目录名；截断到 80 字符避免超长路径。"""
    name = str(title).translate(_WINDOWS_BAD).strip(" .")
    return (name or "未命名")[:80]
