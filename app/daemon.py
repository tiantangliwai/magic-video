"""托盘守护进程的共享入口。

源码版 ``daemon.py`` 与打包版 ``run.py --daemon`` 都调用这里，
保证两条启动路径的初始化顺序完全一致。
"""
from __future__ import annotations

from . import db, metadata, single_instance
from .tray import Tray
from .watcher import Watcher


def run_daemon() -> None:
    # 只允许一份托盘守护：主窗口可能在两个进程里几乎同时 Popen 守护，
    # 靠「先查再启动」挡不住竞态，由守护自身抢命名互斥体做最后一道防线。
    if not single_instance.acquire_daemon():
        return
    db.init_db()
    metadata.ensure_builtin_loaded()
    watcher = Watcher()
    watcher.start()
    tray = Tray(watcher)
    tray.start()
