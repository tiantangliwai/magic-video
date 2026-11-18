"""按改动增量运行「受影响的」单元测试（日常开发默认入口）。

    .venv/Scripts/python.exe run_changed_tests.py                 # 按未提交改动自动挑选用例
    .venv/Scripts/python.exe run_changed_tests.py --list          # 只看映射结果，不跑
    .venv/Scripts/python.exe run_changed_tests.py --module tests.test_api   # 追加显式模块
    .venv/Scripts/python.exe run_changed_tests.py --base origin/master      # 已提交、准备推送前
    .venv/Scripts/python.exe run_changed_tests.py --all           # 全量（等价 run_tests.py）

约定（用户 2026-11-15 明确要求）：
  * 日常改代码只跑增量 / 受影响的用例，不跑全量；
  * 只有 **git push 前** 和 **打 tag / 发版前** 才跑 `run_tests.py` 全量。

映射表是本文件里显式的 ``IMPACT`` / ``FRONTEND_MODULES``，新增模块时顺手补一行即可；
共享文件（app/api.py、web/app.js、app/db.py）只映射到「最小契约集」，
需要更精确或有跨模块影响时用 ``--module`` 追加，推送前的全量是最终兜底。

本脚本 **不写** ``data/test_last.json``（那是全量门禁的快照，供 Obsidian 自测报告读取），
避免增量运行把全量结果覆盖掉。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from tests import db_guard  # noqa: E402

# 与 run_tests.py 一致：增量入口同样强制装好数据库隔离守卫（幂等）。
db_guard.install()

# ---------------------------------------------------------------- 影响面表
# 变更路径 -> 额外要跑的测试模块（除了同名的 tests/test_<stem>.py 之外）。
# 只登记真实的跨模块依赖，别把整库都塞进来，否则就失去「增量」的意义。
IMPACT: dict[str, tuple[str, ...]] = {
    "app/api.py": ("tests.test_media_browser", "tests.test_media_tree",
                   "tests.test_manual_cleanup", "tests.test_watched_cleanup",
                   "tests.test_player_controls", "tests.test_continue_dismiss",
                   "tests.test_watched_folders", "tests.test_watched_season",
                   "tests.test_manual_cover", "tests.test_everything_open",
                   "tests.test_blacklist", "tests.test_watchlist_top",
                   "tests.test_poster_watched", "tests.test_auto_sync"),
    "app/db.py": ("tests.test_api", "tests.test_manual_cleanup", "tests.test_watcher",
                  "tests.test_continue_dismiss", "tests.test_blacklist"),
    "app/config.py": ("tests.test_packaging", "tests.test_startup", "tests.test_theme",
                      "tests.test_everything_open", "tests.test_blacklist"),
    "app/blacklist.py": ("tests.test_media_browser", "tests.test_media_tree",
                         "tests.test_watched_list", "tests.test_watchlist",
                         "tests.test_quicksearch", "tests.test_blacklist",
                         "tests.test_websearch"),
    "app/library_tree.py": ("tests.test_media_tree", "tests.test_covers",
                            "tests.test_blacklist"),
    "app/importer.py": ("tests.test_importer", "tests.test_importer_realworld",
                        "tests.test_watched_cleanup", "tests.test_watched_list",
                        "tests.test_sync_dedupe", "tests.test_auto_sync"),
    "app/covers.py": ("tests.test_covers", "tests.test_watchlist", "tests.test_manual_cover",
                      "tests.test_websearch"),
    "app/watchlist.py": ("tests.test_watchlist", "tests.test_watched_folders",
                         "tests.test_watchlist_top", "tests.test_blacklist"),
    "app/textmatch.py": ("tests.test_watchlist", "tests.test_covers"),
    "installer/product.wxs": ("tests.test_packaging",),
    "app/everything.py": ("tests.test_everything", "tests.test_quicksearch",
                          "tests.test_everything_open"),
    "app/embedded_player.py": ("tests.test_embedded_player", "tests.test_potplayer"),
    "app/potplayer.py": ("tests.test_potplayer", "tests.test_embedded_player",
                         "tests.test_player_controls"),
    "app/theme_native.py": ("tests.test_theme_native", "tests.test_theme"),
    "app/watcher.py": ("tests.test_watcher", "tests.test_watched_cleanup",
                     "tests.test_auto_sync"),
    "app/daemon.py": ("tests.test_daemon_autostart",),
    "app/single_instance.py": ("tests.test_startup", "tests.test_daemon_autostart"),
    "app/tray.py": ("tests.test_daemon_autostart",),
    "app/main.py": ("tests.test_startup", "tests.test_packaging"),
    "app/runtime.py": ("tests.test_packaging", "tests.test_startup"),
    "app/autostart.py": ("tests.test_startup",),
    "app/file_ops.py": ("tests.test_manual_cleanup", "tests.test_watched_folders"),
    "app/scanner.py": ("tests.test_scanner", "tests.test_manual_cleanup"),
    "app/metadata.py": ("tests.test_metadata", "tests.test_importer"),
    "app/obsidian.py": ("tests.test_obsidian",),
    "app/websearch.py": ("tests.test_websearch", "tests.test_blacklist"),
    "app/__init__.py": ("tests.test_docs",),
}

# 前端是共用文件：只跑持有「界面静态契约」的模块，不扩散到全部 28 个模块。
FRONTEND_MODULES: tuple[str, ...] = (
    "tests.test_api",
    "tests.test_theme",
    "tests.test_quicksearch",
    "tests.test_watched_cleanup",
    "tests.test_manual_cleanup",
    "tests.test_watchlist",
    "tests.test_player_controls",
    "tests.test_continue_dismiss",
    "tests.test_daemon_autostart",
    "tests.test_media_browser",
    "tests.test_media_tree",
    "tests.test_smoke",
    # 看过的列表的返回结构与前端契约以前是漏网模块：改共用前端文件必须连带验证
    "tests.test_watched_list",
    "tests.test_watched_folders",
    # 季数双击编辑 / 人工设定封面 / Everything 快捷按钮的前端契约
    "tests.test_watched_season",
    "tests.test_manual_cover",
    "tests.test_everything_open",
    # 视频黑名单设置页 / 追剧列表置顶按钮的前端契约
    "tests.test_blacklist",
    "tests.test_watchlist_top",
    # 网页搜索视图 / 看过的列表卡片墙结构（外层块 + 内层 grid）的前端契约
    "tests.test_websearch",
    # 剧集墙「是否看过」角标的前端契约
    "tests.test_poster_watched",
)

DOC_PATHS = ("README.md", "AGENTS.md", "CHANGELOG.md")
DOC_MODULES: tuple[str, ...] = ("tests.test_docs",)
ENTRY_MODULES: tuple[str, ...] = ("tests.test_startup", "tests.test_packaging")
ENTRY_PATHS = {
    "run.py", "daemon.py", "cli.py", "start", "start.bat", "start_daemon.bat",
    "dev.bat", "dev.py", "build_dist.py", ".gitignore",
}
IGNORE_PREFIXES = (".workbuddy/", "data/", "build/", "dist/", "app/data/")

# tests/base.py 被所有 IsolatedDbTestCase 继承；改它必须全量。
ALL_TESTS_SENTINEL = "__ALL__"


def _existing_modules(names) -> list[str]:
    """过滤掉仓库里不存在的模块名。"""
    out = []
    for name in names:
        rel = ROOT / (name.replace(".", "/") + ".py")
        if rel.exists():
            out.append(name)
    return out


def modules_for(path: str) -> set[str]:
    """单个变更路径 -> 应跑的测试模块集合（未做存在性过滤）。"""
    path = path.replace("\\", "/").strip()
    if not path or path.startswith(IGNORE_PREFIXES):
        return set()
    if path == "tests/base.py":
        return {ALL_TESTS_SENTINEL}
    if path.startswith("tests/") and Path(path).name.startswith("test_"):
        return {"tests." + Path(path).stem}
    if path == "run_changed_tests.py":
        return {"tests.test_changed_tests"}
    if path in ("run_tests.py",):
        return set()
    if path.startswith("web/"):
        return set(FRONTEND_MODULES)
    if path.startswith("docs/") or path in DOC_PATHS:
        return set(DOC_MODULES)
    if path in ENTRY_PATHS:
        return set(ENTRY_MODULES)
    if path.startswith("app/"):
        stem = Path(path).stem
        found = {f"tests.test_{stem}"}
        found.update(IMPACT.get(path, ()))
        return found
    # 非 app/ 下但登记了影响面的文件（如 installer/product.wxs）
    if path in IMPACT:
        return set(IMPACT[path])
    return set()


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def changed_files(base: str) -> list[str]:
    """未提交改动（相对 base）+ 未跟踪的新文件；base 不是 HEAD 时比较 base..HEAD。"""
    if base == "HEAD":
        tracked = _git("diff", "--name-only", "HEAD")
    else:
        tracked = _git("diff", "--name-only", base, "HEAD")
    untracked = _git("ls-files", "--others", "--exclude-standard")
    files = [p for p in (tracked + "\n" + untracked).splitlines() if p.strip()]
    return sorted(set(files))


def select_modules(files: list[str], extra: list[str]) -> tuple[list[str], dict[str, list[str]], bool]:
    """返回（模块列表、路径->模块的可读映射、是否要求全量）。"""
    mapping: dict[str, list[str]] = {}
    selected: set[str] = set()
    need_all = False
    for path in files:
        mods = modules_for(path)
        if ALL_TESTS_SENTINEL in mods:
            need_all = True
        real = sorted(m for m in mods if m != ALL_TESTS_SENTINEL)
        if real:
            mapping[path] = real
            selected.update(real)
    selected.update(extra)
    return sorted(_existing_modules(selected)), mapping, need_all


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按改动增量运行受影响的单元测试")
    parser.add_argument("--base", default="HEAD",
                        help="对比基准（默认 HEAD=未提交改动；已提交可用 HEAD~1 / origin/master）")
    parser.add_argument("--module", action="append", default=[], metavar="tests.test_x",
                        help="追加显式测试模块，可重复")
    parser.add_argument("--only", action="store_true", help="忽略自动映射，只跑 --module 指定的模块")
    parser.add_argument("--list", action="store_true", help="只打印映射结果，不运行")
    parser.add_argument("--all", action="store_true", help="全量（等价 run_tests.py），推送/发版时用")
    args = parser.parse_args(argv)

    if args.all:
        from run_tests import main as full_main
        return full_main()

    files = changed_files(args.base)
    # --only 等价于「假装没有自动映射的变更文件」，只保留 --module 指定的模块
    modules, mapping, need_all = select_modules(
        [] if args.only else files, args.module)

    print(f"对比基准：{args.base} | 变更文件 {len(files)} 个")
    for path in files:
        print(f"  {path} -> {', '.join(mapping.get(path, ['（无需测试）']))}")
    if need_all:
        print("  ⚠ tests/base.py 有改动：基类影响全部用例，请改用 --all 全量")
    print(f"选中测试模块 {len(modules)} 个：" + (", ".join(modules) or "（无）"))

    if args.list:
        return 0
    if need_all:
        print("拒绝只跑增量：请执行 python run_changed_tests.py --all")
        return 2
    if not modules:
        print("没有映射到测试模块。可用 --module 指定，或 --all 全量；本次无需跑测试。")
        return 0

    loader = unittest.defaultTestLoader
    suite = loader.loadTestsFromNames(modules)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(f"\n增量 {result.testsRun} 个用例 | 失败 {len(result.failures)} "
          f"| 错误 {len(result.errors)} | 跳过 {len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
