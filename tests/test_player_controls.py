"""播放控制条回归契约：快捷键注入、倍速档位、上一集/下一集与前端静态契约。

PotPlayer 是闭源软件，没有公开的运行时控制 API；本模块通过把按键消息投递到
播放器窗口实现控制。所有 Win32 调用一律 mock，测试绝不启动真实播放器。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import db, potplayer
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class KeyBindingTests(IsolatedDbTestCase):

    def test_default_bindings_are_all_resolvable(self):
        for action in potplayer.DEFAULT_KEY_BINDINGS:
            self.assertIn(potplayer.binding(action), potplayer.VK_CODES, action)

    def test_setting_overrides_binding(self):
        db.set_setting("pot_key_faster", "P")
        self.assertEqual(potplayer.binding("faster"), "P")
        self.assertEqual(potplayer.KEY_SETTINGS["faster"], "pot_key_faster")

    def test_invalid_binding_falls_back_to_default(self):
        db.set_setting("pot_key_faster", "NOT_A_KEY")
        self.assertEqual(potplayer.binding("faster"), "C")

    def test_unknown_action_is_empty(self):
        self.assertEqual(potplayer.binding("self_destruct"), "")

    def test_speed_step_only_accepts_known_values(self):
        # 默认 0.25：1.25 / 1.5 / 2.0 这三个预设才能精确命中（0.1 步进到不了 1.25）
        self.assertEqual(potplayer.speed_step(), 0.25)
        for value in (0.1, 0.5):
            db.set_setting("pot_speed_step", value)
            self.assertEqual(potplayer.speed_step(), value)
        db.set_setting("pot_speed_step", 0.03)
        self.assertEqual(potplayer.speed_step(), 0.25)


class SendKeyTests(IsolatedDbTestCase):
    """Win32 全 mock：只验证消息码、虚拟键与失败分支。"""

    def setUp(self):
        super().setUp()
        self.fake_user32 = mock.Mock()
        self.fake_user32.MapVirtualKeyW.return_value = 30
        self.fake_user32.PostMessageW.return_value = 1
        self.u32 = mock.patch.object(potplayer, "_user32", self.fake_user32)
        self.u32.start()
        self.addCleanup(self.u32.stop)
        self.window = mock.patch.object(potplayer, "find_window", return_value=123)
        self.window.start()
        self.addCleanup(self.window.stop)

    def test_virtual_key_channel_is_preferred(self):
        """优先用 POT_SEND_VIRTUAL_KEY：不需要窗口焦点，也不受自定义键位影响。"""
        result = potplayer.send_key("play_pause")
        self.assertTrue(result["ok"])
        self.assertEqual(result["key"], "SPACE")
        self.assertEqual(result["hwnd"], 123)
        self.assertEqual(result["via"], "virtual-key")
        calls = self.fake_user32.PostMessageW.call_args_list
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].args[0], 123)
        self.assertEqual(calls[0].args[1], potplayer.WM_USER)
        self.assertEqual(calls[0].args[2], potplayer.POT_SEND_VIRTUAL_KEY)
        self.assertEqual(calls[0].args[3], 0x20)      # wParam 命令码 / lParam 虚拟键

    def test_falls_back_to_keydown_when_virtual_key_refused(self):
        """老版本不认 0x5010 时退回 WM_KEYDOWN + WM_KEYUP，行为保持可用。"""
        self.fake_user32.PostMessageW.side_effect = [0, 1, 1]
        result = potplayer.send_key("play_pause")
        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "wm-keydown")
        calls = self.fake_user32.PostMessageW.call_args_list
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[1].args[1], potplayer.WM_KEYDOWN)
        self.assertEqual(calls[1].args[2], 0x20)
        self.assertEqual(calls[2].args[1], potplayer.WM_KEYUP)
        self.assertGreaterEqual(calls[2].args[3], potplayer._KEYUP_BITS)

    def test_unknown_action_is_rejected(self):
        result = potplayer.send_key("nope")
        self.assertFalse(result["ok"])
        self.fake_user32.PostMessageW.assert_not_called()

    def test_no_window_returns_clear_error(self):
        with mock.patch.object(potplayer, "find_window", return_value=0):
            result = potplayer.send_key("stop")
        self.assertFalse(result["ok"])
        self.assertIn("未运行", result["error"])

    def test_non_windows_platform_degrades_safely(self):
        with mock.patch.object(potplayer, "_user32", None):
            result = potplayer.send_key("stop")
        self.assertFalse(result["ok"])
        self.assertIn("不支持", result["error"])

    def test_post_failure_is_reported_not_swallowed(self):
        self.fake_user32.PostMessageW.return_value = 0
        result = potplayer.send_key("fullscreen")
        self.assertFalse(result["ok"])
        self.assertIn("投递失败", result["error"])

    def test_exception_in_user32_is_caught(self):
        self.fake_user32.PostMessageW.side_effect = OSError("boom")
        self.assertFalse(potplayer.send_key("fullscreen")["ok"])


class SpeedTests(IsolatedDbTestCase):

    def test_presets_are_limited(self):
        self.assertEqual(potplayer.SPEED_PRESETS, (1.0, 1.25, 1.5, 2.0))

    def test_step_025_hits_all_presets_exactly(self):
        db.set_setting("pot_speed_step", 0.25)
        expected = {1.0: (0, 1.0, True), 1.25: (1, 1.25, True),
                    1.5: (2, 1.5, True), 2.0: (4, 2.0, True)}
        for wanted, (presses, actual, exact) in expected.items():
            plan = potplayer.speed_plan(wanted)
            self.assertEqual((plan["presses"], plan["actual"], plan["exact"]),
                             (presses, actual, exact), wanted)

    def test_step_01_reports_inexact_125_without_pretending(self):
        """用户显式选了 0.1 步进时，界面必须如实报「到不了 1.25」。"""
        db.set_setting("pot_speed_step", 0.1)
        plan = potplayer.speed_plan(1.25)
        self.assertFalse(plan["exact"])
        self.assertEqual(plan["actual"], 1.2)
        self.assertEqual(plan["presses"], 2)

    def test_default_step_reaches_all_presets(self):
        """默认 0.25 之后，四个预设全部精确命中（P2-5）。"""
        self.assertEqual(potplayer.speed_step(), 0.25)
        for wanted in potplayer.SPEED_PRESETS:
            self.assertTrue(potplayer.speed_plan(wanted)["exact"], wanted)

    def test_invalid_targets_are_rejected(self):
        for value in ("abc", 0, -1, 99):
            self.assertFalse(potplayer.speed_plan(value)["ok"], value)

    def test_apply_speed_resets_then_presses_faster(self):
        with mock.patch.object(potplayer, "send_key", return_value={"ok": True}) as send:
            result = potplayer.apply_speed(1.5)
        self.assertTrue(result["ok"])
        actions = [c.args[0] for c in send.call_args_list]
        self.assertEqual(actions[0], "reset_speed")
        self.assertEqual(actions[1:], ["faster"] * result["presses"])

    def test_apply_speed_aborts_on_failed_reset(self):
        with mock.patch.object(potplayer, "send_key",
                               return_value={"ok": False, "error": "未运行"}) as send:
            result = potplayer.apply_speed(2.0)
        self.assertFalse(result["ok"])
        self.assertEqual(send.call_count, 1)


class PlayerApiTests(IsolatedDbTestCase):

    def test_control_action_is_whitelisted(self):
        api = Api()
        # 播放类动作现在走 WM_COMMAND 指令通道（potplayer.control），不再投快捷键
        with mock.patch.object(potplayer, "control", return_value={"ok": True}) as ctl:
            self.assertTrue(api.player_control("play_pause")["ok"])
            self.assertFalse(api.player_control("arbitrary_key")["ok"])
        self.assertEqual(ctl.call_count, 1)

    def test_speed_is_whitelisted(self):
        api = Api()
        with mock.patch.object(potplayer, "apply_speed", return_value={"ok": True}) as apply:
            self.assertTrue(api.player_speed(1.5)["ok"])
            self.assertTrue(api.player_speed("1.25")["ok"])
            self.assertFalse(api.player_speed(1.3)["ok"])
            self.assertFalse(api.player_speed("fast")["ok"])
        self.assertEqual(apply.call_count, 2)

    def test_bindings_payload_exposes_only_control_settings(self):
        payload = Api().player_bindings()
        self.assertTrue(payload["ok"])
        self.assertEqual(set(payload["keys"]), set(potplayer.DEFAULT_KEY_BINDINGS))
        self.assertIn(1.25, payload["presets"])
        self.assertIn("SPACE", payload["key_choices"])

    def test_settings_whitelist_accepts_control_keys(self):
        api = Api()
        with mock.patch.object(api, "stop_daemon", return_value={"ok": True}), \
             mock.patch.object(api, "start_daemon", return_value={"ok": True}):
            api.save_settings({"pot_key_faster": "P", "pot_speed_step": 0.25, "unknown_key": 1})
        self.assertEqual(potplayer.binding("faster"), "P")
        self.assertEqual(potplayer.speed_step(), 0.25)
        self.assertIsNone(db.row("SELECT value FROM settings WHERE key = 'unknown_key'"))

    def test_adjacent_episode_walks_series_in_order(self):
        api = Api()
        first = self.media("Show E01.mkv", title_guess="My Show", episode_no=1)
        second = self.media("Show E02.mkv", title_guess="My Show", episode_no=2)
        third = self.media("Show E03.mkv", title_guess="My Show", episode_no=3)
        self.assertEqual(api.player_adjacent(first["id"], "next")["file_id"], second["id"])
        self.assertEqual(api.player_adjacent(second["id"], "next")["file_id"], third["id"])
        self.assertEqual(api.player_adjacent(third["id"], "prev")["file_id"], second["id"])
        self.assertFalse(api.player_adjacent(first["id"], "prev")["ok"])
        self.assertFalse(api.player_adjacent(third["id"], "next")["ok"])

    def test_adjacent_rejects_unknown_file(self):
        self.assertFalse(Api().player_adjacent(987654, "next")["ok"])


class FrontendContractTests(unittest.TestCase):
    """播放控制条是共用 HTML/CSS/JS 的静态契约，改动必须同步三个文件。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")

    def test_all_control_buttons_exist(self):
        for action in ("player-play-pause", "player-stop", "player-prev",
                       "player-next", "player-fullscreen"):
            self.assertIn(f'data-action="{action}"', self.html, action)

    def test_speed_dropdown_has_required_presets(self):
        for value in ("1", "1.25", "1.5", "2"):
            self.assertIn(f'<option value="{value}">', self.html, value)
        self.assertIn("id=\"playerSpeed\"", self.html)

    def test_controls_are_outside_native_window_rect(self):
        # 控制条必须是 #playerHost 之外的兄弟节点，否则会被原生子窗口盖住
        self.assertLess(self.html.index('id="playerHost"'),
                        self.html.index('class="player-controls"'))
        self.assertIn(".player-controls {", self.css)
        self.assertIn(".player-controls-side.right", self.css)
        self.assertIn(".player-stage.stage-zoom", self.css)

    def test_player_buttons_have_comfortable_hit_area(self):
        """两套控制条的按钮点击区都要够大（用户 2026-11-18 反馈原本太小）。"""
        # 顶栏符号键：36×36，倍速下拉同高
        self.assertIn(".player-bar .pbtn { width: 36px; height: 36px;", self.css)
        self.assertIn(".player-bar .pbar-speed { width: 84px !important; height: 36px;", self.css)
        # 内嵌文字键：控制条 64 高、按钮 40 高，倍速同步
        self.assertIn(".player-controls { height: 64px;", self.css)
        self.assertIn(".player-controls .btn { min-height: 40px;", self.css)
        self.assertIn(".player-speed { width: auto; min-width: 108px; min-height: 40px;", self.css)

    def test_js_wires_controls_and_settings(self):
        for needle in ("function playerControl(", "function playerAdjacent(",
                       "function setPlayerSpeed(", "function toggleStageZoom(",
                       "case 'player-play-pause'", "case 'player-fullscreen'",
                       "el.id === 'playerSpeed'", "'pot_key_faster'", "'pot_speed_step'"):
            self.assertIn(needle, self.js, needle)


class ControlCommandTests(IsolatedDbTestCase):
    """WM_COMMAND 指令通道：这是「顶栏按钮点了没反应」的修复点。

    旧实现只投键盘消息，用户自定义过键位、或播放器内嵌成子窗口时就静默失败；
    现在走 PotPlayer 的菜单命令号，并用播放状态复查是否真的生效。
    """

    def setUp(self):
        super().setUp()
        self.fake_user32 = mock.Mock()
        self.fake_user32.PostMessageW.return_value = 1
        for patch in (mock.patch.object(potplayer, "_user32", self.fake_user32),
                      mock.patch.object(potplayer, "find_window", return_value=123)):
            patch.start()
            self.addCleanup(patch.stop)

    def _states(self, *values):
        seq = list(values)
        return mock.patch.object(
            potplayer, "play_status",
            side_effect=lambda hwnd: seq.pop(0) if seq else values[-1])

    def test_play_pause_sends_wm_command_and_verifies(self):
        # 第一次读是「播放中(2)」，命令送达后变「暂停(1)」
        with self._states(2, 1):
            result = potplayer.control("play_pause")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["via"], "command:10014")
        self.assertEqual(result["state"], 1)
        self.assertEqual(self.fake_user32.PostMessageW.call_args.args,
                         (123, potplayer.WM_COMMAND, 10014, 0))

    def test_falls_back_to_play_status_message_then_reports_via(self):
        # WM_COMMAND 被拒（返回 0）→ 退到 POT_SET_PLAY_STATUS 并生效
        self.fake_user32.PostMessageW.side_effect = [0, 1]
        with self._states(2, 2, 2, 1):
            result = potplayer.control("pause")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["via"], "status:1")

    def test_reports_failure_when_state_never_changes(self):
        """三条通道都发出去但状态没变，要如实报错而不是假装成功。"""
        with mock.patch.object(potplayer, "play_status", return_value=2), \
                mock.patch.object(potplayer, "send_key", return_value={"ok": True, "key": "SPACE"}):
            result = potplayer.control("stop")
        self.assertFalse(result["ok"])
        self.assertIn("状态没变", result["error"])

    def test_already_in_target_state_is_ok(self):
        with mock.patch.object(potplayer, "play_status", return_value=-1):
            result = potplayer.control("stop")
        self.assertTrue(result["ok"])
        self.assertEqual(result["via"], "already")

    def test_no_player_gives_clear_error(self):
        with mock.patch.object(potplayer, "find_window", return_value=0):
            result = potplayer.control("play_pause")
        self.assertFalse(result["ok"])
        self.assertIn("未运行", result["error"])

    def test_unknown_action_rejected_without_posting(self):
        result = potplayer.control("self_destruct")
        self.assertFalse(result["ok"])
        self.fake_user32.PostMessageW.assert_not_called()

    def test_api_whitelist_covers_playback_actions_only(self):
        api = Api()
        self.assertFalse(api.player_control("prev")["ok"])      # 上下集由前端换片，不走这里
        self.assertFalse(api.player_control("rm -rf")["ok"])
        with mock.patch.object(potplayer, "control",
                               return_value={"ok": True, "via": "command:10014"}) as ctl:
            self.assertTrue(api.player_control("play_pause")["ok"])
        ctl.assert_called_once_with("play_pause")


if __name__ == "__main__":
    unittest.main()
