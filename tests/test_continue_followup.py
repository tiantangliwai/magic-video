"""「看完一集 → 继续观看接下一集」的取数契约。

完成判定与 watch_progress 的 completed/position_ms 不允许被改动：
这里只验证取数侧补集，以及所有会把补集挡掉的边界。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import db
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class ContinueFollowupTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def _finish(self, file_id: int, when: str = "2026-11-17 20:00:00") -> None:
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, percent, completed, last_played_at) "
            "VALUES (?, 0, 95, 1, ?)", (file_id, when),
        )

    def test_finished_episode_points_to_next(self):
        first = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        second = self.media("Show - 02.mkv", title_guess="Show", season_no=1, episode_no=2)["id"]
        self._finish(first)

        cont = self.api.get_continue_watching()
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont[0]["file_id"], second)
        self.assertEqual(cont[0]["from_episode"], 1)
        self.assertEqual(cont[0]["position_ms"], 0)
        self.assertTrue(cont[0]["after_completed"])
        # 完成判定原样保留，没有靠放宽 completed 来"留在列表里"
        self.assertEqual(db.scalar("SELECT completed FROM watch_progress WHERE file_id = ?", (first,)), 1)
        self.assertEqual(db.scalar("SELECT position_ms FROM watch_progress WHERE file_id = ?", (first,)), 0)

    def test_continue_row_points_to_next(self):
        first = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        second = self.media("Show - 02.mkv", title_guess="Show", season_no=1, episode_no=2)["id"]
        self._finish(first)
        items = self.api.get_continue_row(12)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["file_id"], second)
        self.assertTrue(items[0]["after_completed"])
        self.assertEqual(items[0]["work_title"], "Show")

    def test_last_episode_disappears(self):
        only = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        self._finish(only)
        self.assertEqual(self.api.get_continue_watching(), [])
        self.assertEqual(self.api.get_continue_row(12)["items"], [])

    def test_recycled_next_is_not_offered(self):
        first = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        second = self.media("Show - 02.mkv", title_guess="Show", season_no=1, episode_no=2)["id"]
        db.execute("UPDATE media_files SET deleted_at = ? WHERE id = ?", (db.now(), second))
        self._finish(first)
        self.assertEqual(self.api.get_continue_watching(), [])
        self.assertEqual(self.api.get_continue_row(12)["items"], [])

    def test_dismissed_completed_is_not_offered(self):
        first = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        self.media("Show - 02.mkv", title_guess="Show", season_no=1, episode_no=2)
        self._finish(first)
        db.execute("UPDATE watch_progress SET dismissed_at = ? WHERE file_id = ?", (db.now(), first))
        self.assertEqual(self.api.get_continue_watching(), [])
        self.assertEqual(self.api.get_continue_row(12)["items"], [])

    def test_in_progress_entry_wins_without_duplicate(self):
        first = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        second = self.media("Show - 02.mkv", title_guess="Show", season_no=1, episode_no=2)["id"]
        self._finish(first, "2026-11-16 20:00:00")
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, percent, completed, last_played_at) "
            "VALUES (?, 60000, 20, 0, '2026-11-17 21:00:00')", (second,),
        )
        cont = self.api.get_continue_watching()
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont[0]["file_id"], second)
        self.assertFalse(cont[0].get("after_completed"))

    def test_cross_season_next_episode(self):
        last_s1 = self.media("Show S01E02.mkv", title_guess="Show", season_no=1, episode_no=2)["id"]
        first_s2 = self.media("Show S02E01.mkv", title_guess="Show", season_no=2, episode_no=1)["id"]
        self._finish(last_s1)
        cont = self.api.get_continue_watching()
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont[0]["file_id"], first_s2)

    def test_latest_completed_decides_once(self):
        """第 1、2 集都看完时只按最新的第 2 集求下一集，不回退到第 1 集。"""
        first = self.media("Show - 01.mkv", title_guess="Show", season_no=1, episode_no=1)["id"]
        second = self.media("Show - 02.mkv", title_guess="Show", season_no=1, episode_no=2)["id"]
        self._finish(first, "2026-11-16 20:00:00")
        self._finish(second, "2026-11-17 20:00:00")
        self.assertEqual(self.api.get_continue_watching(), [])


class FollowupFrontendTests(unittest.TestCase):
    def test_cards_render_next_episode_badge(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("after_completed", js)
        self.assertIn("播放下一集", js)
        self.assertIn("接第", js)


if __name__ == "__main__":
    unittest.main()
