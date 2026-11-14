"""播放守护测试：会话生命周期、进度写入、完成判定、切集结算。"""
from __future__ import annotations

from unittest import mock

from app import db, potplayer
from app.watcher import Watcher
from tests.base import IsolatedDbTestCase


def status(media="Show - 01.mkv", *, position=60_000, duration=1_200_000,
           state="playing", available=True):
    pct = round(position / duration * 100, 2) if duration else 0.0
    return {
        "available": available, "media_name": media, "position_ms": position,
        "duration_ms": duration, "percent": pct, "state": state,
        "state_code": 2, "title": media + " - PotPlayer", "healthy": True,
    }


class WatcherFlowTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.a = self.media("Show - 01.mkv", title_guess="Show")
        self.b = self.media("Show - 02.mkv", title_guess="Show")
        self.watcher = Watcher(on_event=lambda kind, payload: self.events.append(kind))
        self.events: list[str] = []

    def _tick_with(self, value):
        with mock.patch.object(potplayer, "read_status", return_value=value), \
             mock.patch.object(potplayer, "resolve_media",
                               side_effect=lambda name: db.row(
                                   "SELECT * FROM media_files WHERE filename = ?", (name,))):
            self.watcher._tick()

    def test_first_tick_opens_session_and_progress(self):
        self._tick_with(status())
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)
        progress = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (self.a["id"],))
        self.assertEqual(progress["position_ms"], 60_000)
        self.assertEqual(progress["play_count"], 1)
        self.assertEqual(progress["completed"], 0)
        self.assertIn("session_started", self.events)

    def test_progress_updates_and_records_furthest_position(self):
        self._tick_with(status(position=60_000))
        self._tick_with(status(position=180_000))
        progress = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (self.a["id"],))
        self.assertEqual(progress["position_ms"], 180_000)
        self.assertAlmostEqual(progress["percent"], 15.0, places=1)
        session = db.row("SELECT * FROM watch_sessions")
        self.assertEqual(session["max_ms"], 180_000)

    def test_completed_threshold_marks_watched(self):
        self._tick_with(status(position=1_140_000))  # 95%
        progress = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (self.a["id"],))
        self.assertEqual(progress["completed"], 1)
        self.assertGreaterEqual(progress["percent"], 90)

    def test_completed_flag_does_not_regress_on_rewind(self):
        """已看完是历史事实，重看开头不应把它抹掉（需显式「标记未看」）。"""
        self._tick_with(status(position=1_140_000))
        self._tick_with(status(position=10_000))
        self.assertEqual(db.scalar("SELECT completed FROM watch_progress WHERE file_id = ?",
                                   (self.a["id"],)), 1)

    def test_switching_episode_closes_previous_session(self):
        self._tick_with(status())
        self._tick_with(status("Show - 02.mkv", position=30_000))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 2)
        first = db.row("SELECT * FROM watch_sessions ORDER BY id LIMIT 1")
        self.assertIsNotNone(first["ended_at"])
        self.assertEqual(first["file_id"], self.a["id"])
        self.assertIn("session_ended", self.events)

    def test_player_closed_closes_session(self):
        self._tick_with(status())
        self._tick_with(status(available=False))
        self.assertIsNone(self.watcher.current)
        session = db.row("SELECT * FROM watch_sessions")
        self.assertIsNotNone(session["ended_at"])

    def test_stop_closes_session_without_thread(self):
        self._tick_with(status())
        self.watcher.stop()
        self.assertIsNone(self.watcher.current)
        self.assertIn("stopped", self.events)

    def test_external_file_is_registered_and_matched_offline(self):
        """用 PotPlayer 直接打开库外文件也要留下历史，且即时查离线词典。"""
        with mock.patch.object(potplayer, "read_status",
                               return_value=status("Frieren - 12.mkv")), \
             mock.patch.object(potplayer, "resolve_media", return_value=None):
            self.watcher._tick()
        external = db.row("SELECT * FROM media_files WHERE status = 'external'")
        self.assertIsNotNone(external)
        self.assertEqual(external["title_cn"], "葬送的芙莉莲")
        self.assertEqual(external["episode_no"], 12)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

    def test_duration_is_backfilled_into_media_file(self):
        self.assertIsNone(self.a["duration_ms"])
        self._tick_with(status())
        self.assertEqual(db.scalar("SELECT duration_ms FROM media_files WHERE id = ?",
                                   (self.a["id"],)), 1_200_000)

    def test_emit_swallows_callback_errors(self):
        """界面回调抛错不能影响守护记录。"""
        def bad_callback(kind, payload):
            raise RuntimeError("UI callback failed")
        watcher = Watcher(on_event=bad_callback)
        watcher._emit("started")
        self.assertTrue(True)


if __name__ == "__main__":
    import unittest
    unittest.main()
