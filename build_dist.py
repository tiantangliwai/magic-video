"""一键构建 Magic Video 的绿色版 ZIP 与 MSI 安装包。

用法（在项目根目录，使用已装好依赖的虚拟环境）：
    .venv/Scripts/python.exe build_dist.py
    .venv/Scripts/python.exe build_dist.py --skip-pyi    # 复用已有 PyInstaller 输出
    .venv/Scripts/python.exe build_dist.py --no-msi      # 只出绿色版

产物（dist/ 与 build/ 已在 .gitignore 中，不入库）：
    dist/MagicVideo-<version>-win64-portable.zip   绿色版，数据写在解压目录 data/
    dist/MagicVideo-<version>-win64.msi           安装版，数据写在 D:\\magicvideo
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import __version__  # noqa: E402

VENV_PY = ROOT / ".venv" / "Scripts" / "python.exe"
BUILD = ROOT / "build"
DIST = ROOT / "dist"
WIX_TOOLS = BUILD / "tools" / "wix" / "tools"
WIX_WORK = BUILD / "wix"
PYI_DIST = BUILD / "pyi-dist"
APP_NAME = "Magic Video"
APP_EXE = "Magic Video.exe"
WIX_PACKAGE_URL = "https://api.nuget.org/v3-flatcontainer/wix/3.14.1/wix.3.14.1.nupkg"


def log(message: str) -> None:
    print(f"[build] {message}", flush=True)


def run(cmd: list[str], *, timeout: int | None = None) -> None:
    log("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    subprocess.run(cmd, check=True, cwd=str(ROOT), timeout=timeout)


def ensure_venv() -> None:
    if not VENV_PY.is_file():
        raise SystemExit(
            "缺少构建环境：请先执行\n"
            "  python -m venv .venv\n"
            "  .venv/Scripts/python.exe -m pip install -r requirements.txt pyinstaller"
        )


def ensure_wix() -> None:
    if (WIX_TOOLS / "candle.exe").is_file():
        return
    log("WiX 工具未就绪，从 NuGet 镜像下载 3.14.1 工具包（约 40 MB）…")
    nupkg = BUILD / "tools" / "wix.nupkg"
    nupkg.parent.mkdir(parents=True, exist_ok=True)
    run(["curl", "-L", "--fail", "--silent", "--show-error", "--ssl-no-revoke",
         "--max-time", "300", "-o", str(nupkg), WIX_PACKAGE_URL], timeout=360)
    with zipfile.ZipFile(nupkg) as archive:
        archive.extractall(WIX_TOOLS.parent)
    if not (WIX_TOOLS / "candle.exe").is_file():
        raise SystemExit("WiX 工具解压失败：缺 candle.exe")


def make_icon() -> Path:
    from PIL import Image, ImageDraw

    icon = BUILD / "magic_video.ico"
    BUILD.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((8, 8, 248, 248), radius=56, fill=(59, 110, 245, 255))
    draw.polygon([(100, 72), (100, 184), (188, 128)], fill=(255, 255, 255, 255))
    image.save(icon, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return icon


def build_exe() -> Path:
    icon = make_icon()
    sep = os.pathsep  # Windows 上为 ";"
    cmd = [
        str(VENV_PY), "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", APP_NAME,
        "--icon", str(icon),
        "--distpath", str(PYI_DIST),
        "--workpath", str(BUILD / "pyi-work"),
        "--specpath", str(BUILD / "pyi-spec"),
        "--add-data", f"{ROOT / 'web'}{sep}web",
        "--add-data", f"{ROOT / 'assets'}{sep}assets",
        "--add-data", f"{ROOT / 'app' / 'data'}{sep}app/data",
        "--add-data", f"{ROOT / 'docs'}{sep}docs",
        "--add-data", f"{ROOT / 'CHANGELOG.md'}{sep}.",
        "--add-data", f"{ROOT / 'README.md'}{sep}.",
        "--collect-all", "webview",
        "--collect-all", "clr_loader",
        "--collect-all", "pystray",
        # 蓝牙音箱保障（app/bluetooth.py）：pycaw 封装 CoreAudio COM，comtypes 运行时
        # 动态生成类型库缓存，两者都要整包收集，否则打包版探测会降级为 error。
        "--collect-all", "pycaw",
        "--collect-all", "comtypes",
        "--hidden-import", "clr",
        "--hidden-import", "psutil",
        "--hidden-import", "requests",
        "--hidden-import", "PIL.Image",
        "--hidden-import", "app.daemon",
        "--hidden-import", "app.runtime",
        str(ROOT / "run.py"),
    ]
    run(cmd, timeout=900)
    output = PYI_DIST / APP_NAME
    if not (output / APP_EXE).is_file():
        raise SystemExit(f"PyInstaller 未生成 {APP_EXE}：{output}")
    return output


def make_portable_zip(app_dir: Path) -> Path:
    marker = app_dir / "portable.txt"
    marker.write_text(
        "绿色版标记：存在本文件时，播放记录写入解压目录下的 data/，不写 D:\\magicvideo。\n"
        "删除本文件则改为写入 D:\\magicvideo。\n",
        encoding="utf-8",
    )
    DIST.mkdir(parents=True, exist_ok=True)
    zip_path = DIST / f"MagicVideo-{__version__}-win64-portable.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path(APP_NAME) / path.relative_to(app_dir))
    log(f"绿色版完成：{zip_path.name} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return zip_path


def make_msi(app_dir: Path) -> Path:
    marker = app_dir / "portable.txt"
    if marker.exists():
        marker.unlink()  # 安装版不带便携标记，数据写 D:\magicvideo
    WIX_WORK.mkdir(parents=True, exist_ok=True)
    components = WIX_WORK / "components.wxs"
    run([
        str(WIX_TOOLS / "heat.exe"), "dir", str(app_dir),
        "-cg", "AppFiles", "-dr", "APPFOLDER",
        "-srd", "-sreg", "-ag", "-var", "var.SourceDir", "-nologo",
        "-out", str(components),
    ], timeout=600)
    run([
        str(WIX_TOOLS / "candle.exe"), "-nologo",
        f"-dSourceDir={app_dir}",
        f"-dProductVersion={__version__}",
        "-out", str(WIX_WORK) + os.sep,
        str(ROOT / "installer" / "product.wxs"),
        str(components),
    ], timeout=300)
    msi_path = DIST / f"MagicVideo-{__version__}-win64.msi"
    run([
        str(WIX_TOOLS / "light.exe"), "-nologo", "-sval",
        "-ext", "WixUIExtension",
        "-out", str(msi_path),
        str(WIX_WORK / "product.wixobj"),
        str(WIX_WORK / "components.wixobj"),
    ], timeout=600)
    if not msi_path.is_file():
        raise SystemExit("MSI 生成失败")
    log(f"安装包完成：{msi_path.name} ({msi_path.stat().st_size / 1024 / 1024:.1f} MB)")
    return msi_path


def verify(app_dir: Path) -> None:
    """运行 exe --selftest，确认依赖、资源路径与数据库在打包环境下都可用。"""
    exe = app_dir / APP_EXE
    with tempfile.TemporaryDirectory(prefix="magicvideo-selftest-") as tmp:
        env = dict(os.environ, MAGIC_VIDEO_DATA_DIR=tmp)
        try:
            proc = subprocess.run(
                [str(exe), "--selftest"], env=env, timeout=120,
                capture_output=True, text=True, cwd=str(ROOT),
            )
        except subprocess.TimeoutExpired:
            raise SystemExit("自检超时：可执行文件可能弹出了错误对话框")
        report_path = Path(tmp) / "selftest.json"
        if not report_path.is_file():
            raise SystemExit(
                f"自检未写出报告（退出码 {proc.returncode}）。stderr: {(proc.stderr or '')[:400]}"
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        log(f"自检：ok={report['ok']} version={report['version']} frozen={report['frozen']}")
        log(f"      资源目录={report['resource_dir']}")
        log(f"      数据目录={report['data_dir']}")
        failed = [k for k, v in report["checks"].items() if not v]
        if failed:
            raise SystemExit(f"自检未通过：{', '.join(failed)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建绿色版 ZIP 与 MSI 安装包")
    parser.add_argument("--skip-pyi", action="store_true", help="复用已有 PyInstaller 输出")
    parser.add_argument("--no-msi", action="store_true", help="只构建绿色版")
    parser.add_argument("--no-verify", action="store_true", help="跳过 exe 自检")
    args = parser.parse_args()

    DIST.mkdir(parents=True, exist_ok=True)
    if not args.skip_pyi:
        ensure_venv()
        log(f"开始 PyInstaller 打包，版本 {__version__}")
        app_dir = build_exe()
    else:
        app_dir = PYI_DIST / APP_NAME
        if not (app_dir / APP_EXE).is_file():
            raise SystemExit(f"找不到已有产物：{app_dir}")

    if not args.no_verify:
        verify(app_dir)
    zip_path = make_portable_zip(app_dir)
    msi_path = None
    if not args.no_msi:
        ensure_wix()
        msi_path = make_msi(app_dir)

    log("产物清单：")
    for path in (zip_path, msi_path):
        if path:
            log(f"  {path}  ({path.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    main()
