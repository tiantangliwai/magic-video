"""Everything 快捷方式与通道诊断：es.exe 空串回退、会话隔离提示、HTTP 认证、打开主程序。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import db, everything
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class EverythingPathTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_empty_es_setting_falls_back_to_default(self):
        db.set_setting("everything_es_path", "")
        self.assertEqual(everything.es_exe(), Path(everything.DEFAULT_ES_PATH))

    def test_configured_es_setting_is_honored(self):
        custom = self.tmp / "my-es.exe"
        db.set_setting("everything_es_path", str(custom))
        self.assertEqual(everything.es_exe(), custom)

    def test_empty_app_setting_falls_back_to_default(self):
        db.set_setting("everything_app_path", "")
        self.assertEqual(everything.app_exe(), Path(everything.DEFAULT_APP_PATH))

    def test_capabilities_reports_app_path_and_installed(self):
        cap = everything.capabilities()
        self.assertIn("app_path", cap)
        self.assertIn("installed", cap)
        self.assertEqual(cap["es_path"], str(Path(everything.DEFAULT_ES_PATH)))

    def test_http_auth_only_when_user_is_set(self):
        self.assertIsNone(everything.http_auth())
        db.set_setting("everything_http_user", "admin")
        db.set_setting("everything_http_pass", "secret")
        self.assertEqual(everything.http_auth(), ("admin", "secret"))

    def test_http_request_passes_auth(self):
        db.set_setting("everything_http_url", "http://127.0.0.1:8080")
        db.set_setting("everything_http_user", "admin")
        db.set_setting("everything_http_pass", "secret")
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"results": []}
        with mock.patch.object(everything.requests, "get", return_value=resp) as get:
            everything.search_http("test", 10, {})
        self.assertEqual(get.call_args.kwargs["auth"], ("admin", "secret"))

    def test_error8_gives_actionable_hint_not_generic_exit_code(self):
        """es.exe 已装但 Everything 以 -svc 运行 → 明确提示改走 HTTP，而非笼统降级。"""
        fake = mock.Mock()
        fake.is_file.return_value = True
        proc = mock.Mock(returncode=8, stdout="",
                         stderr="Error 8: Everything IPC not found. Please make sure Everything is running.")
        with mock.patch.object(everything, "es_exe", return_value=fake), \
             mock.patch.object(everything.subprocess, "run", return_value=proc), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("test")
        self.assertTrue(r["degraded"])
        self.assertEqual(r["source"], "library")
        self.assertIn("服务模式", r["hint"])

    def test_open_app_rejects_missing_or_non_exe(self):
        with mock.patch.object(everything, "app_exe", return_value=self.tmp / "missing.exe"):
            self.assertFalse(everything.open_app()["ok"])
        with mock.patch.object(everything, "app_exe", return_value=self.tmp / "Everything.txt"):
            (self.tmp / "Everything.txt").write_text("x", encoding="utf-8")
            self.assertFalse(everything.open_app()["ok"])

    def test_open_app_uses_startfile_without_shell_command(self):
        exe = self.tmp / "Everything.exe"
        exe.write_bytes(b"MZ")
        with mock.patch.object(everything, "app_exe", return_value=exe), \
             mock.patch.object(everything.os, "startfile") as start:
            r = everything.open_app()
        self.assertTrue(r["ok"], r)
        start.assert_called_once_with(str(exe))

    def test_api_exposes_everything_open(self):
        self.assertTrue(hasattr(self.api, "everything_open"))


class EverythingFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_open_button_in_both_pages_and_handler(self):
        self.assertGreaterEqual(self.js.count('data-action="everything-open"'), 2)
        self.assertIn("case 'everything-open':", self.js)
        self.assertIn("call('everything_open')", self.js)

    def test_app_path_setting_is_saved(self):
        for token in ("set-everything_app_path", "set-everything_http_user",
                      "set-everything_http_pass", "'everything_app_path'",
                      "'everything_http_user'", "'everything_http_pass'"):
            self.assertIn(token, self.js)

    def test_capability_badge_disables_button_when_not_installed(self):
        self.assertIn("cap.installed", self.js)
        self.assertIn("openBtn.disabled", self.js)

    def test_manual_steps_explain_svc_and_lite(self):
        self.assertIn("服务模式 (-svc)", self.js)
        self.assertIn("Lite", self.js)


if __name__ == "__main__":
    unittest.main()
