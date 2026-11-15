"""软件内嵌播放：坐标换算、Win32 嵌入/还原、全链路自动降级。

全部用 mock 的 user32，不启动真实的 PotPlayer，也不碰真实窗口。
"""
from __future__ import annotations

import ctypes
import os
import unittest
from ctypes import wintypes
from unittest import mock

from app import db, embedded_player as ep, potplayer
from tests.base import IsolatedDbTestCase


class RectFromCssTests(unittest.TestCase):
    def test_converts_css_px_by_dpr(self):
        self.assertEqual(ep.rect_from_css({"x": 100, "y": 74, "w": 800, "h": 450, "dpr": 1.5}),
                         (150, 111, 1200, 675))

    def test_accepts_bounding_client_rect_keys(self):
        self.assertEqual(ep.rect_from_css({"left": 10, "top": 20, "width": 300, "height": 200}, dpr=1),
                         (10, 20, 300, 200))

    def test_invalid_or_missing_input_falls_back(self):
        self.assertEqual(ep.rect_from_css(None), ep.DEFAULT_RECT)
        self.assertEqual(ep.rect_from_css("oops"), ep.DEFAULT_RECT)
        self.assertEqual(ep.rect_from_css({"x": "abc", "w": None}), ep.DEFAULT_RECT)

    def test_clamps_absurd_values(self):
        self.assertEqual(ep.rect_from_css({"x": -50, "y": -9, "w": 0, "h": 0}), (0, 0, 200, 150))
        self.assertEqual(ep.rect_from_css({"x": 99999, "y": 99999, "w": 99999, "h": 99999}),
                         (10000, 10000, 10000, 10000))
        # DPR 超出可信区间时按 1 处理
        self.assertEqual(ep.rect_from_css({"x": 4, "y": 6, "w": 400, "h": 300, "dpr": 99}),
                         (4, 6, 400, 300))


class FakeUser32:
    """可控的 user32 替身：记录调用、模拟 SetParent 成功/失败。"""

    def __init__(self, *, parent_after=100, setwindowpos_ok=1, style=0x10000000):
        self.u = mock.MagicMock()
        self.u.IsWindow.return_value = 1
        self.u.IsWindowVisible.return_value = 1
        self.u.GetWindowLongW.return_value = style
        self.u.SetWindowLongW.return_value = style
        self.u.GetParent.side_effect = [0, parent_after]
        self.u.SetParent.return_value = 0
        self.u.SetWindowPos.return_value = setwindowpos_ok
        self.u.GetWindowRect.return_value = 1
        self.u.ShowWindow.return_value = 1
        self.u.GetClassNameW.return_value = 0
        self.u.GetWindowTextW.return_value = 0

    def use(self):
        u = self.u
        return (
            mock.patch.object(ep, "_user32", u),
            mock.patch.object(ep, "HAS_WIN32", True),
        )


class AttachDetachTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeUser32()
        self.p1, self.p2 = self.fake.use()
        self.p1.start()
        self.p2.start()

    def tearDown(self):
        with mock.patch.object(ep, "_user32", self.fake.u), mock.patch.object(ep, "HAS_WIN32", True):
            ep.detach()
        potplayer.set_embedded_hwnd(0)
        ep._STATE.update(attached=False, pot_hwnd=0, main_hwnd=0, path="", reason="",
                         rect=ep.DEFAULT_RECT, orig_style=0, orig_parent=0, orig_rect=ep.DEFAULT_RECT)
        self.p2.stop()
        self.p1.stop()
        super().tearDown()

    def test_attach_reparents_and_persists_hwnd(self):
        r = ep.attach(777, 100, (10, 20, 800, 450))
        self.assertTrue(r["ok"])
        self.assertEqual(r["hwnd"], 777)
        self.fake.u.SetParent.assert_called_once_with(777, 100)
        self.fake.u.SetWindowPos.assert_called_once()
        self.assertEqual(self.fake.u.SetWindowPos.call_args[0][2:6], (10, 20, 800, 450))
        # WS_CHILD 置位、WS_POPUP 清除
        styled = self.fake.u.SetWindowLongW.call_args_list[0][0][2]
        self.assertTrue(styled & ep.WS_CHILD)
        self.assertFalse(styled & ep.WS_POPUP)
        self.assertEqual(potplayer.embedded_hwnd(), 777)
        self.assertEqual(db.get_setting(ep.EMBED_HWND_KEY), "777")

    def test_attach_rejects_invalid_handles(self):
        self.assertFalse(ep.attach(0, 100, ep.DEFAULT_RECT)["ok"])
        self.assertFalse(ep.attach(777, 0, ep.DEFAULT_RECT)["ok"])
        self.fake.u.SetParent.assert_not_called()

    def test_attach_fails_when_setparent_does_not_take_effect(self):
        self.fake.u.GetParent.side_effect = [0, 0]     # SetParent 后仍是 0 -> 失败
        r = ep.attach(777, 100, ep.DEFAULT_RECT)
        self.assertFalse(r["ok"])
        self.assertIn("SetParent", r["error"])
        self.assertEqual(potplayer.embedded_hwnd(), 0)
        self.assertEqual(db.get_setting(ep.EMBED_HWND_KEY), "0")

    def test_attach_fails_when_setwindowpos_fails(self):
        self.fake.u.SetWindowPos.return_value = 0
        r = ep.attach(777, 100, ep.DEFAULT_RECT)
        self.assertFalse(r["ok"])
        self.assertIn("SetWindowPos", r["error"])
        self.assertFalse(ep.status()["attached"])

    def test_attach_twice_reuses_window_and_only_moves_it(self):
        self.assertTrue(ep.attach(777, 100, (0, 0, 800, 450))["ok"])
        self.fake.u.SetParent.reset_mock()
        r = ep.attach(777, 100, (5, 6, 700, 400))
        self.assertTrue(r.get("reused"))
        self.fake.u.SetParent.assert_not_called()
        self.assertEqual(self.fake.u.SetWindowPos.call_args[0][2:6], (5, 6, 700, 400))

    def test_detach_restores_original_style_and_clears_cache(self):
        self.assertTrue(ep.attach(777, 100, ep.DEFAULT_RECT)["ok"])
        r = ep.detach()
        self.assertTrue(r["ok"])
        self.assertTrue(r["was_attached"])
        self.assertEqual(self.fake.u.SetParent.call_args_list[-1][0], (777, 0))
        self.assertEqual(potplayer.embedded_hwnd(), 0)
        self.assertEqual(db.get_setting(ep.EMBED_HWND_KEY), "0")
        self.assertFalse(ep.status()["attached"])

    def test_sync_requires_attached_window(self):
        r = ep.sync((1, 2, 300, 200))
        self.assertFalse(r["ok"])
        self.assertIn("没有内嵌", r["error"])

    def test_sync_repositions_attached_window(self):
        self.assertTrue(ep.attach(777, 100, ep.DEFAULT_RECT)["ok"])
        self.fake.u.SetWindowPos.reset_mock()
        r = ep.sync((30, 40, 640, 360))
        self.assertTrue(r["ok"])
        self.assertEqual(self.fake.u.SetWindowPos.call_args[0][2:6], (30, 40, 640, 360))

    def test_status_drops_state_when_player_window_closed(self):
        self.assertTrue(ep.attach(777, 100, ep.DEFAULT_RECT)["ok"])
        self.fake.u.IsWindow.return_value = 0
        snap = ep.status()
        self.assertFalse(snap["attached"])
        self.assertEqual(snap["hwnd"], 0)

    def test_cleanup_never_leaves_orphan_child(self):
        self.assertTrue(ep.attach(777, 100, ep.DEFAULT_RECT)["ok"])
        self.assertTrue(ep.cleanup()["was_attached"])
        self.assertFalse(ep.status()["attached"])


class FindMainWindowTests(IsolatedDbTestCase):
    def test_finds_own_visible_window_by_title(self):
        u = mock.MagicMock()
        u.IsWindowVisible.return_value = 1

        def pid_of(hwnd, out):
            # ctypes.byref(...) 传给 mock 的是 CArgObject，原始 DWORD 在 ._obj
            target = getattr(out, "_obj", out)
            target.value = os.getpid()
            return 1

        def title_of(hwnd, buf, size):
            buf.value = "Magic Video · 播放历史"
            return 1

        def enumerate_windows(callback, _lparam):
            callback(100, 0)      # 命中后回调返回 False
            return 1

        u.GetWindowThreadProcessId.side_effect = pid_of
        u.GetWindowTextW.side_effect = title_of
        u.EnumWindows.side_effect = enumerate_windows
        with mock.patch.object(ep, "_user32", u), mock.patch.object(ep, "HAS_WIN32", True):
            self.assertEqual(ep.find_main_window(), 100)

    def test_ignores_other_process_windows(self):
        u = mock.MagicMock()
        u.IsWindowVisible.return_value = 1

        def pid_of(hwnd, out):
            target = getattr(out, "_obj", out)
            target.value = os.getpid() + 1
            return 1

        def enumerate_windows(callback, _lparam):
            callback(101, 0)
            return 1

        u.GetWindowThreadProcessId.side_effect = pid_of
        u.EnumWindows.side_effect = enumerate_windows
        with mock.patch.object(ep, "_user32", u), mock.patch.object(ep, "HAS_WIN32", True):
            self.assertEqual(ep.find_main_window(), 0)

    def test_non_windows_returns_zero(self):
        with mock.patch.object(ep, "HAS_WIN32", False):
            self.assertEqual(ep.find_main_window(), 0)


class PlayFallbackTests(IsolatedDbTestCase):
    """核心契约：任何一步失败都要降级为外部播放，且播放本身照常开始。"""

    def setUp(self):
        super().setUp()
        potplayer.set_embedded_hwnd(0)

    def tearDown(self):
        with mock.patch.object(ep, "_user32", mock.MagicMock()), mock.patch.object(ep, "HAS_WIN32", True):
            ep.detach()
        potplayer.set_embedded_hwnd(0)
        super().tearDown()

    def _play(self, *, main=100, pot=777, attach_ok=True, potplay_ok=True, wait=0):
        patches = [
            mock.patch.object(potplayer, "play", return_value=(
                {"ok": True, "args": ["pot.exe", "a.mkv"]} if potplay_ok
                else {"ok": False, "error": "找不到 PotPlayer"})),
            mock.patch.object(ep, "find_main_window", return_value=main),
            mock.patch.object(potplayer, "find_window", return_value=pot),
            mock.patch.object(ep, "attach", return_value=(
                {"ok": True, "hwnd": pot, "rect": ep.DEFAULT_RECT} if attach_ok
                else {"ok": False, "error": "SetParent 未生效"})),
            mock.patch.object(ep, "detach", return_value={"ok": True}),
        ]
        for p in patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in reversed(patches)])
        return ep.play("a.mkv", 12000,
                       {"x": 100, "y": 74, "w": 800, "h": 450, "dpr": 1.5},
                       wait_window=wait)

    def test_success_embeds_with_physical_rect(self):
        r = self._play()
        self.assertTrue(r["ok"])
        self.assertTrue(r["embedded"])
        self.assertFalse(r["fallback"])
        self.assertEqual(r["position_ms"], 12000)
        self.assertEqual(ep.attach.call_args[0][2], (150, 111, 1200, 675))

    def test_no_main_window_falls_back_to_external(self):
        r = self._play(main=0)
        self.assertTrue(r["ok"])
        self.assertFalse(r["embedded"])
        self.assertTrue(r["fallback"])
        self.assertIn("主窗口", r["reason"])
        ep.attach.assert_not_called()

    def test_no_potplayer_window_falls_back_to_external(self):
        r = self._play(pot=0)
        self.assertFalse(r["embedded"])
        self.assertIn("PotPlayer 窗口", r["reason"])
        ep.attach.assert_not_called()

    def test_setparent_failure_falls_back_and_cleans_up(self):
        r = self._play(attach_ok=False)
        self.assertFalse(r["embedded"])
        self.assertIn("SetParent 未生效", r["reason"])
        ep.detach.assert_called()

    def test_potplayer_start_failure_is_not_masked(self):
        r = self._play(potplay_ok=False)
        self.assertFalse(r["ok"])
        self.assertIn("找不到 PotPlayer", r["error"])
        ep.find_main_window.assert_not_called()

    def test_non_windows_attach_refuses_but_play_still_works(self):
        with mock.patch.object(ep, "HAS_WIN32", False):
            r = ep.attach(777, 100, ep.DEFAULT_RECT)
        self.assertFalse(r["ok"])
        self.assertTrue(self._play(main=0)["fallback"])


if __name__ == "__main__":
    unittest.main()
