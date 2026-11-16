"""端到端验证安装包的「选路径生效」与「升级继承路径」。

做法：把 `installer/product.wxs` 复制一份，把 UpgradeCode / 产品名 / 注册表键都换成
测试专用值，构建出两个版本的测试 MSI（0.1.5 → 0.1.6），然后：

  1. 静默安装 0.1.5 并显式指定 APPFOLDER=<临时目录>\\CustomPath，确认文件真的落到那里；
  2. 确认 HKCU 下写入了 InstallPath；
  3. 静默安装 0.1.6（**不传** APPFOLDER），确认它自动装回上一次的 CustomPath；
  4. 卸载测试产品并清理临时目录。

因为 UpgradeCode、产品名、注册表键都是测试专用的，不会碰真实安装，也不会误删用户文件。

用法：
    .venv/Scripts/python.exe installer/verify_install_flow.py            # 端到端（要 MSI 产物）
    .venv/Scripts/python.exe installer/verify_install_flow.py --static   # 只做静态核对，不需凭产物

``--static`` 模式不需要 WiX 与 PyInstaller 产物，只检查 product.wxs 里三处易回归的属性
（目录 Id 是否全大写、是否标 Secure、是否写并读回 InstallPath），CI 与本机随时能跑。
真机层（安装 → 带路径升级 → 卸载）必须在干净机器上人工执行，清单见 ``MANUAL_CHECKLIST``。
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

# 真机验证必须人工确认的项（--static 会把它打印出来，对照执行）
MANUAL_CHECKLIST = (
    "干净机器（或用独立用户账户）双击 MSI，向导里把路径改成 D:\\MagicVideoTest",
    "安装完成后确认 D:\\MagicVideoTest\\Magic Video.exe 存在，C 盘默认目录不存在",
    "确认 HKCU\\Software\\Magic Video\\InstallPath 等于刚才选的路径（regedit 查看）",
    "直接双击新版 MSI 升级，向导默认路径应已是刚才那个目录，一路下一步即可",
    "升级后确认程序还在原目录、开始菜单快捷方式只有一个、旧版本已从「应用和功能」消失",
    "卸载，确认目录与快捷方式被移除、InstallPath 注册表值被清除",
    "卸载后重装一次，确认能正常装回（验证没有残留状态互相干扰）",
)

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


def static_checks() -> list[str]:
    """不依赖任何构建产物，只核对 product.wxs 的三处高危属性。

    这三处任何一处回归，症状都一样：**向导里选的路径被静默丢弃，一律装到 C 盘**。
    而且只在真机安装时暴露，单测不看这一层就发现不了。
    """
    wxs = (ROOT / "installer" / "product.wxs").read_text(encoding="utf-8")
    problems: list[str] = []

    ids = [i for i in re.findall(r'<Directory Id="([^"]+)" Name="Magic Video" />', wxs)
           if i.isupper()]
    if not ids:
        problems.append("找不到安装目录的 Directory 节点")
        return problems
    directory_id = ids[0]
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", directory_id):
        problems.append(f"目录 Id {directory_id} 含小写：它会退化成私有属性，选的路径传不过去")
    if 'Id="AppInstallFolder"' in wxs:
        problems.append("出现了历史 bug 用的 AppInstallFolder（含小写）")

    wizard = re.search(r'<Property Id="WIXUI_INSTALLDIR" Value="([^"]+)"', wxs)
    if not wizard:
        problems.append("缺少 WIXUI_INSTALLDIR，安装向导不会显示目录选择页")
    elif wizard.group(1) != directory_id:
        problems.append(f"WIXUI_INSTALLDIR 指向 {wizard.group(1)}，与目录 Id {directory_id} 不一致")

    secure = re.search(r'<Property Id="([A-Z][A-Z0-9_]*)"[^>]*Secure="yes"', wxs)
    if not secure:
        problems.append("安装目录属性没有标 Secure：提权安装时向导选的路径会在序列切换时丢失")
    elif secure.group(1) != directory_id:
        problems.append(f"标了 Secure 的属性是 {secure.group(1)}，不是安装目录 {directory_id}")

    if "<MajorUpgrade" not in wxs:
        problems.append("缺少 MajorUpgrade，升级会与旧版本并存")
    if not re.search(r'<RegistrySearch[^>]*Root="HKCU"[^>]*Name="InstallPath"', wxs):
        problems.append("缺少 InstallPath 的 RegistrySearch，升级读不回上次路径")
    if not re.search(r'<RegistryValue[^>]*Name="InstallPath"[^>]*Value="\[[A-Z][A-Z0-9_]*\]"', wxs):
        problems.append("安装时没有把选定的目录写进 HKCU InstallPath")
    if '<Property Id="ARPINSTALLLOCATION" Value="[' in wxs:
        problems.append("ARPINSTALLLOCATION 用了 Property 表赋值：Property 不做格式化，必须走 Type 51 动作")
    if 'Property="ARPINSTALLLOCATION"' not in wxs:
        problems.append("没有给 ARPINSTALLLOCATION 赋值，「应用和功能」里看不到安装位置")
    return problems


def print_checklist() -> None:
    print("\n真机层还需要人工执行（--static 覆盖不到）：")
    for index, item in enumerate(MANUAL_CHECKLIST, 1):
        print(f"  {index}. {item}")


def run_static() -> int:
    problems = static_checks()
    if problems:
        print("[FAIL] 安装器静态核对未通过：")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("[PASS] 安装器静态核对通过：目录 Id 全大写 + Secure + InstallPath 写入/回读 + MajorUpgrade")
    print_checklist()
    return 0


def main() -> int:
    if "--static" in sys.argv:
        return run_static()
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
    print_checklist()
    return 0


if __name__ == "__main__":
    sys.exit(main())
