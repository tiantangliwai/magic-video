"""黑/白展示风格：设置持久化、非法值防护、前端契约。"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from tests.base import IsolatedDbTestCase

from app import db
from app import main as app_main
from app.api import Api
from app.main import startup_url

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
        # 每个导航项都带一个内联 SVG 图标（新增视图时必须同步加图标，否则这里会红）：
        # 侧栏 11 个浏览页 + 标题下拉里 3 个工具入口 + 下拉箭头 1 个 = 15
        self.assertEqual(html.count("<svg"), 15)
        sidebar = html.split('<aside class="sidebar">', 1)[1].split('</aside>', 1)[0]
        self.assertEqual(sidebar.count("<svg"), 11)
        self.assertIn('data-view="posters"', html)
        self.assertIn('data-view="stats"', html)
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
        # 首帧主题不能是硬编码 light，否则按钮图标会跟 CSS 首屏不一致
        self.assertNotIn("let currentTheme = 'light';", js)
        self.assertIn("currentTheme = document.documentElement.getAttribute('data-theme')", js)

    def test_index_presets_theme_before_stylesheet(self):
        """首屏必须在 CSS 加载前同步定下 data-theme，避免暗色用户先看到白表头。"""
        html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn("URLSearchParams(location.search).get('theme')", html)
        preset = html.index("setAttribute('data-theme'")
        stylesheet = html.index('rel="stylesheet"')
        self.assertLess(preset, stylesheet, "主题预设必须早于样式表加载")

    def test_startup_url_injects_persisted_theme(self):
        self.assertTrue(startup_url({"theme": "dark"}).endswith("?theme=dark"))
        self.assertTrue(startup_url({"theme": "light"}).endswith("?theme=light"))
        # 缺值与非法值都回退 light，和 Api._theme() 口径一致
        self.assertTrue(startup_url({}).endswith("?theme=light"))
        self.assertTrue(startup_url({"theme": "bogus"}).endswith("?theme=light"))

    def test_startup_url_is_local_path_not_file_uri(self):
        """首屏必须是本地路径 + query，不能是 file:// URI（v0.1.8 ERR_FILE_NOT_FOUND 回归）。

        pywebview 6.2.1 只对本地路径启动内置 HTTP server 解析 ?theme=；file:// URI 会被
        原样交给 WebView2，路径含空格 (%20) 且带 query 时会报 ERR_FILE_NOT_FOUND。
        """
        url = startup_url({"theme": "dark"})
        self.assertFalse(url.startswith("file://"), url)
        self.assertTrue(url.startswith(str(ROOT)), url)
        self.assertTrue(url.endswith("index.html?theme=dark"), url)
        # web_index 必须在源码/包内真实存在，否则路径拼对了也白搭
        self.assertTrue((ROOT / "web" / "index.html").is_file())


class NativeTitlebarTimingTests(unittest.TestCase):
    """原生标题栏由 DWM 绘制，必须在窗口显示前同步，否则暗色用户第一帧看到白条。

    页面 JS 里的 sync_native_theme 要等 WebView 加载完 + IPC 往返，那时窗口早已
    显示过白标题栏；正确时机是 pywebview 的 before_show（Form.Show() 之前）。
    """

    def test_before_show_applies_theme_while_window_still_hidden(self):
        with mock.patch.object(app_main.theme_native, "apply") as apply:
            app_main.sync_native_theme_before_show({"theme": "dark"})
        apply.assert_called_once_with("dark", require_visible=False, retries=2)

    def test_before_show_falls_back_to_light(self):
        for settings in ({}, {"theme": "bogus"}, {"theme": None}, None):
            with mock.patch.object(app_main.theme_native, "apply") as apply:
                app_main.sync_native_theme_before_show(settings)
            apply.assert_called_once_with("light", require_visible=False, retries=2)

    def test_before_show_never_breaks_window(self):
        """DWM 不支持 / 句柄找不到时只降级，绝不能让窗口显示失败。"""
        with mock.patch.object(app_main.theme_native, "apply", side_effect=RuntimeError("boom")):
            app_main.sync_native_theme_before_show({"theme": "dark"})

    def test_create_window_hooks_before_show(self):
        src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("events.before_show", src)
        self.assertIn("sync_native_theme_before_show", src)

    def test_find_main_window_accepts_hidden(self):
        """before_show 时窗口还没 Show，枚举必须允许未显示窗口。"""
        from app import embedded_player
        params = inspect.signature(embedded_player.find_main_window).parameters
        self.assertIn("require_visible", params)
        self.assertIs(params["require_visible"].default, True)

    def test_theme_native_apply_passes_visibility_through(self):
        """apply 必须把 require_visible / retries 透传给窗口枚举。"""
        from app import theme_native
        params = inspect.signature(theme_native.apply).parameters
        self.assertIn("require_visible", params)
        self.assertIn("retries", params)


if __name__ == "__main__":
    unittest.main()
