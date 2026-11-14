"""托盘常驻进程：不显示窗口，只负责后台记录与托盘图标。

开机自启指向本文件（用 pythonw.exe 启动，无控制台窗口）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import db, metadata  # noqa: E402
from app.tray import Tray  # noqa: E402
from app.watcher import Watcher  # noqa: E402


def main() -> None:
    db.init_db()
    metadata.ensure_builtin_loaded()
    watcher = Watcher()
    watcher.start()
    tray = Tray(watcher)
    tray.start()


if __name__ == "__main__":
    main()
