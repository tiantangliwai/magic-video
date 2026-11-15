"""从播放记录同步：按作品合并成一条区间行，不再「每集一条」。"""
from __future__ import annotations

import unittest

from app import api, db
from tests.base import IsolatedDbTestCase


class SyncDedupeTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = api.Api()

    def _completed(self, filename, title, season=None, episode=None):
        item = self.media(
            filename, title_guess=title, season_no=season, episode_no=episode
        )
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, completed) VALUES (?, 60000, 1)",
            (item["id"],),
        )
        return item

    def _rows(self):
        return db.rows(
            "SELECT * FROM watched_list ORDER BY episode_from, id"
        )

    def test_twelve_episodes_become_one_range_row(self):
        for ep in range(1, 13):
            self._completed(f"Show - {ep:02d}.mkv", "Show", 1, ep)
        result = self.api.sync_watched_from_history()
        self.assertEqual(result["added"], 1)
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["episode_from"], rows[0]["episode_to"]), (1, 12))
        self.assertEqual(rows[0]["source"], "playback")

    def test_second_sync_is_idempotent(self):
        for ep in (1, 2, 3):
            self._completed(f"Show - {ep:02d}.mkv", "Show", 1, ep)
        self.api.sync_watched_from_history()
        again = self.api.sync_watched_from_history()
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["merged"], 0)
        self.assertEqual(len(self._rows()), 1)

    def test_existing_manual_range_is_extended_not_duplicated(self):
        db.execute(
            "INSERT INTO watched_list(title, title_cn, season_no, episode_from, episode_to, source) "
            "VALUES ('Show', 'Show', 1, 1, 3, 'manual')"
        )
        for ep in (2, 4):
            self._completed(f"Show - {ep:02d}.mkv", "Show", 1, ep)
        result = self.api.sync_watched_from_history()
        rows = self._rows()
        self.assertEqual(len(rows), 1, "同一作品同季只能有一行")
        self.assertEqual((rows[0]["episode_from"], rows[0]["episode_to"]), (1, 4))
        self.assertEqual(result["updated"], 1)

    def test_whole_series_null_range_covers_everything(self):
        db.execute(
            "INSERT INTO watched_list(title, title_cn, season_no, episode_from, episode_to, source) "
            "VALUES ('Show', 'Show', 1, NULL, NULL, 'manual')"
        )
        for ep in (1, 2, 3):
            self._completed(f"Show - {ep:02d}.mkv", "Show", 1, ep)
        self.api.sync_watched_from_history()
        self.assertEqual(len(self._rows()), 1)

    def test_old_per_episode_playback_rows_are_merged(self):
        for ep in range(1, 6):
            db.execute(
                "INSERT INTO watched_list(title, title_cn, season_no, episode_from, episode_to, source) "
                "VALUES ('Old', 'Old', 1, ?, ?, 'playback')",
                (ep, ep),
            )
        result = self.api.sync_watched_from_history()
        rows = self._rows()
        self.assertEqual(len(rows), 1, "旧版每集一条必须合并成一行")
        self.assertEqual(result["merged"], 4)
        self.assertEqual((rows[0]["episode_from"], rows[0]["episode_to"]), (1, 5))

    def test_merge_keeps_user_edited_row(self):
        db.execute(
            "INSERT INTO watched_list(title, title_cn, season_no, episode_from, episode_to, source, match_by) "
            "VALUES ('Old', '旧番中文名', 1, 1, 1, 'playback', 'user')"
        )
        db.execute(
            "INSERT INTO watched_list(title, title_cn, season_no, episode_from, episode_to, source, match_by) "
            "VALUES ('Old', 'Old', 1, 2, 2, 'playback', 'none')"
        )
        self.api.sync_watched_from_history()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title_cn"], "旧番中文名")
        self.assertEqual(rows[0]["match_by"], "user")

    def test_different_seasons_stay_separate(self):
        for ep in (1, 2):
            self._completed(f"Show S01 - {ep:02d}.mkv", "Show", 1, ep)
        for ep in (1, 2):
            self._completed(f"Show S02 - {ep:02d}.mkv", "Show", 2, ep)
        self.api.sync_watched_from_history()
        rows = self._rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["season_no"] for r in rows], [1, 2])

    def test_manual_rows_of_other_titles_are_untouched(self):
        db.execute(
            "INSERT INTO watched_list(title, title_cn, source) VALUES ('用户手动导入', '手动', 'manual')"
        )
        self._completed("Show - 01.mkv", "Show", 1, 1)
        self.api.sync_watched_from_history()
        titles = [r["title"] for r in self._rows()]
        self.assertIn("用户手动导入", titles)
        self.assertEqual(len(titles), 2)

    def test_episodeless_file_creates_null_range_row_once(self):
        self._completed("Show Movie.mkv", "Show Movie")
        self.api.sync_watched_from_history()
        self.api.sync_watched_from_history()
        rows = self._rows()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["episode_from"])
        self.assertIsNone(rows[0]["episode_to"])


if __name__ == "__main__":
    unittest.main()
