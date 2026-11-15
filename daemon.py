"""托盘常驻进程（源码版）：开机自启指向本文件。

打包版没有独立的 daemon.py，统一用 ``Magic Video.exe --daemon``，
两条路径都调用 ``app.daemon.run_daemon()``。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.daemon import run_daemon  # noqa: E402

if __name__ == "__main__":
    run_daemon()
