"""原生窗口标题栏跟随主题：DWM 沉浸式深色、切换同步、全链路降级。

所有 Win32 调用都用 mock，不创建真实窗口，也不改系统 DWM 设置。
"""
from __future__ import annotations

import unittest
from unittest import mock

from app import embedded_player, theme_native
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


def _attr(c) -> int:
    """从 DwmSetWindowAttribute 的调用里取出属性编号。"""
    value = c.args[1]
    return int(getattr(value, "value", value))


def _payload(c) -> int:
    """byref(c_uint32) 传进来的是 CArgObject，值在 ._obj.value。"""
    value = c.args[2]
    return int(getattr(getattr(value, "_obj", value), "value", value))


class DwmApplyTests(unittest.TestCase):
    def setUp(self):
        self.dwm = mock.MagicMock()
        self.dwm.DwmSetWindowAttribute.return_value = 0
        self.p_dwm = mock.patch.object(theme_native, "_dwm", self.dwm)
        self.p_hwnd = mock.patch.object(embedded_player, "find_main_window", return_value=123)
        self.p_dwm.start()
        self.p_hwnd.start()

    def tearDown(self):
        self.p_hwnd.stop()
        self.p_dwm.stop()

    def _calls(self, attr):
        return [c for c in self.dwm.DwmSetWindowAttribute.call_args_list if _attr(c) == attr]

    def test_dark_enables_immersive_mode_and_theme_colors(self):
        r = theme_native.apply("dark")
        self.assertTrue(r["ok"])
        self.assertTrue(r["dark"])
        self.assertEqual(r["hwnd"], 123)
        dark_calls = self._calls(theme_native.DWMWA_USE_IMMERSIVE_DARK_MODE)
        self.assertEqual(len(dark_calls), 1)
        self.assertEqual(_payload(dark_calls[0]), 1)
        self.assertEqual(_payload(self._calls(theme_native.DWMWA_CAPTION_COLOR)[0]),
                         theme_native._DARK_CAPTION)
        self.assertEqual(_payload(self._calls(theme_native.DWMWA_TEXT_COLOR)[0]),
                         theme_native._DARK_TEXT)

    def test_light_disables_immersive_mode_and_resets_colors(self):
        r = theme_native.apply("light")
        self.assertTrue(r["ok"])
        self.assertFalse(r["dark"])
        self.assertEqual(_payload(self._calls(theme_native.DWMWA_USE_IMMERSIVE_DARK_MODE)[0]), 0)
        for attr in theme_native._COLOR_ATTRS:
            self.assertEqual(_payload(self._calls(attr)[0]), theme_native.DWMWA_COLOR_DEFAULT)

    def test_falls_back_to_legacy_attribute_on_older_windows(self):
        self.dwm.DwmSetWindowAttribute.side_effect = (
            lambda hwnd, attr, value, size: -1 if int(getattr(attr, "value", attr)) == 20 else 0
        )
        r = theme_native.apply("dark")
        self.assertTrue(r["ok"])
        self.assertTrue(self._calls(theme_native.DWMWA_USE_IMMERSIVE_DARK_MODE_OLD))

    def test_no_main_window_degrades_silently(self):
        embedded_player.find_main_window.return_value = 0
        r = theme_native.apply("dark")
        self.assertFalse(r["ok"])
        self.assertIn("主窗口", r["error"])
        self.dwm.DwmSetWindowAttribute.assert_not_called()

    def test_dwm_exception_does_not_escape(self):
        self.dwm.DwmSetWindowAttribute.side_effect = OSError("no dwm")
        r = theme_native.apply("dark")
        self.assertFalse(r["ok"])
        self.assertTrue(r["dark"])

    def test_non_windows_degrades_silently(self):
        with mock.patch("app.theme_native.sys.platform", "linux"):
            r = theme_native.apply("dark")
        self.assertFalse(r["ok"])
        self.assertIn("不支持", r["error"])
        self.dwm.DwmSetWindowAttribute.assert_not_called()

    def test_missing_dwm_dll_degrades_silently(self):
        with mock.patch.object(theme_native, "_dwm", None):
            r = theme_native.apply("dark")
        self.assertFalse(r["ok"])


class ApiThemeBridgeTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_set_theme_also_applies_native_titlebar(self):
        with mock.patch("app.api.theme_native.apply", return_value={"ok": True}) as apply:
            self.assertEqual(self.api.set_theme("dark"), {"ok": True, "theme": "dark"})
        apply.assert_called_once_with("dark")

    def test_sync_native_theme_uses_saved_setting(self):
        self.api.set_theme("dark")
        with mock.patch("app.api.theme_native.apply", return_value={"ok": True}) as apply:
            self.assertTrue(self.api.sync_native_theme()["ok"])
        apply.assert_called_once_with("dark")

    def test_native_failure_never_breaks_theme_switch(self):
        with mock.patch("app.api.theme_native.apply", side_effect=OSError("dwm down")):
            self.assertEqual(self.api.set_theme("dark"), {"ok": True, "theme": "dark"})
            self.assertEqual(self.api.sync_native_theme()["ok"], False)
        self.assertEqual(self.api.get_app_info()["theme"], "dark")


class ThemeFrontendWiringTests(unittest.TestCase):
    def test_css_sets_color_scheme_and_canvas_background(self):
        css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")
        self.assertIn("color-scheme: light", css)
        self.assertIn("color-scheme: dark", css)
        self.assertIn("html { background: var(--bg); }", css)
        self.assertIn("::-webkit-scrollbar-thumb", css)

    def test_js_syncs_native_theme_on_startup(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("call('sync_native_theme')", js)

    def test_api_declares_native_bridge(self):
        src = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        self.assertIn("def sync_native_theme", src)
        self.assertIn("theme_native.apply", src)
        self.assertTrue((ROOT / "app" / "theme_native.py").exists())


class WebviewTitleBarHookTests(unittest.TestCase):
    """pywebview 会按系统「应用模式」写标题栏，并在系统偏好变化时反复重设。

    用户系统浅色应用模式 + 软件深色时，那次重设会把标题栏刷白。钩子必须让它的
    判定跟随本软件主题，重设多少次都只会设回深色。
    """

    def setUp(self):
        import sys
        import types

        self.sys = sys
        fake = types.ModuleType("webview.platforms.winforms")

        class BrowserForm:
            def is_dark_theme(self):     # 模拟系统浅色应用模式（AppsUseLightTheme=1）
                return False

        class BrowserView:               # 与 pywebview 6.x 一致：BrowserForm 嵌在 BrowserView 里
            pass

        BrowserView.BrowserForm = BrowserForm
        fake.BrowserView = BrowserView
        self.fake = fake
        self.p_mod = mock.patch.dict(sys.modules, {"webview.platforms.winforms": fake})
        self.p_hooked = mock.patch.object(theme_native, "_HOOKED", False)
        self.p_mod.start()
        self.p_hooked.start()

    def tearDown(self):
        self.p_hooked.stop()
        self.p_mod.stop()

    def test_hook_overrides_system_light_app_mode(self):
        theme_native.set_theme_state("dark")
        self.assertTrue(theme_native.hook_webview_title_bar())
        # 系统是浅色，但 pywebview 重设时必须按软件主题给深色
        self.assertTrue(self.fake.BrowserView.BrowserForm().is_dark_theme())

    def test_hook_follows_theme_toggle(self):
        theme_native.set_theme_state("dark")
        theme_native.hook_webview_title_bar()
        form = self.fake.BrowserView.BrowserForm()
        self.assertTrue(form.is_dark_theme())
        theme_native.set_theme_state("light")
        self.assertFalse(form.is_dark_theme())

    def test_hook_supports_module_level_browser_form(self):
        """兼容把 BrowserForm 放模块顶层的版本。"""
        import types

        class TopLevelForm:
            def is_dark_theme(self):
                return False

        fake = types.ModuleType("webview.platforms.winforms")
        fake.BrowserForm = TopLevelForm
        with mock.patch.dict(self.sys.modules, {"webview.platforms.winforms": fake}), \
                mock.patch.object(theme_native, "_HOOKED", False):
            theme_native.set_theme_state("dark")
            self.assertTrue(theme_native.hook_webview_title_bar())
            self.assertTrue(TopLevelForm().is_dark_theme())

    def test_apply_updates_state_for_hook(self):
        """API 切主题走 apply()，钩子读到的状态必须跟着变，否则切浅色后又被改回深色。"""
        with mock.patch.object(theme_native, "_dwm", mock.MagicMock()), \
                mock.patch.object(embedded_player, "find_main_window", return_value=9):
            theme_native.apply("dark")
            self.assertTrue(theme_native.current_dark())
            theme_native.apply("light")
            self.assertFalse(theme_native.current_dark())

    def test_hook_waits_when_platform_not_loaded(self):
        """窗口还没建时平台模块未载入：不主动 import，只返回 False 待下次重试。"""
        saved = self.sys.modules.pop("webview.platforms.winforms", None)
        try:
            self.assertFalse(theme_native.hook_webview_title_bar())
        finally:
            self.sys.modules["webview.platforms.winforms"] = saved

    def test_hook_is_idempotent(self):
        theme_native.set_theme_state("dark")
        self.assertTrue(theme_native.hook_webview_title_bar())
        self.assertTrue(theme_native.hook_webview_title_bar())


class TitlebarHookWiringTests(unittest.TestCase):
    """建窗流程必须①先置位主题 ②装钩子 ③显示后再同步一次。"""

    def test_create_window_primes_theme_state(self):
        src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("theme_native.set_theme_state", src)
        self.assertIn("hook_webview_title_bar", src)
        self.assertIn("events.shown", src)


if __name__ == "__main__":
    unittest.main()
