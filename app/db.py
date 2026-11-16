"""SQLite 数据层：建表、连接、通用查询与配置读写。

WAL 模式保证「托盘守护进程」与「主窗口进程」可以并发读写同一个库。
"""
from __future__ import annotations

import random
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterable, Sequence, TypeVar

from . import config

_T = TypeVar("_T")

# 并发写加固（P1-3）：主窗口、托盘守护、扫描、备份是**多个独立进程**写同一个库。
# WAL 只解决读写并发，写写冲突仍靠 SQLite 的 busy handler 等待。等不到就抛
# OperationalError("database is locked")，上层此前只能静默失败（守护记 last_error）。
# 这里统一做「短退避重试」：只对锁竞争重试，其它错误（如约束冲突）立即抛。
# 注意 readonly 也算：WAL 模式下多个进程同时首次打开库时，抢不到 -shm 映射的
# 一方会拿到 "attempt to write a readonly database"，这是**暂时状态**而非真只读，
# 重试一次就能恢复；若真是权限问题，重试 4 次后仍会把原错误抛给调用方。
_BUSY_HINTS = ("locked", "busy", "readonly")
_RETRY_DELAYS = (0.05, 0.12, 0.30, 0.60)

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
    tvdb_id      INTEGER,
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
    -- 文件名里的作品 ID（{tmdb-123} / [tvdbid-456]）：稳定的作品键。
    -- 同一部番不同季、不同压制组的标题写法差异很大，按 ID 归组后
    -- 只要组内有一条匹配成功，其余同组文件直接共享，不再逐个猜。
    tmdb_id       INTEGER,
    tvdb_id       INTEGER,
    season_no     INTEGER,
    episode_no    INTEGER,
    -- 集数是怎么解析出来的（sxxeyy/xformat/chinese/ep/numbered/bare/trailing/bracket/range/user），
    -- 以及可信度 high/medium/low。low 的条目进「集数待确认」，让用户点一下确认，
    -- 不静默猜（`[01]`、`01-02` 这类写法语义本身就有歧义）。
    episode_source TEXT,
    episode_confidence TEXT,
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
    -- 累计真实观看时长（毫秒）：只累加「连续播放的位置增量」，快进/跳转不计入。
    -- 与 position_ms（当前续播点）是两回事，供后续时长统计使用（v0.1.6）。
    total_watched_ms INTEGER NOT NULL DEFAULT 0,
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
    -- 本会话内累计的真实观看时长（毫秒）：位置增量在合理范围内的部分才累加，
    -- 快进/拖进度条造成的大跳变不计入（v0.1.6 会话切分口径）。
    watched_ms  INTEGER NOT NULL DEFAULT 0,
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

-- 在线刮削结果缓存（含负缓存）。存的是「这个原始标题查过没有、结论是什么」，
-- 与 name_alias（已确认的中文名映射）是两回事：查询未命中也要记一笔，
-- 否则每次重新匹配都会把同一批查不到的标题再打一遍 Bangumi（易被限流/封禁）。
-- 这张表是**可重建的缓存**，不进 backup.TABLES：备份恢复时允许丢掉它。
CREATE TABLE IF NOT EXISTS scrape_cache (
    raw_title   TEXT PRIMARY KEY,
    -- hit 命中 / miss 查无结果 / error 请求失败（超时、429、断网等）
    status      TEXT NOT NULL DEFAULT 'miss',
    bgm_id      INTEGER,
    title_cn    TEXT,
    title_orig  TEXT,
    season_no   INTEGER,
    error       TEXT,
    hits        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    -- NULL = 长期有效（成功结果）；负缓存按 online_negative_ttl_sec 过期
    expires_at  TEXT
);
CREATE INDEX IF NOT EXISTS ix_scrape_expires ON scrape_cache(expires_at);

-- 作品级的用户标注（评分 / 标签）：与 media_files 无外键，按作品分组 id
-- （"series::xxx"，与改名覆盖用的是同一套键）挂靠，删媒体文件不会连带删标注。
-- 纯本地，不同步到任何外部站点。
CREATE TABLE IF NOT EXISTS work_meta (
    work_key    TEXT PRIMARY KEY,
    rating      INTEGER,
    tags        TEXT,
    updated_at  TEXT
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


_WAL_READY: str | None = None


def _is_busy(exc: BaseException) -> bool:
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return any(hint in msg for hint in _BUSY_HINTS)


def _retry(fn: Callable[[], _T], *, tries: int = len(_RETRY_DELAYS) + 1) -> _T:
    """执行 ``fn``，遇到 busy/locked 时按递增间隔重试，其它异常原样抛出。

    注意：``fn`` 必须是可重放的（内部自己新开连接/新事务），
    当前所有调用点都是「一条语句一个连接」，天然满足。
    """
    for attempt in range(tries):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            if attempt >= tries - 1 or not _is_busy(exc):
                raise
            time.sleep(_RETRY_DELAYS[attempt] + random.random() * 0.03)


def get_conn() -> sqlite3.Connection:
    """新建一个连接。

    ``PRAGMA journal_mode=WAL`` 会写数据库头并尝试拿锁，实测每次连接要 3~5ms；
    而 WAL 是**写进库文件**的持久属性，同一个库只需要设一次。
    这里按「库文件路径」记录，只在没设过的库上执行一次
    （上层每个列表接口都要多次读设置，累积起来是秒级差距）。
    注意必须按路径记而不是全局布尔：测试会反复切到临时库。
    """
    global _WAL_READY
    config.ensure_dirs()
    path = str(config.DB_PATH)
    conn = sqlite3.connect(path, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # busy_timeout 必须在 journal_mode 之前：切 WAL 自己也要拿锁，
    # 两个进程同时冷启动切换时否则会立刻抛 database is locked。
    conn.execute("PRAGMA busy_timeout=10000")
    if _WAL_READY != path:
        try:
            # 先读当前模式：库里已经是 WAL（写进库文件的持久属性）就不要重复设。
            # 多个进程同时首次打开时，并发执行 ``PRAGMA journal_mode=WAL`` 会在
            # Windows 上抢 -shm 映射，输的一方会拿到「attempt to write a readonly
            # database」。读一次再决定，把争抢窗口压到只有真正首次建库那一次。
            current = conn.execute("PRAGMA journal_mode").fetchone()
            if not current or str(current[0]).lower() != "wal":
                conn.execute("PRAGMA journal_mode=WAL")
            _WAL_READY = path
        except sqlite3.OperationalError:
            # 另一个进程正好在创建 WAL 文件，本连接暂时不可写；不记住状态，
            # 下次连接重来（调用方有 _retry 兜底）。
            pass
    conn.execute("PRAGMA foreign_keys=ON")
    # WAL + NORMAL 是 SQLite 官方推荐组合：fsync 从「每次提交」挪到 checkpoint，
    # 写锁持有区间明显缩短。最坏情况是断电丢最后一个事务（播放进度几秒），库不会损坏。
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def session(immediate: bool = False):
    """事务上下文。

    ``immediate=True`` 用 ``BEGIN IMMEDIATE`` 一开头就拿写锁，适用于
    「先读后写」的块：默认的 DEFERRED 事务是先读后升级为写，
    若此时别的进程已提交，SQLite 会抛 ``SQLITE_BUSY_SNAPSHOT``，
    而这个错误**不吃 busy_timeout**，会瞬间失败。守护进程的
    读-判断-写路径必须用 IMMEDIATE，否则偶发丢进度。
    """
    conn = get_conn()
    try:
        if immediate:
            conn.isolation_level = None
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """为已存在的旧库补列：只 ALTER ADD COLUMN，不重建表、不丢已有播放记录。"""
    additions = {
        "media_files": (
            ("title_cn", "TEXT"), ("match_by", "TEXT"), ("deleted_at", "TEXT"),
            ("partial_hash", "TEXT"),
            # 作品 ID（v0.1.6 P1-4）：老库靠这条 ALTER 补列，首次扫描惰性填充
            ("tmdb_id", "INTEGER"), ("tvdb_id", "INTEGER"),
            # 目录不可达时标「缺失」，只隐藏不删行、不动播放进度（P1-1）
            ("missing_since", "TEXT"),
            # 集数解析来源与可信度：low 的进「集数待确认」（OPTIMIZATION #6 尾巴）
            ("episode_source", "TEXT"), ("episode_confidence", "TEXT"),
        ),
        "subjects": (("tvdb_id", "INTEGER"),),
        "watched_list": (("match_by", "TEXT"),),
        "watch_progress": (
            ("dismissed_at", "TEXT"),
            ("total_watched_ms", "INTEGER NOT NULL DEFAULT 0"),
        ),
        "watch_sessions": (("watched_ms", "INTEGER NOT NULL DEFAULT 0"),),
    }
    for table, columns in additions.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


_INIT_LOCK = threading.Lock()
# 本进程累计写库次数（见 write_count）：只用于上层缓存失效判断，不参与业务逻辑。
_WRITE_COUNT = 0
_INITIALIZED = False


def _create_schema() -> None:
    """建表 + 迁移 + 写默认设置。幂等，可整体重放（供 busy 重试使用）。"""
    conn = get_conn()
    try:
        # executescript 内部会先提交挂起事务，所以建表放在显式事务之外；
        # CREATE TABLE IF NOT EXISTS 本身幂等，无需事务保护。
        conn.executescript(SCHEMA)
        # 迁移与默认设置放进同一个 IMMEDIATE 事务：两个进程同时冷启动时
        # 不会交叉执行 ALTER TABLE（DDL 冲突会让其中一方直接失败）。
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            _migrate(conn)
            for key, value in config.DEFAULTS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
                    (key, str(value)),
                )
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()


def init_db() -> None:
    """建表 + 迁移 + 写默认设置；同一进程只执行一次，避免界面刷新时反复写库。"""
    global _INITIALIZED
    if _INITIALIZED:
        return
    with _INIT_LOCK:
        if _INITIALIZED:
            return
        _retry(_create_schema)
        _INITIALIZED = True


def rows(sql: str, params: Sequence[Any] = ()) -> list[dict]:
    def run() -> list[dict]:
        with session() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    return _retry(run)


def row(sql: str, params: Sequence[Any] = ()) -> dict | None:
    def run() -> dict | None:
        with session() as conn:
            r = conn.execute(sql, params).fetchone()
            return dict(r) if r else None
    return _retry(run)


def execute(sql: str, params: Sequence[Any] = ()) -> int:
    global _WRITE_COUNT

    def run() -> int:
        with session() as conn:
            cur = conn.execute(sql, params)
            return cur.lastrowid or cur.rowcount
    result = _retry(run)
    _WRITE_COUNT += 1
    return result


def executemany(sql: str, seq: Iterable[Sequence[Any]]) -> int:
    global _WRITE_COUNT
    # 生成器只能消费一次，重试前必须先物化，否则第二次重试会插 0 行。
    payload = list(seq)

    def run() -> int:
        with session() as conn:
            cur = conn.executemany(sql, payload)
            return cur.rowcount
    result = _retry(run)
    _WRITE_COUNT += 1
    return result


def write_count() -> int:
    """本进程内的写库次数，供上层缓存判断「数据是否动过」。

    不能用数据库文件的 mtime：守护进程每 3 秒写一次播放进度，
    mtime 会一直变，缓存永远命中不了。这个计数只统计**同一进程**的写，
    所以上层还要叠加一次 media_files 指纹来覆盖守护进程 / 其他进程的改动。
    """
    return _WRITE_COUNT


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
    def run() -> Any:
        with session() as conn:
            r = conn.execute(sql, params).fetchone()
            return r[0] if r else None
    return _retry(run)


def is_busy(exc: BaseException) -> bool:
    """公开判定：这个异常是不是「锁竞争」而非真正的错误。

    上层（守护轮询）据此区分「偶发争锁，重试即可」和「真出事了」，
    避免并发问题被静默吞掉。
    """
    return _is_busy(exc)


def checkpoint(mode: str = "TRUNCATE") -> None:
    """把 WAL 内容合回主库并尽量收缩 ``-wal``。

    长读事务（备份导出）只推迟自动 checkpoint，``-wal`` 会持续膨胀，
    导出结束后主动收一次。失败不影响业务（下一轮自动 checkpoint 还会做）。
    """
    try:
        with session() as conn:
            conn.execute(f"PRAGMA wal_checkpoint({mode})")
    except sqlite3.Error:
        pass
