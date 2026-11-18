"""看完自动同步到「看过的列表」+ 完成判定的时长兜底（2026-11-18）。

背景：用户看完 `Kaichou wa Maid-sama!` 第 1、2 集，点「从播放记录同步」却看不到
这部作品。只读排查真实库得到两个独立原因：

1. 第 1 集实际播到 94.96%，但 PotPlayer 的 ``POT_GET_TOTAL_TIME`` 当时读回 0，
   ``percent`` 被写成 0 → 永远不满足完成阈值，这一集从来不算「看完」；
2. ``title_guess`` 是 ``Kaichou wa Maid-sama! 02``（集号没剥掉），同一部番每集
   标题都不一样，同步会按集拆成多条，而不是一条作品记录。

外加一条产品要求：不能总靠用户点按钮 → 守护侧「判定看完即同步」+ 打开列表时
静默兜底；同时给「用户删掉的自动同步条目」记墓碑，避免删了又自己长回来。
"""
from __future__ import annotations

import unittest
from unittest import mock

from app import db, importer, watcher
from tests.base import IsolatedDbTestCase


def _complete(file_id: int, duration_ms: int = 1453120) -> None:
    """把某个文件写成「已看完」。"""
    db.execute(
        "INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent, completed) "
        "VALUES (?, 0, ?, 100, 1) "
        "ON CONFLICT(file_id) DO UPDATE SET completed = 1, percent = 100",
        (file_id, duration_ms),
    )


class SyncAggregationTests(IsolatedDbTestCase):
    def test_episode_suffix_in_title_does_not_split_one_work(self):
        """title_guess 带集号时必须剥掉，两集只产生一条区间行。"""
        for ep in (1, 2):
            row = self.media(f"Maid - {ep:02d}.mkv",
                             title_guess=f"Kaichou wa Maid-sama! {ep:02d}", episode_no=ep)
            _complete(row["id"])
        result = importer.sync_watched_from_progress()
        self.assertEqual(result["added"], 1)
        rows = db.rows("SELECT title, title_cn, episode_from, episode_to FROM watched_list")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Kaichou wa Maid-sama!")
        self.assertEqual((rows[0]["episode_from"], rows[0]["episode_to"]), (1, 2))

    def test_title_with_embedded_number_is_not_stripped(self):
        """标题自带数字（魔王2099）不能被当成集号剥掉。"""
        row = self.media("Maou 2099 - 05.mkv", title_guess="魔王2099", episode_no=5)
        _complete(row["id"])
        importer.sync_watched_from_progress()
        self.assertEqual(db.scalar("SELECT title FROM watched_list"), "魔王2099")

    def test_only_titles_limits_scope(self):
        """守护侧只同步刚看完的那一部，不扫全部已完成记录。"""
        a = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        b = self.media("B - 01.mkv", title_guess="Work B", episode_no=1)
        _complete(a["id"])
        _complete(b["id"])
        result = importer.sync_watched_from_progress(
            only_titles=["Work A"], merge_duplicates=False)
        self.assertEqual(result["added"], 1)
        self.assertEqual([r["title"] for r in db.rows("SELECT title FROM watched_list")],
                         ["Work A"])

    def test_empty_only_titles_does_nothing(self):
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        _complete(row["id"])
        result = importer.sync_watched_from_progress(only_titles=[], merge_duplicates=False)
        self.assertEqual(result["added"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)


class TombstoneTests(IsolatedDbTestCase):
    def test_ignored_work_is_not_synced_back(self):
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        _complete(row["id"])
        importer.mark_sync_ignored("Work A", None)
        result = importer.sync_watched_from_progress()
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["ignored"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)

    def test_manual_import_clears_tombstone(self):
        """用户主动重新导入同名作品 → 解除「不要再同步」的标记。"""
        importer.mark_sync_ignored("Work A", None)
        self.assertIn(importer._sync_key("Work A", None), importer._sync_ignore_keys())
        importer.commit([{"title": "Work A", "title_cn": "Work A", "match_by": "native"}])
        self.assertNotIn(importer._sync_key("Work A", None), importer._sync_ignore_keys())
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        _complete(row["id"])
        result = importer.sync_watched_from_progress()
        # 手动那条是「整部」行，同步被它覆盖 → 不新增，也不再被墓碑拦
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["ignored"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_deleting_playback_row_writes_tombstone(self):
        """在看过的列表里删掉自动同步来的行 → 下次同步不能加回来。"""
        from app.api import Api

        api = Api()
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        _complete(row["id"])
        importer.sync_watched_from_progress()
        item = db.row("SELECT * FROM watched_list")
        self.assertEqual(item["source"], "playback")
        api.delete_watched(item["id"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)
        result = importer.sync_watched_from_progress()
        self.assertEqual(result["added"], 0)

    def test_deleting_manual_row_does_not_write_tombstone(self):
        """手动导入的行本来就是用户自己的，删掉不该影响以后自动同步同名作品。"""
        from app.api import Api

        api = Api()
        importer.commit([{"title": "Work A", "title_cn": "Work A", "match_by": "native"}])
        item = db.row("SELECT * FROM watched_list")
        api.delete_watched(item["id"])
        self.assertEqual(importer._sync_ignore_keys(), set())


class WatcherDurationFallbackTests(IsolatedDbTestCase):
    def _status(self, name: str, position: int, duration: int = 0) -> dict:
        return {
            "available": True, "hwnd": 1, "title": f"{name} - PotPlayer",
            "media_name": name, "position_ms": position, "duration_ms": duration,
            "state": "playing", "state_code": 1,
            "percent": round(position / duration * 100, 2) if duration else 0.0,
            "healthy": True,
        }

    def test_zero_total_time_still_marks_completed(self):
        """播放器读不到总时长（duration=0）时，用库里已存时长兜底判完成。"""
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1,
                         duration_ms=1000000)
        w = watcher.Watcher()
        with mock.patch.object(watcher.potplayer, "read_status",
                               return_value=self._status("A - 01.mkv", 950000)):
            w._tick()
        prog = db.row("SELECT completed, percent, duration_ms FROM watch_progress "
                      "WHERE file_id = ?", (row["id"],))
        self.assertEqual(prog["completed"], 1)
        self.assertGreaterEqual(prog["percent"], 90)
        # 兜底时长不能被 0 覆盖回库
        self.assertEqual(prog["duration_ms"], 1000000)

    def test_completion_triggers_auto_sync(self):
        """守护侧判定看完的那一次，作品必须已出现在看过的列表里（不需要点按钮）。"""
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1,
                         duration_ms=1000000)
        w = watcher.Watcher()
        with mock.patch.object(watcher.potplayer, "read_status",
                               return_value=self._status("A - 01.mkv", 950000)):
            w._tick()
        rows = db.rows("SELECT title, episode_from, episode_to, source FROM watched_list")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["title"], "Work A")
        self.assertEqual(rows[0]["source"], "playback")
        self.assertEqual((rows[0]["episode_from"], rows[0]["episode_to"]), (1, 1))

    def test_switch_off_keeps_manual_only(self):
        """关掉自动同步 → 判定完成照旧写进度，但不动看过的列表。"""
        db.set_setting("auto_sync_watched", "0")
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1,
                         duration_ms=1000000)
        w = watcher.Watcher()
        with mock.patch.object(watcher.potplayer, "read_status",
                               return_value=self._status("A - 01.mkv", 950000)):
            w._tick()
        self.assertEqual(
            db.scalar("SELECT completed FROM watch_progress WHERE file_id = ?", (row["id"],)), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)


class AutoSyncApiTests(IsolatedDbTestCase):
    def test_api_auto_sync_respects_switch(self):
        from app.api import Api

        api = Api()
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        _complete(row["id"])
        db.set_setting("auto_sync_watched", "0")
        result = api.sync_watched_auto()
        self.assertTrue(result.get("disabled"))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)

    def test_api_auto_sync_adds_and_throttles(self):
        from app import api as api_module

        api = api_module.Api()
        api_module._AUTO_SYNC_AT = 0.0
        row = self.media("A - 01.mkv", title_guess="Work A", episode_no=1)
        _complete(row["id"])
        first = api.sync_watched_auto()
        self.assertEqual(first["added"], 1)
        second = api.sync_watched_auto()
        self.assertTrue(second.get("throttled"))


if __name__ == "__main__":
    unittest.main()
