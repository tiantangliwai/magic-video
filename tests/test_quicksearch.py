"""全盘快搜独立左侧入口：前端静态契约与「导航 ↔ 视图」一致性。

纯静态检查，不启动窗口、不连数据库、不发网络请求。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class QuickSearchViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_sidebar_has_dedicated_nav_item(self):
        self.assertRegex(self.html, r'data-view="quicksearch"[^>]*>')
        self.assertIn('>全盘快搜</button>', self.html)
        self.assertIn('class="nav-icon ni-search"', self.html)

    def test_nav_icon_has_light_and_dark_colors(self):
        self.assertIn(".ni-search {", self.css)
        self.assertIn('html[data-theme="dark"] .ni-search', self.css)

    def test_nav_views_match_view_meta_and_renderers(self):
        """加导航忘了加视图（或反过来）会直接被这条测试挡住。"""
        nav = set(re.findall(r'class="nav-item[^"]*"\s+data-view="([^"]+)"', self.html))
        meta_block = self.js.split("const VIEW_META = {", 1)[1].split("};", 1)[0]
        rend_block = self.js.split("const RENDERERS = {", 1)[1].split("};", 1)[0]
        meta = set(re.findall(r"^\s*([a-z]+):\s*\[", meta_block, re.M))
        rend = set(re.findall(r"([a-z]+):\s*render\w+", rend_block))
        self.assertIn("quicksearch", nav)
        self.assertEqual(nav, meta)
        self.assertEqual(nav, rend)

    def test_quicksearch_has_own_renderer(self):
        self.assertIn("async function renderQuickSearch()", self.js)
        self.assertIn('id="esSearch"', self.js)
        self.assertIn('id="esResults"', self.js)
        self.assertIn('id="esSourceBadge"', self.js)
        self.assertIn("call('everything_capabilities')", self.js)

    def test_posters_no_longer_embeds_search_card(self):
        """剧集墙只管封面，全盘快搜不能还留一份重复入口。"""
        block = self.js.split("async function renderPosters()", 1)[1].split(
            "// 全盘快搜是左侧独立入口", 1)[0]
        self.assertNotIn('id="esSearch"', block)
        self.assertNotIn('id="esResults"', block)
        self.assertNotIn("全盘快搜（Everything）", block)

    def test_search_bindings_still_work(self):
        self.assertIn('data-action="es-search"', self.js)
        self.assertIn("ev.target.id === 'esSearch'", self.js)
        self.assertIn("call('everything_search'", self.js)
        self.assertIn("data-action=\"play-external\"", self.js)

    def test_settings_button_goes_to_dedicated_view(self):
        self.assertIn('data-view="quicksearch">去全盘快搜</button>', self.js)
        self.assertNotIn("去全盘快搜 / 剧集墙", self.js)


if __name__ == "__main__":
    unittest.main()
