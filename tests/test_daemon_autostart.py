"""后台记录「默认自动启动 + 设置里可关」回归测试。

约束：所有涉及数据库的用例都跑在临时 SQLite 里，进程拉起与 psutil 枚举
全部 mock，绝不真的启动/杀掉本机托盘守护，也绝不读写真实播放记录。
"""
from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from app import config, db, runtime
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


def _proc(cmdline, pid=1001):
    """伪造一个 psutil 进程对象。"""
    p = mock.Mock()
    p.info = {"cmdline": cmdline}
    p.pid = pid
    return p


class DaemonAutoStartApiTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()
        # 本机可能真的开着守护；所有涉及「是否已在运行」的用例都显式钉死，
        # 避免测试结果依赖真实进程状态。
        self.running_patcher = mock.patch.object(
            self.api, "daemon_running", return_value={"running": False})
        self.running_mock = self.running_patcher.start()

    def tearDown(self):
        self.running_patcher.stop()
        super().tearDown()

    # ---------- 默认配置 ----------
    def test_default_is_enabled_and_exposed_to_settings(self):
        self.assertEqual(config.DEFAULTS["daemon_auto_start"], 1)
        self.assertEqual(db.get_int("daemon_auto_start"), 1)
        # init_db 预置的 settings 值都是字符串，前端 isOn() 会把 '1' 视作开启
        self.assertEqual(int(self.api.get_settings()["daemon_auto_start"]), 1)

    def test_api_init_never_spawns_process(self):
        """Api() 会被单测大量实例化，构造阶段绝不能拉起守护。"""
        with mock.patch("app.api.subprocess.Popen") as popen:
            Api()
        popen.assert_not_called()
        source = inspect.getsource(Api.__init__)
        self.assertNotIn("ensure_daemon_auto_start", source)
        self.assertNotIn("start_daemon", source)

    # ---------- 自动启动 ----------
    def test_ensure_starts_daemon_when_enabled(self):
        with mock.patch.object(self.api, "start_daemon", return_value={"ok": True}) as start:
            result = self.api.ensure_daemon_auto_start()
        start.assert_called_once()
        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])

    def test_ensure_is_idempotent_within_same_instance(self):
        """窗口侧 + 前端 init 会各调一次：进程刚 spawn 时也不能拉起第二个托盘。"""
        with mock.patch.object(self.api, "start_daemon", return_value={"ok": True}) as start:
            first = self.api.ensure_daemon_auto_start()
            second = self.api.ensure_daemon_auto_start()
        start.assert_called_once()
        self.assertTrue(first["started"])
        self.assertFalse(second["started"])
        self.assertTrue(second["already"])

    def test_ensure_skips_when_daemon_already_running(self):
        self.running_mock.return_value = {"running": True, "pid": 4321}
        with mock.patch.object(self.api, "start_daemon") as start:
            result = self.api.ensure_daemon_auto_start()
        start.assert_not_called()
        self.assertFalse(result["started"])
        self.assertTrue(result["already"])

    def test_ensure_does_nothing_when_disabled(self):
        db.set_setting("daemon_auto_start", 0)
        with mock.patch.object(self.api, "start_daemon") as start:
            result = self.api.ensure_daemon_auto_start()
        start.assert_not_called()
        self.assertTrue(result["ok"])
        self.assertFalse(result["started"])
        self.assertTrue(result["disabled"])

    def test_ensure_reports_start_failure_without_raising(self):
        with mock.patch.object(self.api, "start_daemon",
                               return_value={"ok": False, "error": "boom"}):
            result = self.api.ensure_daemon_auto_start()
        self.assertFalse(result["ok"])
        self.assertFalse(result["started"])
        self.assertEqual(result["error"], "boom")
        self.assertFalse(self.api._daemon_auto_started)

    # ---------- 设置保存 ----------
    def test_save_settings_accepts_new_key(self):
        self.api.save_settings({"daemon_auto_start": 0})
        self.assertEqual(db.get_int("daemon_auto_start"), 0)

    def test_disabling_stops_running_daemon_immediately(self):
        with mock.patch.object(self.api, "stop_daemon", return_value={"ok": True, "stopped": 1}) as stop, \
             mock.patch.object(self.api, "start_daemon") as start:
            self.api.save_settings({"daemon_auto_start": 0})
        stop.assert_called_once()
        start.assert_not_called()
        self.assertEqual(db.get_int("daemon_auto_start"), 0)

    def test_enabling_starts_daemon_immediately(self):
        db.set_setting("daemon_auto_start", 0)
        with mock.patch.object(self.api, "start_daemon", return_value={"ok": True}) as start, \
             mock.patch.object(self.api, "stop_daemon") as stop:
            self.api.save_settings({"daemon_auto_start": 1})
        start.assert_called_once()
        stop.assert_not_called()
        self.assertEqual(db.get_int("daemon_auto_start"), 1)

    def test_saving_unrelated_settings_does_not_touch_daemon(self):
        """保存代理 / 主题等设置时不应顺手重启守护。"""
        with mock.patch.object(self.api, "start_daemon") as start, \
             mock.patch.object(self.api, "stop_daemon") as stop:
            self.api.save_settings({"poll_interval_sec": 6})
        start.assert_not_called()
        stop.assert_not_called()

    # ---------- 停止守护 ----------
    def test_stop_daemon_terminates_every_matched_process(self):
        procs = [_proc(["pythonw.exe", "daemon.py"], 101), _proc(["pythonw.exe", "daemon.py"], 102)]
        with mock.patch.object(self.api, "_daemon_processes", return_value=procs):
            result = self.api.stop_daemon()
        self.assertEqual(result, {"ok": True, "stopped": 2})
        for p in procs:
            p.terminate.assert_called_once()
            p.wait.assert_called_once_with(timeout=3)
            p.kill.assert_not_called()
        self.assertFalse(self.api._daemon_auto_started)

    def test_stop_daemon_kills_process_that_ignores_terminate(self):
        p = _proc(["pythonw.exe", "daemon.py"])
        p.wait.side_effect = RuntimeError("still alive")
        with mock.patch.object(self.api, "_daemon_processes", return_value=[p]):
            result = self.api.stop_daemon()
        p.kill.assert_called_once()
        self.assertEqual(result["stopped"], 1)

    def test_stop_daemon_skips_one_bad_process_and_continues(self):
        bad = _proc(["pythonw.exe", "daemon.py"], 201)
        bad.terminate.side_effect = RuntimeError("access denied")
        good = _proc(["pythonw.exe", "daemon.py"], 202)
        with mock.patch.object(self.api, "_daemon_processes", return_value=[bad, good]):
            result = self.api.stop_daemon()
        self.assertEqual(result["stopped"], 1)
        good.terminate.assert_called_once()

    def test_stop_daemon_without_running_process_is_noop(self):
        with mock.patch.object(self.api, "_daemon_processes", return_value=[]):
            result = self.api.stop_daemon()
        self.assertEqual(result, {"ok": True, "stopped": 0})

    # ---------- 进程识别（源码版 / 打包版） ----------
    def test_daemon_running_detects_source_mode(self):
        fake = _proc(["C:/Python/pythonw.exe", "D:/project/magic video/daemon.py"], 777)
        with mock.patch("app.api.psutil.process_iter", return_value=[fake]):
            self.running_patcher.stop()
            result = self.api.daemon_running()
            self.running_patcher.start()
        self.assertEqual(result, {"running": True, "pid": 777})

    def test_daemon_running_ignores_unrelated_python_processes(self):
        others = [
            _proc(["python.exe", "run.py"], 1),
            _proc(["C:/Tool/PotPlayer/PotPlayerMini64.exe"], 2),
            _proc([], 3),
        ]
        with mock.patch("app.api.psutil.process_iter", return_value=others):
            self.running_patcher.stop()
            result = self.api.daemon_running()
            self.running_patcher.start()
        self.assertEqual(result, {"running": False})

    def test_daemon_running_detects_frozen_daemon_but_not_main_window(self):
        exe = "C:/Magic Video/Magic Video.exe"
        daemon = _proc([exe, "--daemon"], 808)
        window = _proc([exe], 809)
        with mock.patch.object(runtime, "is_frozen", return_value=True), \
             mock.patch.object(runtime, "executable", return_value=exe), \
             mock.patch("app.api.psutil.process_iter", return_value=[window, daemon]):
            self.running_patcher.stop()
            result = self.api.daemon_running()
            self.running_patcher.start()
        self.assertEqual(result, {"running": True, "pid": 808})

    def test_daemon_running_degrades_when_psutil_fails(self):
        with mock.patch("app.api.psutil.process_iter", side_effect=OSError("no permission")):
            self.running_patcher.stop()
            result = self.api.daemon_running()
            self.running_patcher.start()
        self.assertEqual(result, {"running": False})


class DaemonAutoStartFrontendTests(unittest.TestCase):
    """前端静态契约：按钮彻底消失、状态仍在、设置页可关、启动会兜底。"""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.api_src = (ROOT / "app" / "api.py").read_text(encoding="utf-8")
        cls.main_src = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    def test_sidebar_has_no_start_button_but_keeps_status(self):
        self.assertNotIn('data-action="start-daemon"', self.html)
        self.assertIn('id="daemonDot"', self.html)
        self.assertIn('id="daemonText"', self.html)

    def test_home_and_action_dispatch_have_no_start_button(self):
        self.assertNotIn("start-daemon", self.js)
        self.assertNotIn("start_daemon", self.js)

    def test_settings_page_exposes_the_switch(self):
        self.assertIn("switchHtml('daemon_auto_start'", self.js)
        self.assertIn("s.daemon_auto_start", self.js)

    def test_save_settings_collects_the_switch(self):
        self.assertIn("case 'save-settings'", self.js)
        self.assertIn("querySelectorAll('[data-setting]')", self.js)
        self.assertIn("daemon_auto_start", self.js)

    def test_init_calls_ensure_as_idempotent_fallback(self):
        self.assertIn("call('ensure_daemon_auto_start')", self.js)

    def test_daemon_status_poll_still_exists(self):
        self.assertIn("async function updateDaemonState()", self.js)
        self.assertIn("setInterval(updateDaemonState", self.js)

    def test_backend_declares_all_three_bridge_methods(self):
        for name in ("ensure_daemon_auto_start", "stop_daemon", "start_daemon"):
            self.assertIn(f"def {name}", self.api_src)
        self.assertIn('"daemon_auto_start"', self.api_src)

    def test_window_entry_triggers_auto_start_not_constructor(self):
        self.assertIn("api.ensure_daemon_auto_start()", self.main_src)
        self.assertIn("except Exception", self.main_src)
        source = inspect.getsource(Api.__init__)
        self.assertNotIn("ensure_daemon_auto_start", source)


if __name__ == "__main__":
    unittest.main()
