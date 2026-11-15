"""本地调试辅助：把真实数据库复制成安全副本。

真实库默认在 ``D:\\magicvideo\\library.db``（见 ``app.config.DEFAULT_DATA_DIR``）。
调试窗口用它当数据目录，既能看到全部扫描结果，又不会把调试期间的
播放、导入、扫描操作写进真实播放记录。SQLite backup API 读取一致快照，
源库开启 WAL 或被软件占用时也能安全复制。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app import config

SOURCE = config.DEFAULT_DATA_DIR / "library.db"
TARGET_DIR = Path(__file__).resolve().parent / "data" / "dev"
TARGET = TARGET_DIR / "library.db"


def sync_copy() -> tuple[bool, int]:
    """返回 (是否复制成功, 副本里的媒体文件数)。源库不存在时返回 (False, 0)。"""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    if not SOURCE.is_file():
        return False, 0
    src = sqlite3.connect(f"file:{SOURCE.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(str(TARGET))
    try:
        src.backup(dst)
        try:
            count = dst.execute("SELECT COUNT(*) FROM media_files").fetchone()[0]
        except sqlite3.Error:
            count = 0
    finally:
        dst.close()
        src.close()
    return True, int(count)


if __name__ == "__main__":
    ok, count = sync_copy()
    if ok:
        print(f"[dev] real db copied -> {TARGET} ({count} media files)")
    else:
        print(f"[dev] no real db at {SOURCE}; will start with an empty dev db")
