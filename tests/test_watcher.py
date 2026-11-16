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


class ThresholdTests(IsolatedDbTestCase):
    """v0.1.6 完成判定四阈值：片头忽略 / 最低进度 / 片尾忽略。"""

    def setUp(self):
        super().setUp()
        self.a = self.media("Show - 01.mkv", title_guess="Show")
        self.watcher = Watcher()

    def _tick_with(self, value):
        with mock.patch.object(potplayer, "read_status", return_value=value), \
             mock.patch.object(potplayer, "resolve_media",
                               side_effect=lambda name: db.row(
                                   "SELECT * FROM media_files WHERE filename = ?", (name,))):
            self.watcher._tick()

    def _progress(self):
        return db.row("SELECT * FROM watch_progress WHERE file_id = ?", (self.a["id"],))

    def test_head_position_below_threshold_is_not_resumed(self):
        """默认片头 30 秒内不生成续播点（挡掉「点开几秒就有续播」）。"""
        self._tick_with(status(position=10_000))
        self.assertEqual(self._progress()["position_ms"], 0)
        self.assertEqual(self._progress()["completed"], 0)

    def test_custom_thresholds_can_be_disabled(self):
        """片头忽略与最低进度都设为 0 时，几秒的进度也照常记录。"""
        db.set_setting("ignore_seconds_at_start", 0)
        db.set_setting("min_resume_percent", 0)
        self._tick_with(status(position=10_000))
        self.assertEqual(self._progress()["position_ms"], 10_000)

    def test_tail_percent_marks_completed(self):
        """剩余片尾 ≤ ignore_percent_at_end 也判已看完（complete_percent 调高后独立生效）。"""
        db.set_setting("complete_percent", 99)
        self._tick_with(status(position=1_130_000))  # 94.2%，剩余 5.8% ≤ 8
        progress = self._progress()
        self.assertEqual(progress["completed"], 1)
        self.assertEqual(progress["position_ms"], 0)  # 完成时清续播点

    def test_rewatch_head_keeps_existing_resume_point(self):
        """已看到中途后重看片头，不应把已有续播点抹掉。"""
        self._tick_with(status(position=600_000))  # 50%
        self._tick_with(status(position=12_000))   # 回到片头 12 秒
        self.assertEqual(self._progress()["position_ms"], 600_000)


if __name__ == "__main__":
    import unittest
    unittest.main()
