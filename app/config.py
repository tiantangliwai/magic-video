"""全局路径与默认配置（不依赖数据库，可安全被任何模块导入）。

源码版/打包版：资源在源码根 / PyInstaller 解压目录，数据统一放在
``D:\\magicvideo``（用户指定，避免 C 盘增长、便于备份）。
``MAGIC_VIDEO_DATA_DIR`` 优先级最高，绿色版 ``portable.txt`` 次之。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_PACKAGE_DIR = Path(__file__).resolve().parent
SOURCE_ROOT = APP_PACKAGE_DIR.parent

# 用户指定：播放记录、封面、日志统一放 D 盘，不再写 %APPDATA%。
# 两种运行形态共用一个默认值，守护进程 / 开机自启（不继承环境变量）才不会写错库。
DEFAULT_DATA_DIR = Path("D:/magicvideo")


def resolve_layout(
    *,
    frozen: bool = False,
    bundle_dir: str | Path | None = None,
    exe_dir: str | Path | None = None,
    appdata_dir: str | Path | None = None,
    data_override: str = "",
    portable: bool = False,
    source_root: str | Path | None = None,
    default_data_dir: str | Path | None = None,
) -> tuple[Path, Path]:
    """纯函数：返回 ``(资源根目录, 数据目录)``，方便单元测试直接验证。

    数据目录优先级（源码版与打包版一致）：
    ``MAGIC_VIDEO_DATA_DIR`` > 绿色版 ``portable.txt``（仅打包版）> ``DEFAULT_DATA_DIR``。
    ``appdata_dir`` 仅为兼容旧调用保留，不再参与默认值计算。
    """
    src = Path(source_root or SOURCE_ROOT)
    default_dir = Path(default_data_dir or DEFAULT_DATA_DIR)
    if not frozen:
        data_dir = Path(data_override).expanduser() if data_override else default_dir
        return src, data_dir

    bundle = Path(bundle_dir or src)
    if data_override:
        data_dir = Path(data_override).expanduser()
    elif portable:
        data_dir = Path(exe_dir or src) / "data"
    else:
        data_dir = default_dir
    return bundle, data_dir


FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))
EXE_DIR = Path(sys.executable).resolve().parent if FROZEN else SOURCE_ROOT
APPDATA_DIR = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
PORTABLE = (EXE_DIR / "portable.txt").exists() if FROZEN else False
DATA_OVERRIDE = os.environ.get("MAGIC_VIDEO_DATA_DIR", "")

RESOURCE_DIR, DATA_DIR = resolve_layout(
    frozen=FROZEN,
    bundle_dir=BUNDLE_DIR,
    exe_dir=EXE_DIR,
    data_override=DATA_OVERRIDE,
    portable=PORTABLE,
    source_root=SOURCE_ROOT,
)

# APP_DIR 兼容既有引用：源码版是项目根，打包版是随包分发的资源根（含 docs/ 与 CHANGELOG.md）
APP_DIR = RESOURCE_DIR
WEB_DIR = RESOURCE_DIR / "web"
DB_PATH = DATA_DIR / "library.db"
COVER_DIR = DATA_DIR / "covers"
LOG_DIR = DATA_DIR / "logs"
BUILTIN_ALIAS_FILE = RESOURCE_DIR / "app" / "data" / "aliases_builtin.json"
ALIAS_IMPORT_FILE = DATA_DIR / "alias_import.json"

POTPLAYER_DEFAULT = Path(r"D:\tool\DAUM\PotPlayer\PotPlayerMini64.exe")

VIDEO_EXTS = {
    ".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".flv", ".ts",
    ".m2ts", ".webm", ".rmvb", ".rm", ".mpg", ".mpeg", ".vob", ".3gp",
}
IGNORE_DIR_NAMES = {"$RECYCLE.BIN", "System Volume Information", ".git", "node_modules"}
IGNORE_NAME_PATTERNS = ("sample", "trailer", "特典", "SP", "NCOP", "NCED", "Menu", "Preview")

DEFAULTS = {
    "potplayer_path": str(POTPLAYER_DEFAULT),
    "poll_interval_sec": 3,
    "complete_percent": 90,
    "recording_enabled": 1,
    "autostart": 0,  # 开机自启：写 HKCU Run 注册表项，只影响系统登录时
    # 主窗口启动时自动拉起托盘守护（默认开）。与 autostart（开机自启）、
    # recording_enabled（守护是否写历史）语义互不重叠；可在设置页关闭。
    "daemon_auto_start": 1,
    "http_proxy": "",
    "metadata_source": "cache_first",
    "bangumi_base": "https://api.bgm.tv",
    "window_width": 1180,
    "window_height": 780,
    "minimize_to_tray": 1,
    "theme": "light",  # 展示风格：light(白) / dark(黑)，存 SQLite 而非 localStorage（窗口是 private_mode）
    # 默认在软件内播放 PotPlayer；任何一步嵌入失败都会自动降级为独立窗口
    "embed_player": 1,
    # Obsidian 知识沉淀：vault 留空表示自动探测（仅有一个 vault 时才自动采用）
    "obsidian_vault": "",
    "obsidian_folder": "Magic Video",
    "obsidian_auto_sync": 0,
    # 全盘快搜（Everything）：留空即使用 app/everything.py 里的默认路径。
    # es.exe 是官方命令行客户端（MIT），主程序路径只用于「打开 Everything」快捷按钮。
    "everything_es_path": "",
    "everything_http_url": "",
    "everything_http_user": "",
    "everything_http_pass": "",
    "everything_app_path": "",
}


def ensure_dirs() -> None:
    for directory in (DATA_DIR, COVER_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
