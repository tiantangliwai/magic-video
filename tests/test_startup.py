"""启动链路回归测试：单实例、js_api 反射安全、播放器消息超时、一键启动脚本。"""
from __future__ import annotations

import inspect
import os
import stat
import sys
import unittest
from pathlib import Path
from unittest import mock

from app import config, potplayer, single_instance
from app.api import Api

ROOT = Path(__file__).resolve().parent.parent


class SingleInstanceTests(unittest.TestCase):
    """重复点击任务栏图标时只能有一个窗口。"""

    def setUp(self):
        self.k32_patcher = mock.patch.object(single_instance, "_kernel32")
        self.u32_patcher = mock.patch.object(single_instance, "_user32")
        self.k32 = self.k32_patcher.start()
        self.u32 = self.u32_patcher.start()

    def tearDown(self):
        self.k32_patcher.stop()
        self.u32_patcher.stop()
        single_instance._held_handle = None

    def test_first_process_acquires(self):
        self.k32.CreateMutexW.return_value = 12345
        self.k32.GetLastError.return_value = 0
        self.assertTrue(single_instance.acquire())
        self.assertEqual(single_instance._held_handle, 12345)

    def test_second_process_is_rejected(self):
        self.k32.CreateMutexW.return_value = 12345
        self.k32.GetLastError.return_value = single_instance.ERROR_ALREADY_EXISTS
        self.assertFalse(single_instance.acquire())

    def test_guard_activates_existing_instead_of_opening_window(self):
        self.k32.CreateMutexW.return_value = 1
        self.k32.GetLastError.return_value = single_instance.ERROR_ALREADY_EXISTS
        self.u32.FindWindowW.return_value = 999
        self.u32.SetForegroundWindow.return_value = 1
        self.assertFalse(single_instance.guard())
        self.u32.ShowWindow.assert_called_once_with(999, single_instance.SW_RESTORE)
        self.u32.SetForegroundWindow.assert_called_once_with(999)

    def test_guard_missing_window_still_returns_false(self):
        self.k32.CreateMutexW.return_value = 1
        self.k32.GetLastError.return_value = single_instance.ERROR_ALREADY_EXISTS
        self.u32.FindWindowW.return_value = 0
        self.assertFalse(single_instance.guard())
        self.u32.ShowWindow.assert_not_called()

    def test_non_windows_degrades_to_allow(self):
        with mock.patch.object(single_instance, "_kernel32", None):
            self.assertTrue(single_instance.acquire())
            self.assertTrue(single_instance.guard())


class JsApiReflectionTests(unittest.TestCase):
    """pywebview 会 dir() 深度反射 js_api；公开持有 Window 会拖垮启动并抛 COM 异常。"""

    def test_api_exposes_no_window_attribute(self):
        api = Api()
        self.assertFalse(hasattr(api, "window"))
        self.assertTrue(hasattr(api, "_window"))
        api.attach_window(object())
        self.assertFalse(hasattr(api, "window"))

    def test_reflection_walks_no_non_method_public_attributes(self):
        api = Api()
        recursed, errors = [], []

        def walk(obj, base=""):
            for name in dir(obj):
                if name.startswith("_"):
                    continue
                try:
                    attr = getattr(obj, name)
                except Exception as exc:  # pragma: no cover - 正是要防的情况
                    errors.append((name, type(exc).__name__))
                    continue
                if inspect.ismethod(attr) or inspect.isfunction(attr):
                    continue
                if inspect.isclass(attr) or (
                    isinstance(attr, object) and not callable(attr) and hasattr(attr, "__module__")
                ):
                    recursed.append(f"{base}.{name}" if base else name)
                    if len(recursed) < 100:
                        walk(attr, name)

        walk(api)
        self.assertEqual(errors, [])
        self.assertEqual(recursed, [])


class PotPlayerTimeoutTests(unittest.TestCase):
    """播放器无响应时绝不阻塞界面线程。"""

    def test_send_uses_timeout_and_returns_result(self):
        fake = mock.Mock()
        fake.SendMessageTimeoutW.return_value = 1

        def fake_smt(_hwnd, _msg, _code, _lparam, _flags, _timeout, out):
            out._obj.value = 2
            return 1

        fake.SendMessageTimeoutW.side_effect = fake_smt
        with mock.patch.object(potplayer, "_user32", fake):
            self.assertEqual(potplayer.send(7, potplayer.POT_GET_PLAY_STATUS), 2)
        args = fake.SendMessageTimeoutW.call_args[0]
        self.assertEqual(args[4], potplayer.SMTO_ABORTIFHUNG)
        self.assertEqual(args[5], potplayer.SEND_TIMEOUT_MS)

    def test_send_timeout_returns_zero(self):
        fake = mock.Mock()
        fake.SendMessageTimeoutW.return_value = 0
        with mock.patch.object(potplayer, "_user32", fake):
            self.assertEqual(potplayer.send(7, potplayer.POT_GET_TOTAL_TIME), 0)

    def test_send_exception_returns_zero(self):
        fake = mock.Mock()
        fake.SendMessageTimeoutW.side_effect = OSError("com gone")
        with mock.patch.object(potplayer, "_user32", fake):
            self.assertEqual(potplayer.send(7, potplayer.POT_GET_TOTAL_TIME), 0)

    def test_send_non_windows_returns_zero(self):
        with mock.patch.object(potplayer, "_user32", None):
            self.assertEqual(potplayer.send(7, potplayer.POT_GET_TOTAL_TIME), 0)


class StartScriptTests(unittest.TestCase):
    """Git Bash 下 ./start 一键启动，默认连真实数据库。"""

    def setUp(self):
        self.script = ROOT / "start"

    def test_script_exists_with_shebang_and_lf(self):
        self.assertTrue(self.script.is_file())
        raw = self.script.read_bytes()
        # shebang + 纯 LF：CRLF 会让 Git Bash 报 "bad interpreter"，必须由 .gitattributes 保住
        self.assertTrue(raw.startswith(b"#!/usr/bin/env bash\n"))
        self.assertNotIn(b"\r\n", raw)

    def test_script_is_executable(self):
        # Windows/NTFS 不把 Git Bash 的 chmod +x 映射到 Python stat，用 os.access 判断；
        # POSIX 直接看 S_IXUSR。git index 的 100755 由仓库维护。
        if sys.platform == "win32":
            self.assertTrue(os.access(str(self.script), os.X_OK))
        else:
            self.assertTrue(self.script.stat().st_mode & stat.S_IXUSR)

    def test_script_uses_venv_real_data_and_run(self):
        text = self.script.read_text(encoding="utf-8")
        self.assertIn('#!/usr/bin/env bash', text)
        self.assertIn('.venv/Scripts/python.exe', text)
        self.assertIn('MAGIC_VIDEO_DATA_DIR', text)
        self.assertIn('Magic Video', text)
        self.assertIn('D:\\magicvideo', text)
        self.assertNotIn('%APPDATA%', text)
        self.assertIn('run.py', text)
        self.assertNotIn('dev.py', text)

    def test_source_layout_honors_real_data_override(self):
        src, data = config.resolve_layout(
            source_root=str(ROOT), data_override=r"C:\Users\X\AppData\Roaming\Magic Video"
        )
        self.assertEqual(data, Path(r"C:\Users\X\AppData\Roaming\Magic Video"))
        self.assertEqual(src, ROOT)


if __name__ == "__main__":
    unittest.main()
