"""观影报告：旧会话（watched_ms=0）的保守估算与「估」标记契约。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app import db
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


def _day(offset: int = 0) -> str:
    return (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")


class StatsEstimateTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()
        self.file_id = self.media("Frieren - 01.mkv", title_guess="Frieren",
                                  title_cn="葬送的芙莉莲", season_no=1, episode_no=1)["id"]

    def _session(self, *, watched_ms=0, from_ms=0, to_ms=0, max_ms=0,
                 started: str | None = None):
        started = started or (_day() + " 20:00:00")
        db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, ended_at, "
            "from_ms, to_ms, max_ms, watched_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.file_id, started, started, from_ms, to_ms, max_ms, watched_ms),
        )

    def test_legacy_session_uses_interval_estimate(self):
        """11-15 那批数据：watched_ms=0 但有真实区间，不能显示成 0。"""
        self._session(from_ms=1000, to_ms=1_201_000, max_ms=1_201_000)  # 20 分钟
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 20.0)
        self.assertEqual(data["totals"]["active_days"], 1)
        self.assertEqual(data["totals"]["estimated_sessions"], 1)
        self.assertEqual(data["daily"][-1]["estimated"], 1)
        self.assertEqual(data["by_work"][0]["estimated"], 1)
        self.assertIn("估算", data["note"])

    def test_seek_jump_is_conservative(self):
        """max 被拖进度条抬高、最后停在靠前位置 → 只算到 to_ms。"""
        self._session(from_ms=0, to_ms=60_000, max_ms=1_200_000)
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 1.0)
        self.assertEqual(data["totals"]["estimated_sessions"], 1)

    def test_short_session_stays_zero_without_estimate(self):
        self._session(from_ms=0, to_ms=30_000, max_ms=30_000)
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 0.0)
        self.assertEqual(data["totals"]["estimated_sessions"], 0)

    def test_exact_watched_ms_wins_over_interval(self):
        self._session(watched_ms=600_000, from_ms=0, to_ms=7_200_000, max_ms=7_200_000)
        data = self.api.get_watch_stats(7)
        self.assertEqual(data["totals"]["minutes"], 10.0)
        self.assertEqual(data["totals"]["estimated_sessions"], 0)
        self.assertEqual(data["daily"][-1]["estimated"], 0)

    def test_calendar_uses_same_estimate(self):
        self._session(from_ms=0, to_ms=1_800_000, max_ms=1_800_000)
        cal = self.api.get_watch_calendar(_day()[:7])
        self.assertEqual(cal["total_minutes"], 30.0)
        self.assertEqual(cal["estimated_sessions"], 1)
        self.assertEqual(cal["days"][-1]["estimated"], 1)

    def test_estimate_does_not_write_database(self):
        self._session(from_ms=0, to_ms=1_800_000, max_ms=1_800_000)
        self.api.get_watch_stats(7)
        self.assertEqual(
            db.scalar("SELECT SUM(watched_ms) FROM watch_sessions") or 0, 0,
            "估算只能发生在读取时，不能污染精确时长字段",
        )


class ReportNamingTests(unittest.TestCase):
    def test_navigation_and_heading_use_new_name(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("stats: ['观影报告'", js)
        self.assertIn("<h3>观影报告</h3>", js)
        self.assertIn("</svg></span>观影报告</button>", html)
        self.assertNotIn("看片统计", js)
        self.assertNotIn("看片统计", html)

    def test_frontend_marks_estimates(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("num(d.estimated)", js)
        self.assertIn("num(hit.estimated)", js)
        self.assertIn("num(w.estimated)", js)
        self.assertIn("num(cal.estimated_sessions)", js)


if __name__ == "__main__":
    unittest.main()
