"""端到端验证安装包的「选路径生效」与「升级继承路径」。

做法：把 `installer/product.wxs` 复制一份，把 UpgradeCode / 产品名 / 注册表键都换成
测试专用值，构建出两个版本的测试 MSI（0.1.5 → 0.1.6），然后：

  1. 静默安装 0.1.5 并显式指定 APPFOLDER=<临时目录>\\CustomPath，确认文件真的落到那里；
  2. 确认 HKCU 下写入了 InstallPath；
  3. 静默安装 0.1.6（**不传** APPFOLDER），确认它自动装回上一次的 CustomPath；
  4. 卸载测试产品并清理临时目录。

因为 UpgradeCode、产品名、注册表键都是测试专用的，不会碰真实安装，也不会误删用户文件。
用法：.venv/Scripts/python.exe installer/verify_install_flow.py
"""
from __future__ import annotations

import pathlib
import random
import re
import shutil
import subprocess
import sys
import tempfile
import winreg

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIX = ROOT / "build" / "tools" / "wix" / "tools"
WORK = ROOT / "build" / "installer-test"
APP_DIR = ROOT / "build" / "pyi-dist" / "Magic Video"
COMPONENTS = ROOT / "build" / "wix" / "components.wxs"

TEST_UPGRADE_CODE = "{A7C31D8E-4F62-4B19-8E70-2C5B9D1A6F43}"
TEST_COMPONENT_GUID = "{5B2E9A14-6C48-4D07-9F31-8E7A2B4C6D15}"
TEST_KEY = r"Software\Magic Video InstallerTest"
TEST_NAME = "Magic Video InstallerTest"


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kwargs)


def make_test_product() -> pathlib.Path:
    source = (ROOT / "installer" / "product.wxs").read_text(encoding="utf-8")
    text = source
    text = re.sub(r'UpgradeCode="\{[0-9A-Fa-f\-]+\}"', f'UpgradeCode="{TEST_UPGRADE_CODE}"', text)
    text = text.replace('Name="Magic Video"', f'Name="{TEST_NAME}"')
    text = text.replace('Key="Software\\Magic Video"', f'Key="{TEST_KEY}"')
    # 开始菜单也换个名字，避免和真实快捷方式混在一起
    text = text.replace('Name="Magic Video"\n                  Description=',
                        'Name="Magic Video InstallerTest"\n                  Description=')
    # 组件 GUID 必须换：与真实产品共用同一个 GUID 时，Windows Installer 会按引用计数
    # 认为组件仍被真实产品占用，卸载测试产品后注册表值与快捷方式都会被保留。
    text = re.sub(r'Guid="\{2D9B4E6A-[0-9A-Fa-f\-]+\}"', f'Guid="{TEST_COMPONENT_GUID}"', text)
    out = WORK / "product.wxs"
    out.write_text(text, encoding="utf-8")
    return out


def build_msi(version: str) -> pathlib.Path:
    test_product = make_test_product()
    run([
        str(WIX / "candle.exe"), "-nologo",
        f"-dSourceDir={APP_DIR}",
        f"-dProductVersion={version}",
        "-out", str(WORK) + "\\",
        str(test_product), str(COMPONENTS),
    ])
    msi = WORK / f"test-{version}.msi"
    run([
        str(WIX / "light.exe"), "-nologo", "-sval", "-ext", "WixUIExtension",
        "-out", str(msi),
        str(WORK / "product.wixobj"), str(WORK / "components.wixobj"),
    ])
    return msi


def msiexec(args: list[str]) -> int:
    proc = subprocess.run(["msiexec"] + args, capture_output=True, text=True)
    return proc.returncode


def install(msi: pathlib.Path, log: pathlib.Path, props: dict[str, str]) -> int:
    args = ["/i", str(msi), "/qn", "/norestart", "/l*v", str(log)]
    args += [f"{k}={v}" for k, v in props.items()]
    return msiexec(args)


def uninstall(msi: pathlib.Path, log: pathlib.Path) -> int:
    return msiexec(["/x", str(msi), "/qn", "/norestart", "/l*v", str(log)])


def read_install_path() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TEST_KEY) as key:
            return winreg.QueryValueEx(key, "InstallPath")[0]
    except OSError:
        return None


def main() -> int:
    if not APP_DIR.is_dir():
        raise SystemExit(f"缺少 PyInstaller 产物：{APP_DIR}（先跑 build_dist.py）")
    if not COMPONENTS.is_file():
        raise SystemExit(f"缺少 components.wxs：{COMPONENTS}（先跑 build_dist.py）")

    WORK.mkdir(parents=True, exist_ok=True)
    for stale in WORK.glob("test-*.msi"):
        stale.unlink()

    msi_old = build_msi("0.1.5")
    msi_new = build_msi("0.1.6")
    print(f"[ok] 测试包：{msi_old.name} / {msi_new.name}")

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="mv-installer-test-"))
    custom = tmp / "CustomPath"
    default_like = pathlib.Path.home() / "AppData" / "Local" / "Programs" / TEST_NAME
    start_menu = (pathlib.Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows"
                  / "Start Menu" / "Programs")
    failures: list[str] = []

    try:
        # 1) 指定自定义路径安装
        code = install(msi_old, WORK / "install-a.log", {"APPFOLDER": str(custom)})
        print(f"[1] 安装 0.1.5 到 {custom} → msiexec 退出码 {code}")
        exe = custom / "Magic Video.exe"
        if code != 0:
            failures.append(f"指定路径安装失败，退出码 {code}")
        elif not exe.is_file():
            failures.append(f"自定义路径下没有 exe：{exe}")
        else:
            print(f"[ok] 文件确实装到了自定义路径（exe {exe.stat().st_size / 1024 / 1024:.1f} MB）")
        if default_like.exists():
            failures.append(f"不该出现默认路径目录：{default_like}")

        # 2) 注册表是否记下路径
        remembered = read_install_path()
        print(f"[2] HKCU InstallPath = {remembered!r}")
        if not remembered or pathlib.Path(remembered) != custom:
            failures.append(f"注册表没记住安装路径：{remembered!r} != {custom}")

        # 3) 升级：不传 APPFOLDER，应自动装回 CustomPath
        code = install(msi_new, WORK / "install-b.log", {})
        print(f"[3] 升级到 0.1.6（不传路径）→ msiexec 退出码 {code}")
        if code != 0:
            failures.append(f"升级安装失败，退出码 {code}")
        if not exe.is_file():
            failures.append("升级后自定义路径下的 exe 消失了")
        else:
            print("[ok] 升级后仍装在原来的自定义路径")
        if default_like.exists():
            failures.append(f"升级时又装到了默认路径：{default_like}")
    finally:
        # 4) 卸载 + 清理
        code = uninstall(msi_new, WORK / "uninstall.log")
        print(f"[4] 卸载测试产品 → msiexec 退出码 {code}")
        if code != 0:
            failures.append(f"卸载失败，退出码 {code}")
        if read_install_path() is not None:
            failures.append("卸载后注册表 InstallPath 仍存在")
        if (start_menu / "Magic Video InstallerTest.lnk").exists():
            failures.append("卸载后开始菜单快捷方式仍在")
        if custom.exists():
            shutil.rmtree(custom, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
        if default_like.exists():
            shutil.rmtree(default_like, ignore_errors=True)
        for log in ("install-a.log", "install-b.log", "uninstall.log"):
            path = WORK / log
            if path.is_file():
                print(f"    日志 {path}（{path.stat().st_size // 1024} KB）")

    if failures:
        print("\n[FAIL] " + "\n       ".join(failures))
        return 1
    print("\n[PASS] 选路径生效 + 升级继承历史路径")
    return 0


if __name__ == "__main__":
    sys.exit(main())
