"""追剧列表：一键置顶 + 按钮对齐的静态契约。"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import api, db
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web" / "app.js"
STYLE_CSS = ROOT / "web" / "style.css"


class WatchlistTopTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = api.Api()

    def _add(self, title, downloaded, order):
        return db.execute(
            "INSERT INTO watchlist(title, downloaded, sort_order) VALUES (?, ?, ?)",
            (title, downloaded, order),
        )

    def _order(self, downloaded=1):
        return [
            r["title"] for r in db.rows(
                "SELECT title FROM watchlist WHERE downloaded = ? ORDER BY sort_order ASC, id ASC",
                (downloaded,),
            )
        ]

    def test_top_moves_item_to_first_in_same_column(self):
        self._add("A", 1, 1)
        self._add("B", 1, 2)
        cid = self._add("C", 1, 3)
        result = self.api.watchlist_top(cid)
        self.assertTrue(result["ok"])
        self.assertTrue(result["moved"])
        self.assertEqual(self._order(1), ["C", "A", "B"])

    def test_already_top_is_reported_not_moved(self):
        aid = self._add("A", 1, 1)
        self._add("B", 1, 2)
        result = self.api.watchlist_top(aid)
        self.assertTrue(result["ok"])
        self.assertFalse(result["moved"])
        self.assertTrue(result.get("already_top"))
        self.assertEqual(self._order(1), ["A", "B"])

    def test_other_column_is_untouched(self):
        self._add("D1", 0, 10)
        self._add("D2", 0, 11)
        self._add("A", 1, 1)
        bid = self._add("B", 1, 2)
        self.api.watchlist_top(bid)
        self.assertEqual(self._order(1), ["B", "A"])
        self.assertEqual(self._order(0), ["D1", "D2"], "未下载栏顺序不能被改")

    def test_repeated_top_keeps_working(self):
        ids = [self._add(name, 1, idx + 1) for idx, name in enumerate("ABC")]
        self.api.watchlist_top(ids[1])
        self.api.watchlist_top(ids[2])
        self.assertEqual(self._order(1), ["C", "B", "A"])

    def test_unknown_item_returns_error(self):
        result = self.api.watchlist_top(99999)
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])

    def test_top_does_not_change_downloaded_flag(self):
        aid = self._add("A", 1, 1)
        bid = self._add("B", 1, 2)
        self.api.watchlist_top(bid)
        row = db.row("SELECT downloaded FROM watchlist WHERE id = ?", (aid,))
        self.assertEqual(int(row["downloaded"]), 1)


class WatchlistAlignFrontendTests(unittest.TestCase):
    def test_top_button_uses_icon_class(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn('data-action="watchlist-top"', js)
        self.assertIn("btn-icon", js)
        self.assertIn("case 'watchlist-top'", js)

    def test_ops_css_aligns_and_wraps_buttons(self):
        css = STYLE_CSS.read_text(encoding="utf-8")
        self.assertIn(".wl-ops .btn-icon", css)
        self.assertIn("flex-wrap: wrap", css)
        self.assertIn("justify-content: flex-end", css)

    def test_hint_mentions_top_button(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("⇈ 一下移到同栏最上面", js)


if __name__ == "__main__":
    unittest.main()
