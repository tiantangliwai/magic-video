"""全局路径与默认配置（不依赖数据库，可安全被任何模块导入）。"""
from __future__ import annotations

from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
APP_PACKAGE_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"
DATA_DIR = APP_DIR / "data"
DB_PATH = DATA_DIR / "library.db"
COVER_DIR = DATA_DIR / "covers"
LOG_DIR = DATA_DIR / "logs"
BUILTIN_ALIAS_FILE = APP_PACKAGE_DIR / "data" / "aliases_builtin.json"
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
    "autostart": 0,
    "http_proxy": "",
    "metadata_source": "cache_first",
    "bangumi_base": "https://api.bgm.tv",
    "window_width": 1180,
    "window_height": 780,
    "minimize_to_tray": 1,
    # Obsidian 知识沉淀：vault 留空表示自动探测（仅有一个 vault 时才自动采用）
    "obsidian_vault": "",
    "obsidian_folder": "Magic Video",
    "obsidian_auto_sync": 0,
}


def ensure_dirs() -> None:
    for directory in (DATA_DIR, COVER_DIR, LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
