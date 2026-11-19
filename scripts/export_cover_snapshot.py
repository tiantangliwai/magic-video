"""命令行入口：把「看过 / 未看过」清单与封面积累到 Obsidian。

实现位于 ``app/snapshot.py``（随安装包分发，后台进程页的「重新积累」按钮也调它），
这里只负责把项目根目录加进 sys.path 后转发参数。

    python scripts/export_cover_snapshot.py            # 生成节点 + 快照包
    python scripts/export_cover_snapshot.py --dry-run  # 只统计，不写任何文件
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.snapshot import main

if __name__ == "__main__":
    main()
