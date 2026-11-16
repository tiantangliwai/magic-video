"""并发写加固（P1-3）：多个进程同时写同一个库，不能丢更新、不能炸库。

真实场景是「主窗口 + 托盘守护 + 扫描 + 备份」四个独立进程写同一个 WAL 库。
WAL 只解决读写并发，写写冲突仍靠 busy handler 等待；等不到就抛
``database is locked``，上层此前只能静默失败。这里用**真实子进程**
验证重试退避、``BEGIN IMMEDIATE`` 与备份快照的效果。

子进程通过 ``MAGIC_VIDEO_DATA_DIR`` 指向同一个临时目录（config 原生支持），
绝不碰真实 ``D:/magicvideo``。
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from unittest import mock

from app import config, db
from tests.base import IsolatedDbTestCase

# 子进程脚本：只做单条 UPDATE 自增，不复用父进程的模块状态。
WORKER = r"""
import sys
from app import db

file_id = int(sys.argv[1])
count = int(sys.argv[2])
for _ in range(count):
    db.execute(
        "UPDATE watch_progress SET play_count = play_count + 1 WHERE file_id = ?",
        (file_id,),
    )
print("ok")
"""

# 子进程脚本：持写锁 1 秒后提交，用来验证别的进程会等而不是直接失败。
LOCKER = r"""
import sys, time
from app import db

conn = db.get_conn()
conn.isolation_level = None
conn.execute("BEGIN IMMEDIATE")
conn.execute(
    "UPDATE watch_progress SET play_count = play_count + 1 WHERE file_id = ?",
    (int(sys.argv[1]),),
)
time.sleep(1.0)
conn.execute("COMMIT")
conn.close()
print("ok")
"""


class ConcurrencyTest(IsolatedDbTestCase):
    """进程级并发写：不碰真实数据目录，全部在临时库里跑。"""

    def _env(self) -> dict:
        env = {**os.environ, "MAGIC_VIDEO_DATA_DIR": str(self.tmp)}
        # 避免外部 PYTHONPATH 把别的 app 包带进子进程
        env.pop("PYTHONPATH", None)
        return env

    def _spawn(self, script: str, file_id: int, count: int) -> subprocess.Popen:
        return subprocess.Popen(
            [sys.executable, "-c", script, str(file_id), str(count)],
            cwd=str(config.SOURCE_ROOT),
            env=self._env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _progress_row(self, file_id: int) -> dict:
        return db.row("SELECT * FROM watch_progress WHERE file_id = ?", (file_id,))

    def test_parallel_increments_do_not_lose_updates(self):
        """4 个进程各自增 40 次，最终必须是 160——丢一次都说明重试没兜住。"""
        record = self.media("并发自增.mkv")
        db.execute("INSERT INTO watch_progress(file_id, play_count) VALUES (?, 0)", (record["id"],))

        procs = [self._spawn(WORKER, record["id"], 40) for _ in range(4)]
        outputs = [p.communicate(timeout=180) for p in procs]
        for proc, (out, err) in zip(procs, outputs):
            self.assertEqual(proc.returncode, 0, f"子进程失败：{err}")

        self.assertEqual(self._progress_row(record["id"])["play_count"], 160)

    def test_writer_waits_for_short_exclusive_lock(self):
        """别人持写锁 1 秒时，本进程应在 busy_timeout 内等到，而不是报错。"""
        record = self.media("争锁.mkv")
        db.execute("INSERT INTO watch_progress(file_id, play_count) VALUES (?, 0)", (record["id"],))

        locker = self._spawn(LOCKER, record["id"], 1)
        try:
            time.sleep(0.3)  # 让子进程先拿到锁
            started = time.monotonic()
            db.execute(
                "UPDATE watch_progress SET play_count = play_count + 1 WHERE file_id = ?",
                (record["id"],),
            )
            elapsed = time.monotonic() - started
        finally:
            _, err = locker.communicate(timeout=60)
            self.assertEqual(locker.returncode, 0, f"持锁进程失败：{err}")

        # 确实等过锁（否则说明根本没并发上），但没等到超时
        self.assertGreater(elapsed, 0.2)
        self.assertEqual(self._progress_row(record["id"])["play_count"], 2)

    def test_database_integrity_after_parallel_writes(self):
        """并发写之后库必须完好：integrity_check = ok，且没有残留 -journal。"""
        record = self.media("完整性.mkv")
        db.execute("INSERT INTO watch_progress(file_id, play_count) VALUES (?, 0)", (record["id"],))
        procs = [self._spawn(WORKER, record["id"], 15) for _ in range(3)]
        for proc in procs:
            _, err = proc.communicate(timeout=120)
            self.assertEqual(proc.returncode, 0, f"子进程失败：{err}")

        self.assertEqual(db.scalar("PRAGMA integrity_check"), "ok")
        self.assertFalse((self.tmp / "library.db-journal").exists())

    def test_session_immediate_commits(self):
        """``session(immediate=True)`` 正常提交，写对自己可见。"""
        record = self.media("立即事务.mkv")
        with db.session(immediate=True) as conn:
            conn.execute(
                "INSERT INTO watch_progress(file_id, play_count) VALUES (?, 5)", (record["id"],))
        self.assertEqual(self._progress_row(record["id"])["play_count"], 5)

    def test_session_immediate_rolls_back_on_error(self):
        """异常必须整块回滚，不能留下半截数据。"""
        record = self.media("回滚.mkv")
        with self.assertRaises(RuntimeError):
            with db.session(immediate=True) as conn:
                conn.execute(
                    "INSERT INTO watch_progress(file_id, play_count) VALUES (?, 7)", (record["id"],))
                raise RuntimeError("boom")
        self.assertIsNone(self._progress_row(record["id"]))


class RetryTest(IsolatedDbTestCase):
    """重试策略本身：只兜锁竞争，不掩盖真错误。"""

    def test_retry_recovers_from_busy(self):
        attempts = {"n": 0}

        def flaky():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise sqlite3.OperationalError("database is locked")
            return "ok"

        with mock.patch.object(db.time, "sleep"):
            self.assertEqual(db._retry(flaky), "ok")
        self.assertEqual(attempts["n"], 3)

    def test_retry_gives_up_after_limit(self):
        attempts = {"n": 0}

        def always_busy():
            attempts["n"] += 1
            raise sqlite3.OperationalError("database is locked")

        with mock.patch.object(db.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                db._retry(always_busy, tries=3)
        self.assertEqual(attempts["n"], 3)

    def test_retry_passes_through_real_errors(self):
        def broken():
            raise sqlite3.OperationalError("no such table: nope")

        with mock.patch.object(db.time, "sleep"):
            with self.assertRaises(sqlite3.OperationalError):
                db._retry(broken)

    def test_is_busy_recognises_lock_errors(self):
        self.assertTrue(db.is_busy(sqlite3.OperationalError("database is locked")))
        self.assertTrue(db.is_busy(sqlite3.OperationalError("database table is busy")))
        self.assertFalse(db.is_busy(sqlite3.OperationalError("no such column: x")))
        self.assertFalse(db.is_busy(ValueError("locked")))

    def test_executemany_retry_keeps_generator_payload(self):
        """生成器只能消费一次：重试前必须物化，否则第二次会插 0 行。"""
        record = self.media("生成器.mkv")
        rows = ((record["id"], i, i) for i in range(3))
        real_retry = db._retry
        calls = {"n": 0}

        def retry_twice(fn, **kwargs):
            def wrapped():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise sqlite3.OperationalError("database is locked")
                return fn()
            return real_retry(wrapped, **kwargs)

        with mock.patch.object(db, "_retry", retry_twice):
            db.executemany(
                "INSERT INTO watch_sessions(file_id, started_at, from_ms, to_ms) "
                "VALUES (?, '2026-01-01 00:00:00', ?, ?)",
                rows,
            )
        self.assertEqual(
            db.scalar("SELECT COUNT(*) FROM watch_sessions WHERE file_id = ?", (record["id"],)), 3)
