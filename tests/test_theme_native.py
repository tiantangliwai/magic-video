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


if __name__ == "__main__":
    unittest.main()
