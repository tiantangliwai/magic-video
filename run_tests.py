"""Magic Video 单元测试入口。

    python run_tests.py            # 跑全部测试
    python -m unittest discover -s tests -v

所有用例都在临时目录里使用独立的 SQLite 文件，不会读写用户真实的 data/library.db。
每次运行后把结果快照写到 data/test_last.json，供 Obsidian 自测报告笔记读取。
"""
from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app import __version__, config  # noqa: E402


def _count_modules(suite) -> dict:
    counts: dict[str, int] = {}
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            for key, value in _count_modules(item).items():
                counts[key] = counts.get(key, 0) + value
        else:
            module = item.__class__.__module__
            counts[module] = counts.get(module, 0) + 1
    return counts


def _write_result(result, duration: float, modules: dict, data_dir: Path) -> None:
    """写结果快照；失败也不影响测试退出码（例如只读目录）。"""
    payload = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "version": __version__,
        "python": sys.version.split()[0],
        "total": result.testsRun,
        "passed": result.testsRun - len(result.failures) - len(result.errors) - len(result.skipped),
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "success": result.wasSuccessful(),
        "duration_sec": round(duration, 2),
        "modules": modules,
    }
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "test_last.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        print(f"（提示：测试结果快照写入失败：{exc}）")


def main() -> int:
    # 在用例改写 config.DATA_DIR 之前先固定真实数据目录
    real_data_dir = Path(config.DATA_DIR).resolve()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    # TestSuite.run() 之后会清空内部用例列表，模块统计必须在运行前取
    modules = _count_modules(suite)
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    elapsed = time.perf_counter() - started
    _write_result(result, elapsed, modules, real_data_dir)
    print(f"\n共 {result.testsRun} 个用例 | 失败 {len(result.failures)} | 错误 {len(result.errors)} "
          f"| 跳过 {len(result.skipped)}")
    print(f"结果快照：{real_data_dir / 'test_last.json'}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
