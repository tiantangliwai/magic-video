"""继续观看软删除 + 历史记录按作品只显示最新一条的回归契约。

铁律：观看记录绝不自动删除。软删除只 UPDATE watch_progress.dismissed_at；
历史去重视图收拢，watch_sessions 里旧会话原样保留。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import db
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class ContinueDismissTests(IsolatedDbTestCase):

    def media_with_progress(self, name, *, position=60_000):
        record = self.media(name)
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent, "
            "completed, last_played_at) VALUES (?, ?, 200000, 30.0, 0, ?)",
            (record["id"], position, db.now()),
        )
        return record["id"]

    def test_schema_has_dismissed_column_and_migration_registers_it(self):
        cols = {r["name"] for r in db.rows("PRAGMA table_info(watch_progress)")}
        self.assertIn("dismissed_at", cols)
        source = (ROOT / "app" / "db.py").read_text(encoding="utf-8")
        # 断言迁移已登记 dismissed_at（不依赖元组排列的具体书写格式）
        self.assertIn('"dismissed_at", "TEXT"', source)
        self.assertIn('"watch_progress":', source)

    def test_dismiss_hides_only_from_continue_watching(self):
        fid = self.media_with_progress("Show - 01.mkv")
        self.assertEqual(len(Api().get_continue_watching()), 1)
        result = Api().dismiss_continue_watching(fid)
        self.assertTrue(result["ok"])
        self.assertTrue(result["dismissed"])
        self.assertEqual(Api().get_continue_watching(), [])
        # 进度、会话、文件都在；这只是隐藏，不是删除
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), 1)
        self.assertEqual(db.scalar("SELECT position_ms FROM watch_progress WHERE file_id = ?", (fid,)), 60_000)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 1)

    def test_restore_brings_it_back(self):
        fid = self.media_with_progress("Show - 01.mkv")
        api = Api()
        api.dismiss_continue_watching(fid)
        restored = api.dismiss_continue_watching(fid, restore=True)
        self.assertTrue(restored["ok"])
        self.assertFalse(restored["dismissed"])
        self.assertEqual(len(api.get_continue_watching()), 1)

    def test_dismiss_is_idempotent_and_rejects_unknown_id(self):
        fid = self.media_with_progress("Show - 01.mkv")
        api = Api()
        self.assertTrue(api.dismiss_continue_watching(fid)["ok"])
        self.assertTrue(api.dismiss_continue_watching(fid)["ok"])
        self.assertIsNotNone(
            db.scalar("SELECT dismissed_at FROM watch_progress WHERE file_id = ?", (fid,)))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), 1)
        self.assertFalse(api.dismiss_continue_watching(987654)["ok"])
        self.assertFalse(api.dismiss_continue_watching("abc")["ok"])

    def test_replaying_clears_dismiss_flag(self):
        fid = self.media_with_progress("Show - 01.mkv")
        (self.tmp / "Show - 01.mkv").write_bytes(b"video")
        api = Api()
        api.dismiss_continue_watching(fid)
        self.assertEqual(api.get_continue_watching(), [])
        with mock.patch("app.api.potplayer.play", return_value={"ok": True}) as play:
            result = api.play_file(fid)
        self.assertTrue(result["ok"])
        self.assertTrue(play.called)
        self.assertIsNone(db.scalar("SELECT dismissed_at FROM watch_progress WHERE file_id = ?", (fid,)))
        self.assertEqual(len(api.get_continue_watching()), 1)

    def test_completed_list_is_not_affected_by_dismiss(self):
        fid = self.media("Show - 01.mkv")["id"]
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, completed, last_played_at) "
            "VALUES (?, 200000, 1, ?)", (fid, db.now()),
        )
        api = Api()
        api.dismiss_continue_watching(fid)
        self.assertEqual(len(api.get_completed()), 1)


class HistoryDedupTests(IsolatedDbTestCase):

    def session(self, file_id, started, *, max_ms=1000):
        return db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, ended_at, from_ms, to_ms, max_ms) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (file_id, started, started, max_ms, max_ms),
        )

    def test_same_file_shows_only_latest_session(self):
        record = self.media("Frieren - 01.mkv")
        self.session(record["id"], "2026-11-01 10:00:00", max_ms=1000)
        self.session(record["id"], "2026-11-02 10:00:00", max_ms=2000)
        latest = self.session(record["id"], "2026-11-03 10:00:00", max_ms=3000)
        history = Api().get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], latest)
        self.assertEqual(history[0]["max_ms"], 3000)
        # 旧会话一条不少
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 3)

    def test_different_works_each_keep_one_row(self):
        a = self.media("A - 01.mkv", title_cn="作品甲")["id"]
        b = self.media("B - 01.mkv", title_cn="作品乙")["id"]
        self.session(a, "2026-11-01 10:00:00")
        self.session(a, "2026-11-02 10:00:00")
        self.session(b, "2026-11-03 10:00:00")
        ids = {r["file_id"] for r in Api().get_history()}
        self.assertEqual(ids, {a, b})

    def test_same_work_different_episodes_keep_only_latest(self):
        """同一部剧的不同集（不同 file_id）只显示最新一条，旧会话不删库。"""
        ep3 = self.media("Show - 03.mkv", title_cn="同一部剧")["id"]
        ep5 = self.media("Show - 05.mkv", title_cn="同一部剧")["id"]
        ep7 = self.media("Show - 07.mkv", title_cn="同一部剧")["id"]
        self.session(ep3, "2026-11-01 10:00:00")
        self.session(ep5, "2026-11-02 10:00:00")
        latest = self.session(ep7, "2026-11-03 10:00:00")
        history = Api().get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["id"], latest)
        self.assertEqual(history[0]["file_id"], ep7)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 3)

    def test_episodes_with_no_title_fall_back_to_file_key(self):
        """没有作品标题的文件（外部/未刮削）按 file_id 各自保留，不被合并。"""
        a = self.media("A - 01.mkv", title_guess=None)["id"]
        b = self.media("B - 01.mkv", title_guess=None)["id"]
        self.session(a, "2026-11-01 10:00:00")
        self.session(b, "2026-11-02 10:00:00")
        self.assertEqual({r["file_id"] for r in Api().get_history()}, {a, b})

    def test_external_sessions_without_file_id_are_not_merged(self):
        self.session(None, "2026-11-01 10:00:00")
        self.session(None, "2026-11-02 10:00:00")
        self.assertEqual(len(Api().get_history()), 2)

    def test_manual_delete_still_works_on_deduplicated_history(self):
        record = self.media("Frieren - 01.mkv")
        old = self.session(record["id"], "2026-11-01 10:00:00")
        self.session(record["id"], "2026-11-02 10:00:00")
        api = Api()
        removed = api.delete_history_session(old)
        self.assertTrue(removed["ok"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)
        self.assertEqual(len(api.get_history()), 1)

    def test_no_new_delete_from_watch_sessions_in_api(self):
        source = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("DELETE FROM watch_sessions"), 1)


class FrontendContractTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_dismiss_button_and_handler_exist(self):
        self.assertIn('data-action="dismiss-continue"', self.js)
        self.assertIn("case 'dismiss-continue'", self.js)
        self.assertIn("'dismiss_continue_watching'", self.js)

    def test_hints_explain_soft_delete_and_history_dedup(self):
        self.assertIn("软删除", self.js)
        self.assertIn("每部只显示", self.js)


if __name__ == "__main__":
    unittest.main()
