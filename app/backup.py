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
        with db.session() as conn:
            for table in TABLES:
                rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                tables[table] = [dict(r) for r in rows]

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
        with db.session() as conn:
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
