"""蓝牙音箱保障单测：全程 mock CoreAudio，绝不触碰真实音频设备。

隔离铁律：涉及 db 配置的用例继承 IsolatedDbTestCase（临时库）；
所有 COM / pycaw 调用都经 bluetooth._audio() 注入假实现，不连真实设备。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app import api, bluetooth as bt, config
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


def _dev(device_id: str, name: str, state: str) -> SimpleNamespace:
    """构造一个假 pycaw AudioDevice：state 用带 name 属性的假枚举。"""
    return SimpleNamespace(id=device_id, FriendlyName=name,
                           state=SimpleNamespace(name=state))


class BluetoothProbeTests(IsolatedDbTestCase):
    def test_disabled_when_no_name(self):
        bt.db.set_setting(bt.SETTING_NAME, "")
        r = bt.probe()
        self.assertEqual(r["status"], bt.DISABLED)
        self.assertEqual(r["action"], "")

    def test_switches_when_active_but_not_default(self):
        bt.db.set_setting(bt.SETTING_NAME, "Aura")
        speaker = _dev("{0.0.0.00000000}.{SPEAKER}", "扬声器 (Aura Studio 5 Stereo)", "Active")
        other = _dev("{0.0.0.00000000}.{REALTEK}", "Realtek Digital Output", "Active")
        fake = mock.Mock()
        fake.GetAllDevices.return_value = [other, speaker]
        fake.GetSpeakers.return_value = other  # 当前默认是别的设备
        with mock.patch.object(bt, "_audio", return_value=fake):
            r = bt.probe()
        self.assertEqual(r["status"], bt.SWITCHED)
        self.assertEqual(r["device"], "扬声器 (Aura Studio 5 Stereo)")
        fake.SetDefaultDevice.assert_called_once_with(speaker.id)

    def test_active_when_already_default_no_switch(self):
        bt.db.set_setting(bt.SETTING_NAME, "Aura")
        speaker = _dev("{0.0.0.00000000}.{SPEAKER}", "Aura Studio 5", "Active")
        fake = mock.Mock()
        fake.GetAllDevices.return_value = [speaker]
        fake.GetSpeakers.return_value = speaker
        with mock.patch.object(bt, "_audio", return_value=fake):
            r = bt.probe()
        self.assertEqual(r["status"], bt.ACTIVE)
        fake.SetDefaultDevice.assert_not_called()

    def test_link_down_when_unplugged(self):
        bt.db.set_setting(bt.SETTING_NAME, "Aura")
        speaker = _dev("{0.0.0.00000000}.{SPEAKER}", "Aura Studio 5", "Unplugged")
        fake = mock.Mock()
        fake.GetAllDevices.return_value = [speaker]
        with mock.patch.object(bt, "_audio", return_value=fake):
            r = bt.probe()
        self.assertEqual(r["status"], bt.LINK_DOWN)
        self.assertEqual(r["action"], "open_settings")
        fake.SetDefaultDevice.assert_not_called()

    def test_not_found(self):
        bt.db.set_setting(bt.SETTING_NAME, "不存在的音箱")
        fake = mock.Mock()
        fake.GetAllDevices.return_value = [_dev("{0.0.0.00000000}.{X}", "别的设备", "Active")]
        with mock.patch.object(bt, "_audio", return_value=fake):
            r = bt.probe()
        self.assertEqual(r["status"], bt.NOT_FOUND)
        # 设备关机后被驱动从列表摘掉也走这条分支，用户此时最需要的是重连入口，
        # 所以必须给「打开蓝牙设置」按钮，不能只说「检查设备名」。
        self.assertEqual(r["action"], "open_settings")
        self.assertIn("未开机", r["message"])

    def test_ignores_capture_endpoints(self):
        """capture 端点（{0.0.1.}）即便名字匹配也不能被当成音箱。"""
        bt.db.set_setting(bt.SETTING_NAME, "Aura")
        capture = _dev("{0.0.1.00000000}.{MIC}", "Aura Studio 5 Microphone", "Active")
        fake = mock.Mock()
        fake.GetAllDevices.return_value = [capture]
        with mock.patch.object(bt, "_audio", return_value=fake):
            r = bt.probe()
        self.assertEqual(r["status"], bt.NOT_FOUND)

    def test_com_failure_degrades_silently(self):
        bt.db.set_setting(bt.SETTING_NAME, "Aura")
        with mock.patch.object(bt, "_audio", side_effect=RuntimeError("no com")):
            r = bt.probe()
        self.assertEqual(r["status"], bt.ERROR)
        self.assertEqual(r["action"], "")

    def test_case_insensitive_substring(self):
        bt.db.set_setting(bt.SETTING_NAME, "aura studio")
        speaker = _dev("{0.0.0.00000000}.{SPEAKER}", "扬声器 (Aura Studio 5 Stereo)", "Active")
        fake = mock.Mock()
        fake.GetAllDevices.return_value = [speaker]
        fake.GetSpeakers.return_value = speaker
        with mock.patch.object(bt, "_audio", return_value=fake):
            r = bt.probe()
        self.assertEqual(r["status"], bt.ACTIVE)


class ApiBluetoothIntegrationTests(IsolatedDbTestCase):
    def test_with_bluetooth_attaches_only_on_ok(self):
        with mock.patch.object(bt, "probe", return_value={"status": bt.LINK_DOWN}) as p:
            ok = api.Api._with_bluetooth({"ok": True})
            self.assertEqual(ok["bluetooth"]["status"], bt.LINK_DOWN)
            p.assert_called_once()
        fail = api.Api._with_bluetooth({"ok": False, "error": "x"})
        self.assertNotIn("bluetooth", fail)

    def test_open_bluetooth_settings_delegates(self):
        with mock.patch.object(bt, "open_bluetooth_settings", return_value={"ok": True}):
            self.assertEqual(api.Api().open_bluetooth_settings(), {"ok": True})

    def test_default_name_is_empty(self):
        self.assertEqual(config.DEFAULTS[bt.SETTING_NAME], "")


class BluetoothSettingsWhitelistTests(IsolatedDbTestCase):
    def test_setting_roundtrip_via_api(self):
        a = api.Api()
        a.save_settings({bt.SETTING_NAME: "我的音箱"})
        self.assertEqual(bt.configured_name(), "我的音箱")
        self.assertEqual(a.get_settings()[bt.SETTING_NAME], "我的音箱")


class BluetoothFrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    def test_setting_input_and_save_key(self):
        self.assertIn('id="set-bluetooth_speaker_name"', self.js)
        self.assertIn("'bluetooth_speaker_name'", self.js)

    def test_play_paths_consume_bluetooth_tip(self):
        # 非内嵌与内嵌两条起播路径都要消费 bluetooth 字段
        self.assertGreaterEqual(self.js.count("handleBluetoothTip(r.bluetooth)"), 2)

    def test_alert_actions_wired(self):
        for action in ("bt-open-settings", "bt-dismiss"):
            self.assertIn(f"data-action=\"{action}\"", self.html)
            self.assertIn(f"case '{action}'", self.js)
        self.assertIn("data-action=\"bt-open-settings\"", self.html)
        self.assertIn("open_bluetooth_settings", self.js)

    def test_alert_container_present(self):
        self.assertIn('id="btAlert"', self.html)
        self.assertIn("打开蓝牙设置", self.html)


if __name__ == "__main__":
    unittest.main()
