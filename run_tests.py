"""Magic Video 单元测试入口。

    python run_tests.py            # 跑全部测试
    python -m unittest discover -s tests -v

所有用例都在临时目录里使用独立的 SQLite 文件，不会读写用户真实的 data/library.db。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    suite = unittest.defaultTestLoader.discover(
        str(Path(__file__).resolve().parent / "tests"), top_level_dir=str(Path(__file__).resolve().parent)
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n共 {result.testsRun} 个用例 | 失败 {len(result.failures)} | 错误 {len(result.errors)} "
          f"| 跳过 {len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
