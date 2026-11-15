"""托盘守护进程的共享入口。

源码版 ``daemon.py`` 与打包版 ``run.py --daemon`` 都调用这里，
保证两条启动路径的初始化顺序完全一致。
"""
from __future__ import annotations

from . import db, metadata
from .tray import Tray
from .watcher import Watcher


def run_daemon() -> None:
    db.init_db()
    metadata.ensure_builtin_loaded()
    watcher = Watcher()
    watcher.start()
    tray = Tray(watcher)
    tray.start()
