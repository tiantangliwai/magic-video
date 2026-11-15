"""黑/白展示风格：设置持久化、非法值防护、前端契约。"""
from __future__ import annotations

import unittest
from pathlib import Path

from tests.base import IsolatedDbTestCase

from app import db
from app.api import Api

ROOT = Path(__file__).resolve().parent.parent


class ThemeSettingTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_default_theme_is_light(self):
        self.assertEqual(self.api.get_app_info()["theme"], "light")

    def test_set_theme_persists_in_sqlite(self):
        self.assertEqual(self.api.set_theme("dark"), {"ok": True, "theme": "dark"})
        self.assertEqual(db.get_setting("theme"), "dark")
        self.assertEqual(Api().get_app_info()["theme"], "dark")  # 新 Api 实例仍读到

    def test_set_theme_round_trip(self):
        self.api.set_theme("dark")
        self.api.set_theme("light")
        self.assertEqual(self.api.get_app_info()["theme"], "light")

    @staticmethod
    def _raw(key):
        return db.scalar("SELECT value FROM settings WHERE key = ?", (key,))

    def test_invalid_theme_rejected_without_writing(self):
        # init_db 会以 DEFAULTS 预置 theme=light；非法输入不得覆盖它
        for bad in ("", "blue", "neon", None, 123):
            self.assertFalse(self.api.set_theme(bad)["ok"], bad)
        self.assertEqual(self._raw("theme"), "light")
        # 大小写/首尾空格做归一化容错，属于合法输入
        self.assertTrue(self.api.set_theme("  DARK ")["ok"])
        self.assertEqual(self._raw("theme"), "dark")

    def test_corrupted_setting_falls_back_to_light(self):
        db.set_setting("theme", "neon")
        self.assertEqual(self.api.get_app_info()["theme"], "light")

    def test_save_settings_ignores_theme_injection(self):
        # 主题只能走 set_theme，save_settings 的白名单不应顺手写它
        self.api.save_settings({"theme": "dark", "http_proxy": "http://127.0.0.1:1"})
        self.assertEqual(self._raw("theme"), "light")  # 仍是 DEFAULTS 预置值，没被注入
        self.assertEqual(db.get_setting("http_proxy"), "http://127.0.0.1:1")


class ThemeFrontendContractTests(unittest.TestCase):
    """静态守护：黑白主题必须落在 CSS 变量 + HTML 标记 + JS 切换三处。"""

    def test_css_defines_both_themes(self):
        css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn('html[data-theme="dark"]', css)
        self.assertIn('html[data-theme="light"]', css)
        self.assertIn("--bg:", css)
        self.assertIn(".nav-icon svg", css)

    def test_index_has_theme_button_and_svg_icons(self):
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-theme="light"', html)
        self.assertIn('data-action="toggle-theme"', html)
        self.assertEqual(html.count("<svg"), 12)  # 10 个原有视图 + 追剧列表 + 后台进程
        self.assertIn('data-view="posters"', html)
        self.assertIn('data-view="quicksearch"', html)
        self.assertIn('data-view="watchlist"', html)
        self.assertIn('data-view="processes"', html)
        self.assertNotIn("ic-home", html)

    def test_js_applies_and_persists_theme(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("function applyTheme", js)
        self.assertIn("setAttribute('data-theme'", js)
        self.assertIn("call('set_theme', next)", js)
        self.assertIn("applyTheme(info.theme)", js)


if __name__ == "__main__":
    unittest.main()
