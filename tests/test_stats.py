"""观影报告 / 继续观看行 / 按季海报墙 / 多版本 / 评分标签（v0.1.6 P2-1 ~ P2-4）。

全部在 ``IsolatedDbTestCase`` 的临时库上跑，不碰真实 ``D:\\magicvideo``。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app import db
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


def _day(offset: int) -> str:
    return (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")


class WatchStatsTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()
        self.file_id = self.media("Frieren - 01.mkv", title_guess="Frieren",
                                  title_cn="葬送的芙莉莲", season_no=1, episode_no=1)["id"]

    def _session(self, started: str, watched_ms: int, file_id: int | None = None):
        db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, ended_at, watched_ms) "
            "VALUES (?, ?, ?, ?)",
            (file_id or self.file_id, started, started, watched_ms),
        )

    def test_daily_series_covers_every_day_in_range(self):
        self._session(_day(1) + " 20:00:00", 1_800_000)
        data = self.api.get_watch_stats(7)
        self.assertTrue(data["ok"])
        self.assertEqual(len(data["daily"]), 7)
        # 按日期升序，最后一条是今天
        self.assertEqual(data["daily"][-1]["date"], _day(0))
        self.assertEqual(data["days"], 7)

    def test_minutes_sum_watched_ms_only(self):
        self._session(_day(0) + " 20:00:00", 600_000)   # 10 分钟
        self._session(_day(0) + " 21:00:00", 300_000)   # 5 分钟
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 15.0)
        self.assertEqual(data["totals"]["sessions"], 2)
        self.assertEqual(data["totals"]["active_days"], 1)
        self.assertEqual(data["totals"]["avg_minutes_per_active_day"], 15.0)

    def test_sessions_outside_range_are_ignored(self):
        self._session(_day(20) + " 20:00:00", 3_600_000)
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 0.0)
        self.assertEqual(data["totals"]["active_days"], 0)

    def test_by_work_aggregates_and_caps(self):
        self._session(_day(0) + " 20:00:00", 600_000)
        other = self.media("Other - 01.mkv", title_guess="Other", title_cn="另一部",
                           season_no=1, episode_no=1)["id"]
        self._session(_day(1) + " 20:00:00", 1_200_000, other)
        data = self.api.get_watch_stats(7)
        top = data["by_work"][0]
        self.assertEqual(top["title"], "另一部")
        self.assertEqual(top["minutes"], 20.0)
        self.assertEqual(data["totals"]["works"], 2)

    def test_blacklisted_work_is_not_counted(self):
        self.api.save_settings({"video_blacklist": "芙莉莲"})
        self._session(_day(0) + " 20:00:00", 600_000)
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 0.0)

    def test_completion_rate_uses_progress_table(self):
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, percent, completed, updated_at) "
            "VALUES (?, 0, 100, 1, ?)", (self.file_id, db.now()))
        data = self.api.get_watch_stats(30)
        self.assertEqual(data["completion"]["files"], 1)
        self.assertEqual(data["completion"]["completed"], 1)
        self.assertEqual(data["completion"]["rate"], 100.0)

    def test_note_documents_the_measurement_rule(self):
        """统计口径必须随数据一起返回，界面直接展示，避免「时长怎么算的」反复问。"""
        note = self.api.get_watch_stats(7)["note"]
        self.assertIn("快进", note)
        self.assertIn("重看", note)


class ContinueRowTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_one_entry_per_work_keeps_latest(self):
        first = self.media("Frieren - 01.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                           season_no=1, episode_no=1)["id"]
        second = self.media("Frieren - 02.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                            season_no=1, episode_no=2)["id"]
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, last_played_at) "
                   "VALUES (?, 60000, 20, '2026-11-10 20:00:00')", (first,))
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, last_played_at) "
                   "VALUES (?, 90000, 30, '2026-11-16 20:00:00')", (second,))
        items = self.api.get_continue_row(12)["items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["file_id"], second)

    def test_completed_dismissed_and_deleted_are_excluded(self):
        done = self.media("A - 01.mkv", title_guess="A", season_no=1, episode_no=1)["id"]
        gone = self.media("B - 01.mkv", title_guess="B", season_no=1, episode_no=1)["id"]
        db.execute("INSERT INTO watch_progress(file_id, position_ms, completed) VALUES (?, 100, 1)",
                   (done,))
        db.execute("INSERT INTO watch_progress(file_id, position_ms, dismissed_at) "
                   "VALUES (?, 100, ?)", (gone, db.now()))
        self.assertEqual(self.api.get_continue_row(12)["items"], [])

    def test_limit_is_clamped(self):
        for i in range(3):
            fid = self.media(f"Show{i} - 01.mkv", title_guess=f"Show{i}",
                             season_no=1, episode_no=1)["id"]
            db.execute("INSERT INTO watch_progress(file_id, position_ms, last_played_at) "
                       "VALUES (?, 1000, ?)", (fid, db.now()))
        self.assertEqual(len(self.api.get_continue_row(2)["items"]), 2)


class SeasonWallTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()
        for season in (1, 2):
            for ep in (1, 2):
                self.media(f"Frieren S{season:02d}E{ep:02d}.mkv", title_guess="Frieren",
                           title_cn="葬送的芙莉莲", season_no=season, episode_no=ep)

    def test_season_cards_split_per_season(self):
        data = self.api.get_poster_wall({"limit": 10})
        self.assertEqual(len(data["cards"]), 1, "按作品只有一张卡")
        self.assertEqual(len(data["season_cards"]), 2, "按季应拆成两张")
        seasons = sorted(c["season_no"] for c in data["season_cards"])
        self.assertEqual(seasons, [1, 2])
        for card in data["season_cards"]:
            self.assertEqual(card["file_count"], 2)
            self.assertEqual(card["work_key"], data["cards"][0]["work_key"],
                             "季卡片的评分/标签挂在作品上，key 必须一致")

    def test_season_cards_carry_watch_state(self):
        data = self.api.get_poster_wall({"limit": 10})
        self.assertTrue(all(c["watch_state"] in
                            ("done", "partial", "watching", "listed", "none")
                            for c in data["season_cards"]))


class VersionMergeTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_same_episode_files_merge_to_one_entry(self):
        self.media("Frieren - 01 BD.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                   season_no=1, episode_no=1, size=900)
        self.media("Frieren - 01 WEB.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                   season_no=1, episode_no=1, size=500)
        self.media("Frieren - 02.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                   season_no=1, episode_no=2, size=500)
        tree = self.api.get_media_tree({"mode": "series"})
        group = tree["groups"][0]
        self.assertEqual(group["file_count"], 2, "两集就是 2，不是 3 个文件")
        season = group["children"][0]
        files = season["files"]
        self.assertEqual(len(files), 2)
        merged = [f for f in files if f.get("version_count")]
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["version_count"], 2)
        self.assertEqual(len(merged[0]["versions"]), 2)
        # 主条目取体积大的（BD 那份），点播默认落到它
        self.assertIn("BD", merged[0]["filename"])

    def test_no_version_key_for_single_file_episode(self):
        self.media("Frieren - 01.mkv", title_guess="Frieren", season_no=1, episode_no=1)
        tree = self.api.get_media_tree({"mode": "series"})
        files = tree["groups"][0]["children"][0]["files"]
        self.assertIsNone(files[0].get("version_count"))

    def test_flat_page_exposes_versions_for_picker(self):
        self.media("Frieren - 01 BD.mkv", title_guess="Frieren", season_no=1, episode_no=1, size=900)
        self.media("Frieren - 01 WEB.mkv", title_guess="Frieren", season_no=1, episode_no=1, size=500)
        rows = self.api.get_media_files_page({"limit": 10})["rows"]
        self.assertTrue(all(len(r.get("versions") or []) == 2 for r in rows),
                        "平铺列表里同一集的两行都要带版本列表，才能下拉切换")

    def test_get_versions_lists_siblings(self):
        first = self.media("Frieren - 01 BD.mkv", title_guess="Frieren",
                           season_no=1, episode_no=1)["id"]
        self.media("Frieren - 01 WEB.mkv", title_guess="Frieren", season_no=1, episode_no=1)
        versions = self.api.get_versions(first)["versions"]
        self.assertEqual(len(versions), 2)
        self.assertTrue(any(v["current"] for v in versions))

    def test_get_versions_rejects_unknown_file(self):
        self.assertFalse(self.api.get_versions(999999)["ok"])


class WorkMetaTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_save_and_read_back(self):
        saved = self.api.save_work_meta("series::葬送的芙莉莲", 9, "神作, 已追完")
        self.assertTrue(saved["ok"], saved)
        self.assertEqual(saved["tags"], "神作,已追完", "中文逗号也要归一化")
        items = self.api.get_work_meta(["series::葬送的芙莉莲"])["items"]
        self.assertEqual(items["series::葬送的芙莉莲"]["rating"], 9)

    def test_upsert_overwrites_same_key(self):
        self.api.save_work_meta("k", 5, "a")
        self.api.save_work_meta("k", 8, "b")
        self.assertEqual(self.api.get_work_meta(["k"])["items"]["k"]["rating"], 8)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM work_meta"), 1)

    def test_rating_is_validated(self):
        self.assertFalse(self.api.save_work_meta("k", 11)["ok"])
        self.assertFalse(self.api.save_work_meta("k", -1)["ok"])
        self.assertFalse(self.api.save_work_meta("k", "很高")["ok"])
        self.assertFalse(self.api.save_work_meta("", 5)["ok"])

    def test_blank_rating_clears_it(self):
        self.api.save_work_meta("k", 8, "x")
        self.api.save_work_meta("k", "", "")
        row = db.row("SELECT rating, tags FROM work_meta WHERE work_key = 'k'")
        self.assertIsNone(row["rating"])
        self.assertEqual(row["tags"], "")

    def test_meta_survives_media_deletion(self):
        """标注按作品键挂靠，与 media_files 无外键，删文件不该丢评分。"""
        rec = self.media("Frieren - 01.mkv", title_guess="Frieren")["id"]
        self.api.save_work_meta("series::Frieren", 7, "好看")
        db.execute("UPDATE media_files SET deleted_at = ? WHERE id = ?", (db.now(), rec))
        self.assertEqual(self.api.get_work_meta(["series::Frieren"])["items"]["series::Frieren"]["rating"], 7)


class CalendarTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()
        self.file_id = self.media("Frieren - 01.mkv", title_guess="Frieren",
                                  title_cn="葬送的芙莉莲")["id"]

    def test_month_groups_by_day(self):
        month = datetime.now().strftime("%Y-%m")
        db.execute("INSERT INTO watch_sessions(file_id, started_at, watched_ms) VALUES (?, ?, ?)",
                   (self.file_id, f"{month}-01 20:00:00", 1_200_000))
        db.execute("INSERT INTO watch_sessions(file_id, started_at, watched_ms) VALUES (?, ?, ?)",
                   (self.file_id, f"{month}-01 21:00:00", 600_000))
        data = self.api.get_watch_calendar(month)
        self.assertEqual(data["month"], month)
        self.assertEqual(len(data["days"]), 1)
        self.assertEqual(data["days"][0]["day"], "01")
        self.assertEqual(data["days"][0]["minutes"], 30.0)
        self.assertEqual(data["days"][0]["top_title"], "葬送的芙莉莲")

    def test_bad_month_falls_back_to_current(self):
        data = self.api.get_watch_calendar("不是月份")
        self.assertEqual(data["month"], datetime.now().strftime("%Y-%m"))

    def test_other_month_is_empty(self):
        data = self.api.get_watch_calendar("1999-01")
        self.assertEqual(data["days"], [])
        self.assertEqual(data["total_minutes"], 0.0)


class CalendarLayoutContractTests(unittest.TestCase):
    """观看日历布局契约：窗口拉宽后日历要铺满整行，不再只占左侧 520px。

    用户 2026-11-19 反馈「观影报告的观看日历拉长后只占左边一部分」。
    根因是 .cal-grid 的 max-width:520px 把网格钉死在左侧。纯 CSS 静态契约，
    守护这几个关键声明，防止回归。
    """

    @classmethod
    def setUpClass(cls):
        cls.css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")

    def test_grid_fills_full_width_without_old_cap(self):
        # 网格改为 width:100% 铺满，旧的 520px 上限必须移除
        self.assertIn(".cal-grid { display: grid;", self.css)
        self.assertIn("width: 100%; }", self.css)
        self.assertNotIn("max-width: 520px", self.css)

    def test_grid_uses_minmax_track(self):
        # minmax(0,1fr) 防止格子内容把列撑破，宽屏下七等分铺满
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr))", self.css)

    def test_cell_uses_fixed_height_and_no_aspect_ratio(self):
        # 格子用固定最小高度、宽度随列拉伸；不能再有 aspect-ratio:1，
        # 否则宽屏下格子被锁成小方块稀疏排开（探测实测 88×88 散在 1306px 里）
        self.assertIn(".cal-cell { position: relative; min-height: 66px;", self.css)
        grid_block = self.css.split(".cal-cell {", 1)[1].split("}", 1)[0]
        self.assertNotIn("aspect-ratio", grid_block)


if __name__ == "__main__":
    unittest.main()
