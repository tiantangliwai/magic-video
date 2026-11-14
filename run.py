"""Magic Video 主窗口入口。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import create_window  # noqa: E402

if __name__ == "__main__":
    create_window(debug="--debug" in sys.argv)
