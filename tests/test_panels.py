"""独立工具窗口（设置 / 扫描 / 导入）的生命周期契约。

这些窗口是 pywebview 原生窗口，能被拖动、拉出主窗口边界、与主窗口并行操作——
HTML 覆盖层做不到。本测试全程 mock webview.create_window，绝不启动真实 WebView。
"""
from __future__ import annotations

import unittest
from unittest import mock

from app import panels


class FakeWindow:
    def __init__(self, title):
        self.title = title
        self.shown = False
        self.destroyed = False
        self.hidden_calls = 0
        self.show_calls = 0
        self.events = mock.Mock()
        # events 需要支持 += 运算符
        self.events.closing = _FakeEvent()

    def show(self):
        self.shown = True
        self.show_calls += 1

    def hide(self):
        self.shown = False
        self.hidden_calls += 1

    def destroy(self):
        self.destroyed = True


class _FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self


class PanelUrlTests(unittest.TestCase):
    def test_url_has_theme_and_panel_query(self):
        with mock.patch("app.config.WEB_DIR", __import__("pathlib").Path("D:/x/web")):
            url = panels.panel_url("settings", "dark")
        self.assertIn("index.html", url)
        self.assertIn("?theme=dark", url)
        self.assertIn("panel=settings", url)

    def test_url_normalizes_bad_theme(self):
        with mock.patch("app.config.WEB_DIR", __import__("pathlib").Path("D:/x/web")):
            url = panels.panel_url("scan", "garbage")
        self.assertIn("theme=light", url)

    def test_titles_do_not_contain_main_window_name(self):
        # 标题含 "Magic Video" 会被 find_main_window / single_instance 误当主窗口
        for name, title in panels.PANEL_TITLES.items():
            self.assertNotIn("Magic Video", title, name)
            self.assertTrue(title.strip(), name)


class PanelLifecycleTests(unittest.TestCase):
    def setUp(self):
        panels.reset_for_tests()

    def tearDown(self):
        panels.close_all()
        panels.reset_for_tests()

    def _create(self, name="settings"):
        win = FakeWindow(panels.PANEL_TITLES[name])
        api = mock.Mock()
        with mock.patch.object(panels, "_make_api", return_value=api), \
             mock.patch("webview.create_window", return_value=win) as cw:
            created = panels.create_panel(name, "dark")
        return created, win, api, cw

    def test_create_registers_window_and_attaches_own_api(self):
        created, win, api, cw = self._create("scan")
        self.assertIs(created, win)
        self.assertIs(panels._WINDOWS["scan"], win)
        api.attach_window.assert_called_once_with(win)
        # hidden 预建：show/hide 不会卡在 shown 事件守卫
        self.assertTrue(cw.call_args.kwargs.get("hidden"))
        self.assertNotIn("Magic Video", cw.call_args.args[0])

    def test_create_is_idempotent(self):
        _, win, _, cw = self._create("settings")
        again, _, _, _ = self._create("settings")
        self.assertIs(again, win)
        self.assertEqual(cw.call_count, 1)

    def test_open_shows_existing_window(self):
        _, win, _, _ = self._create("import")
        win.show_calls = 0
        result = panels.open_panel("import")
        self.assertTrue(result["ok"])
        self.assertEqual(win.show_calls, 1)
        self.assertTrue(panels.is_open("import"))

    def test_close_hides_but_keeps_window(self):
        _, win, _, _ = self._create("settings")
        panels.open_panel("settings")
        result = panels.close_panel("settings")
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(win.hidden_calls, 1)
        self.assertFalse(win.destroyed)
        self.assertFalse(panels.is_open("settings"))
        # 再开还是同一个窗口对象（不重建 WebView）
        panels.open_panel("settings")
        self.assertIs(panels._WINDOWS["settings"], win)

    def test_native_x_is_intercepted_as_hide(self):
        _, win, _, _ = self._create("scan")
        handler = win.events.closing.handlers[0]
        panels._VISIBLE.add("scan")
        result = handler()  # 模拟用户点原生 X
        self.assertIs(result, False)          # 返回 False → WinForms 取消关闭
        self.assertGreaterEqual(win.hidden_calls, 1)
        self.assertFalse(win.destroyed)
        self.assertNotIn("scan", panels._VISIBLE)

    def test_close_all_destroys_even_with_closing_hook(self):
        _, win, _, _ = self._create("settings")
        panels.close_all()
        # close_all 置位 _shutting_down，closing 钩子放行，窗口真正销毁
        self.assertTrue(win.destroyed)
        self.assertEqual(panels._WINDOWS, {})
        self.assertFalse(panels.is_open("settings"))

    def test_unknown_panel_rejected(self):
        result = panels.open_panel("bogus")
        self.assertFalse(result["ok"])
        self.assertIn("未知", result["error"])

    def test_open_recreates_when_missing(self):
        # 单个面板建窗失败后，前端再点开应该兜底重建
        win = FakeWindow("设置")
        api = mock.Mock()
        with mock.patch.object(panels, "_make_api", return_value=api), \
             mock.patch("webview.create_window", return_value=win):
            result = panels.open_panel("settings", "light")
        self.assertTrue(result["ok"])
        self.assertIn("settings", panels._WINDOWS)


class ApiBridgeTests(unittest.TestCase):
    def test_api_open_close_panel_delegate(self):
        from app.api import Api
        api = object.__new__(Api)
        with mock.patch("app.api.panels.open_panel", return_value={"ok": True}) as op, \
             mock.patch("app.api.panels.close_panel", return_value={"ok": True}) as cp, \
             mock.patch.object(Api, "_theme", return_value="dark"):
            self.assertTrue(api.open_panel("settings")["ok"])
            op.assert_called_once_with("settings", "dark")
            self.assertTrue(api.close_panel("settings")["ok"])
            cp.assert_called_once_with("settings")


_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent
_APP_JS = (_ROOT / "web" / "app.js").read_text(encoding="utf-8")
_STYLE_CSS = (_ROOT / "web" / "style.css").read_text(encoding="utf-8")
_MAIN_PY = (_ROOT / "app" / "main.py").read_text(encoding="utf-8")


class PanelFrontendContractTests(unittest.TestCase):
    """前端 / 建窗的静态契约：读源码断言，不启动 WebView。"""

    def test_frontend_parses_panel_query(self):
        self.assertIn("new URLSearchParams(location.search)", _APP_JS)
        self.assertIn("const panelMode", _APP_JS)
        self.assertIn("PANEL_VIEWS = ['settings', 'scan', 'import']", _APP_JS)

    def test_switch_view_opens_panel_for_three_views(self):
        block = _APP_JS.split("function switchView(view, opts = {}) {", 1)[1].split(
            "const prev = currentView;", 1)[0]
        self.assertIn("PANEL_VIEWS.includes(view)", block)
        self.assertIn("call('open_panel', view)", block)
        self.assertIn("if (!panelMode)", block)

    def test_panel_init_skips_daemon_and_player(self):
        block = _APP_JS.split("async function init() {", 1)[1].split(
            "try {\n    const info = await call('get_app_info');", 1)[0]
        self.assertIn("if (panelMode)", block)
        self.assertIn("setupPanelChrome()", block)
        self.assertIn("await openSettingsDialog()", block)

    def test_panel_has_close_button_and_esc(self):
        self.assertIn("data-action=\"close-panel\"", _APP_JS)
        self.assertIn("case 'close-panel':", _APP_JS)
        self.assertIn("function closePanelWindow()", _APP_JS)
        esc = _APP_JS.split("document.addEventListener('keydown', (ev) => {\n  if (ev.key !== 'Escape') return;", 1)[1].split("});", 1)[0]
        self.assertIn("panelMode", esc)

    def test_save_settings_reopens_panel_dialog(self):
        block = _APP_JS.split("case 'save-settings': {", 1)[1].split("toast('设置已保存');", 1)[0]
        self.assertIn("if (panelMode)", block)
        self.assertIn("await openSettingsDialog()", block)
        self.assertIn("else {", block)

    def test_css_hides_main_chrome_and_fills_settings(self):
        self.assertIn("body.panel-mode .sidebar", _STYLE_CSS)
        self.assertIn("body.panel-mode .topbar", _STYLE_CSS)
        self.assertIn("body.panel-mode #settingsModal", _STYLE_CSS)
        self.assertIn("panel-toolbar", _STYLE_CSS)

    def test_main_prebuilds_panels_and_closes_on_exit(self):
        self.assertIn("panels.create_all", _MAIN_PY)
        self.assertIn("panels.close_all", _MAIN_PY)
        self.assertIn("window.events.closing", _MAIN_PY)


if __name__ == "__main__":
    unittest.main()
