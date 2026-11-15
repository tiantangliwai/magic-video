"""打包版运行形态单元测试：路径分离、启动命令、自启与笔记降级。

不启动播放器、不打开窗口、不连接真实数据库、不发网络请求。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import __version__, autostart, config, obsidian, runtime

FROZEN_EXE = r"C:\Program Files\Magic Video\Magic Video.exe"


class LayoutTests(unittest.TestCase):
    """config.resolve_layout 是纯函数，可以直接验证四种布局。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="magicvideo-layout-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_source_layout_uses_repo_root(self):
        resource, data = config.resolve_layout(frozen=False, source_root=self.tmp)
        self.assertEqual(resource, self.tmp)
        self.assertEqual(data, self.tmp / "data")

    def test_frozen_default_uses_appdata_not_bundle(self):
        bundle = self.tmp / "bundle"
        appdata = self.tmp / "appdata"
        resource, data = config.resolve_layout(
            frozen=True, bundle_dir=bundle, exe_dir=self.tmp / "Program Files",
            appdata_dir=appdata,
        )
        self.assertEqual(resource, bundle)
        self.assertEqual(data, appdata / "Magic Video")
        self.assertNotEqual(data.parent, bundle)  # 数据绝不写进解压目录

    def test_frozen_portable_uses_exe_sibling_data(self):
        exe_dir = self.tmp / "portable"
        _, data = config.resolve_layout(
            frozen=True, bundle_dir=exe_dir / "bundle", exe_dir=exe_dir,
            appdata_dir=self.tmp / "appdata", portable=True,
        )
        self.assertEqual(data, exe_dir / "data")

    def test_data_override_has_highest_priority(self):
        override = self.tmp / "custom-data"
        _, data = config.resolve_layout(
            frozen=True, bundle_dir=self.tmp / "bundle", exe_dir=self.tmp,
            appdata_dir=self.tmp / "appdata", portable=True, data_override=str(override),
        )
        self.assertEqual(data, override)

    def test_source_mode_constants_point_to_real_files(self):
        self.assertFalse(config.FROZEN)
        self.assertTrue((config.WEB_DIR / "index.html").is_file())
        self.assertTrue(config.BUILTIN_ALIAS_FILE.is_file())
        self.assertEqual(config.DATA_DIR, config.SOURCE_ROOT / "data")


class RuntimeCommandTests(unittest.TestCase):
    def test_not_frozen_by_default(self):
        self.assertFalse(runtime.is_frozen())

    def test_source_commands_use_python_scripts(self):
        with mock.patch.object(runtime, "is_frozen", return_value=False):
            main = runtime.main_command()
            daemon = runtime.daemon_command()
        self.assertTrue(main[-1].endswith("run.py"))
        self.assertTrue(daemon[-1].endswith("daemon.py"))

    def test_frozen_main_command_is_bare_exe(self):
        with mock.patch.object(runtime, "is_frozen", return_value=True), \
                mock.patch.object(runtime.sys, "executable", FROZEN_EXE):
            cmd = runtime.main_command()
        self.assertEqual(len(cmd), 1)
        self.assertTrue(cmd[0].endswith("Magic Video.exe"))

    def test_frozen_daemon_command_has_daemon_flag(self):
        with mock.patch.object(runtime, "is_frozen", return_value=True), \
                mock.patch.object(runtime.sys, "executable", FROZEN_EXE):
            cmd = runtime.daemon_command()
        self.assertEqual(len(cmd), 2)
        self.assertTrue(cmd[0].endswith("Magic Video.exe"))
        self.assertEqual(cmd[1], "--daemon")

    def test_command_line_quotes_every_part(self):
        self.assertEqual(runtime.command_line(["a b.exe", "--daemon"]), '"a b.exe" "--daemon"')


class FrozenIntegrationTests(unittest.TestCase):
    def test_autostart_command_in_frozen_mode(self):
        with mock.patch.object(runtime, "is_frozen", return_value=True), \
                mock.patch.object(runtime.sys, "executable", FROZEN_EXE):
            cmd = autostart.command()
        self.assertIn("--daemon", cmd)
        self.assertIn("Magic Video.exe", cmd)
        self.assertNotIn("daemon.py", cmd)

    def test_git_info_fallback_in_frozen_mode(self):
        with mock.patch.object(runtime, "is_frozen", return_value=True):
            info = obsidian.git_info()
        self.assertEqual(info["tag"], f"v{__version__}")
        self.assertEqual(info["branch"], "release")
        self.assertEqual(info["recent"], [])
        self.assertFalse(info["dirty"])

    def test_api_start_daemon_uses_runtime_command(self):
        from app.api import Api
        api = Api()
        with mock.patch.object(api, "daemon_running", return_value={"running": False}), \
                mock.patch("app.api.subprocess.Popen") as popen, \
                mock.patch("app.api.runtime.daemon_command", return_value=["exe", "--daemon"]):
            result = api.start_daemon()
        self.assertTrue(result["ok"])
        self.assertEqual(popen.call_args.args[0], ["exe", "--daemon"])

    def test_tray_open_main_uses_runtime_command(self):
        from app.tray import Tray
        tray = Tray(watcher=None)
        with mock.patch("app.tray.subprocess.Popen") as popen, \
                mock.patch("app.tray.runtime.main_command", return_value=["exe"]):
            tray._open_main()
        self.assertEqual(popen.call_args.args[0], ["exe"])

    def test_run_main_dispatches_selftest(self):
        import run
        with mock.patch.object(run.sys, "argv", ["Magic Video.exe", "--selftest"]), \
                mock.patch.object(run, "_selftest") as selftest_mock, \
                mock.patch.object(run, "create_window") as window_mock:
            run.main()
        selftest_mock.assert_called_once_with()
        window_mock.assert_not_called()

    def test_run_main_dispatches_daemon_flag(self):
        import run
        with mock.patch.object(run.sys, "argv", ["Magic Video.exe", "--daemon"]), \
                mock.patch("app.daemon.run_daemon") as daemon_mock, \
                mock.patch.object(run, "create_window") as window_mock:
            run.main()
        daemon_mock.assert_called_once_with()
        window_mock.assert_not_called()

    def test_run_main_opens_window_without_flag(self):
        import run
        with mock.patch.object(run.sys, "argv", ["run.py"]), \
                mock.patch.object(run, "create_window") as window_mock:
            run.main()
        window_mock.assert_called_once_with(debug=False)


if __name__ == "__main__":
    unittest.main()
