"""SQLite 数据层：建表、连接、通用查询与配置读写。

WAL 模式保证「托盘守护进程」与「主窗口进程」可以并发读写同一个库。
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterable, Sequence

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS directories (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    path          TEXT NOT NULL UNIQUE,
    enabled       INTEGER NOT NULL DEFAULT 1,
    last_scan_at  TEXT,
    file_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS subjects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bgm_id       INTEGER,
    tmdb_id      INTEGER,
    title_cn     TEXT,
    title_orig   TEXT,
    aliases      TEXT,
    type         TEXT NOT NULL DEFAULT 'anime',
    year         INTEGER,
    season_no    INTEGER,
    cover_url    TEXT,
    cover_local  TEXT,
    summary      TEXT,
    source       TEXT,
    confidence   REAL NOT NULL DEFAULT 0,
    verified     INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_subjects_cn ON subjects(title_cn);
CREATE INDEX IF NOT EXISTS ix_subjects_bgm ON subjects(bgm_id);

CREATE TABLE IF NOT EXISTS media_files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    dir_id        INTEGER REFERENCES directories(id) ON DELETE SET NULL,
    path          TEXT NOT NULL UNIQUE,
    filename      TEXT NOT NULL,
    size          INTEGER,
    mtime         REAL,
    duration_ms   INTEGER,
    partial_hash  TEXT,
    title_raw     TEXT,
    title_guess   TEXT,
    title_cn      TEXT,
    match_by      TEXT,
    season_no     INTEGER,
    episode_no    INTEGER,
    year          INTEGER,
    subject_id    INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    status        TEXT NOT NULL DEFAULT 'new',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at    TEXT,
    -- 人工把本地文件移入回收站的时间；只做标记，绝不删行（watch_progress / watch_sessions 是 CASCADE 外键）
    deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS ix_media_filename ON media_files(filename);
CREATE INDEX IF NOT EXISTS ix_media_subject ON media_files(subject_id);

CREATE TABLE IF NOT EXISTS watched_list (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_text      TEXT,
    title         TEXT NOT NULL,
    title_cn      TEXT,
    season_no     INTEGER,
    episode_from  INTEGER,
    episode_to    INTEGER,
    subject_id    INTEGER REFERENCES subjects(id) ON DELETE SET NULL,
    source        TEXT NOT NULL DEFAULT 'manual',
    status        TEXT NOT NULL DEFAULT 'watched',
    match_by      TEXT,
    imported_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    note          TEXT
);
CREATE INDEX IF NOT EXISTS ix_watched_title ON watched_list(title);

-- 追剧清单：已下载 / 未下载两栏，sort_order 保持导入时的书写顺序且可人工调整。
-- 与 media_files / watch_progress 没有外键：删掉一条追剧记录绝不影响播放记录。
CREATE TABLE IF NOT EXISTS watchlist (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    downloaded   INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 0,
    note         TEXT,
    source       TEXT NOT NULL DEFAULT 'manual',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS ix_watchlist_order ON watchlist(downloaded, sort_order, id);

CREATE TABLE IF NOT EXISTS watch_progress (
    file_id          INTEGER PRIMARY KEY REFERENCES media_files(id) ON DELETE CASCADE,
    position_ms      INTEGER NOT NULL DEFAULT 0,
    duration_ms      INTEGER NOT NULL DEFAULT 0,
    percent          REAL NOT NULL DEFAULT 0,
    completed        INTEGER NOT NULL DEFAULT 0,
    play_count       INTEGER NOT NULL DEFAULT 0,
    first_played_at  TEXT,
    last_played_at   TEXT,
    updated_at       TEXT,
    -- 「继续观看」里人工移除的软删除标记；只隐藏列表，绝不删进度/会话
    dismissed_at     TEXT
);

CREATE TABLE IF NOT EXISTS watch_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id     INTEGER REFERENCES media_files(id) ON DELETE CASCADE,
    started_at  TEXT,
    ended_at    TEXT,
    from_ms     INTEGER NOT NULL DEFAULT 0,
    to_ms       INTEGER NOT NULL DEFAULT 0,
    max_ms      INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'potplayer'
);
CREATE INDEX IF NOT EXISTS ix_sessions_file ON watch_sessions(file_id);
CREATE INDEX IF NOT EXISTS ix_sessions_start ON watch_sessions(started_at);

CREATE TABLE IF NOT EXISTS name_alias (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_title   TEXT NOT NULL UNIQUE,
    title_cn    TEXT NOT NULL,
    title_orig  TEXT,
    subject_key TEXT,
    source      TEXT NOT NULL DEFAULT 'local',
    verified    INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_conn() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(str(config.DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


@contextmanager
def session():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """为已存在的旧库补列：只 ALTER ADD COLUMN，不重建表、不丢已有播放记录。"""
    additions = {
        "media_files": (("title_cn", "TEXT"), ("match_by", "TEXT"), ("deleted_at", "TEXT")),
        "watched_list": (("match_by", "TEXT"),),
        "watch_progress": (("dismissed_at", "TEXT"),),
    }
    for table, columns in additions.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


_INIT_LOCK = threading.Lock()
_INITIALIZED = False


def init_db() -> None:
    """建表 + 迁移 + 写默认设置；同一进程只执行一次，避免界面刷新时反复写库。"""
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        with session() as conn:
            conn.executescript(SCHEMA)
            _migrate(conn)
            for key, value in config.DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
        _INITIALIZED = True


def rows(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    with session() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(sql: str, params: Sequence[Any] = ()) -> dict | None:
    with session() as conn:
        r = conn.execute(sql, params).fetchone()
        return dict(r) if r else None


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    with session() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid or cur.rowcount


def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> int:
    with session() as conn:
        cur = conn.executemany(sql, seq)
        return cur.rowcount


def get_setting(key: str, default: Any = None) -> str | None:
    r = row("SELECT value FROM settings WHERE key = ?", (key,))
    if r:
        return r["value"]
    return str(default) if default is not None else config.DEFAULTS.get(key)


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(float(get_setting(key, default)))
    except (TypeError, ValueError):
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, str(value), now()),
    )


def all_settings() -> dict:
    merged = dict(config.DEFAULTS)
    for r in rows("SELECT key, value FROM settings"):
        merged[r["key"]] = r["value"]
    return merged


def scalar(sql: str, params: Sequence[Any] = ()) -> Any:
    with session() as conn:
        r = conn.execute(sql, params).fetchone()
        return r[0] if r else None
