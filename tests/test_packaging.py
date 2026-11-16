"""打包版运行形态单元测试：路径分离、启动命令、自启与笔记降级。

不启动播放器、不打开窗口、不连接真实数据库、不发网络请求。
"""
from __future__ import annotations

import re
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
        self.assertEqual(data, config.DEFAULT_DATA_DIR)

    def test_frozen_default_uses_shared_data_dir_not_bundle(self):
        bundle = self.tmp / "bundle"
        resource, data = config.resolve_layout(
            frozen=True, bundle_dir=bundle, exe_dir=self.tmp / "Program Files",
            appdata_dir=self.tmp / "appdata",
        )
        self.assertEqual(resource, bundle)
        self.assertEqual(data, config.DEFAULT_DATA_DIR)
        self.assertNotEqual(data.parent, bundle)  # 数据绝不写进解压目录

    def test_default_data_dir_can_be_overridden_for_tests(self):
        custom = self.tmp / "isolated"
        _, data = config.resolve_layout(frozen=False, source_root=self.tmp,
                                        default_data_dir=custom)
        self.assertEqual(data, custom)
        _, data_frozen = config.resolve_layout(
            frozen=True, bundle_dir=self.tmp / "bundle", exe_dir=self.tmp,
            default_data_dir=custom,
        )
        self.assertEqual(data_frozen, custom)

    def test_frozen_portable_uses_exe_sibling_data(self):
        exe_dir = self.tmp / "portable"
        _, data = config.resolve_layout(
            frozen=True, bundle_dir=exe_dir / "bundle", exe_dir=exe_dir,
            appdata_dir=self.tmp / "appdata", portable=True,
        )
        self.assertEqual(data, exe_dir / "data")

    def test_source_layout_honors_data_override(self):
        # 本地调试时用真实库副本：源码版也必须认 MAGIC_VIDEO_DATA_DIR，
        # 否则调试窗口只能看到项目内 data/ 的空库。
        override = self.tmp / "dev-data"
        resource, data = config.resolve_layout(
            frozen=False, source_root=self.tmp, data_override=str(override),
        )
        self.assertEqual(resource, self.tmp)
        self.assertEqual(data, override)

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
        self.assertEqual(config.DATA_DIR, config.DEFAULT_DATA_DIR)


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


class InstallerDefinitionTests(unittest.TestCase):
    """安装包定义（installer/product.wxs）的硬约束。

    背景：v0.1.5 用户反馈「重新选了安装路径，还是装到 C 盘」。根因是安装目录的
    Directory Id 写成了 `AppInstallFolder`（含小写）——MSI 规定只有**全大写**的属性
    才是 public property，值才能在 UI 序列 → execute 序列之间传递。小写属性的中文名
    只在 UI 序列里生效，execute 序列读不到，最终仍按默认路径安装。

    这些断言把「属性必须全大写、向导绑定与 heat 引用必须指向同一个 Id、升级要记住
    上次路径」固定下来，避免以后改安装包时重犯。
    """

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent.parent
        cls.wxs = (root / "installer" / "product.wxs").read_text(encoding="utf-8")
        cls.build = (root / "build_dist.py").read_text(encoding="utf-8")

    def _install_dir_id(self) -> str:
        found = re.findall(r'<Directory Id="([^"]+)" Name="Magic Video" />', self.wxs)
        found = [i for i in found if i.isupper()]
        self.assertTrue(found, "找不到安装目录的 Directory 节点")
        return found[0]

    def test_install_directory_property_is_all_uppercase(self):
        """目录 Id 必须全大写，否则它在 MSI 里是私有属性，向导选的路径传不过去。"""
        ids = re.findall(r'<Directory Id="([^"]*)" Name="(?:Programs|Magic Video)"', self.wxs)
        self.assertTrue(ids)
        for directory_id in ids:
            self.assertRegex(directory_id, r"^[A-Z][A-Z0-9_]*$",
                             f"目录属性 {directory_id} 含小写，会退化成私有属性")
        self.assertNotIn('Id="AppInstallFolder"', self.wxs)

    def test_wizard_binds_to_the_same_directory(self):
        directory_id = self._install_dir_id()
        match = re.search(r'<Property Id="WIXUI_INSTALLDIR" Value="([^"]+)"', self.wxs)
        self.assertIsNotNone(match, "缺少 WIXUI_INSTALLDIR，向导不会显示目录选择页")
        self.assertEqual(match.group(1), directory_id)

    def test_heat_references_the_same_directory(self):
        directory_id = self._install_dir_id()
        match = re.search(r'"-dr",\s*"([^"]+)"', self.build)
        self.assertIsNotNone(match, "build_dist.py 里找不到 heat 的 -dr 参数")
        self.assertEqual(match.group(1), directory_id)

    def test_shortcut_points_at_the_same_directory(self):
        directory_id = self._install_dir_id()
        self.assertIn(f'Target="[{directory_id}]Magic Video.exe"', self.wxs)
        self.assertIn(f'WorkingDirectory="{directory_id}"', self.wxs)

    def test_upgrade_remembers_previous_install_path(self):
        """安装时写 HKCU InstallPath，升级时由 AppSearch 读回来，避免每次都回默认路径。"""
        self.assertIn("<MajorUpgrade", self.wxs)
        self.assertRegex(self.wxs, r'<RegistrySearch[^>]*Root="HKCU"[^>]*Name="InstallPath"')
        self.assertRegex(self.wxs, r'<RegistryValue[^>]*Name="InstallPath"[^>]*Value="\[[A-Z][A-Z0-9_]*\]"')

    def test_install_dir_property_is_secure(self):
        """提权安装时，只有 SecureCustomProperties 里的公共属性会从 UI 序列传到执行序列。

        本机实测安装以提权方式运行（Privileged=1）；不标 Secure，向导里选的路径会被丢掉。
        """
        directory_id = self._install_dir_id()
        match = re.search(r'<Property Id="([A-Z][A-Z0-9_]*)"[^>]*Secure="yes"', self.wxs)
        self.assertIsNotNone(match, "安装目录属性没有标 Secure，提权安装时选的路径会丢失")
        self.assertEqual(match.group(1), directory_id)

    def test_arp_install_location_is_set_by_custom_action(self):
        """Property 表不做格式化，ARPINSTALLLOCATION 必须用 Type 51 动作赋值。"""
        self.assertIn('Property="ARPINSTALLLOCATION"', self.wxs)
        self.assertIn('After="CostFinalize"', self.wxs)
        self.assertNotIn('<Property Id="ARPINSTALLLOCATION" Value="[', self.wxs)

    def test_verify_script_static_mode_passes(self):
        """安装器验证脚本的静态层能独立跑通（不需要 WiX / PyInstaller 产物）。

        真机层（安装 → 带路径升级 → 卸载）必须人工执行，脚本会把清单打印出来；
        但静态层要进自动化，否则「又装回 C 盘」这类回归只能等发版后才发现。
        """
        import subprocess
        import sys
        root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, str(root / "installer" / "verify_install_flow.py"), "--static"],
            capture_output=True, text=True, cwd=str(root), timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("[PASS]", proc.stdout)
        self.assertIn("真机层", proc.stdout)


if __name__ == "__main__":
    unittest.main()
