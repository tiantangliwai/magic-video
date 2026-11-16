"""文档与版本一致性守护测试。

防止「代码版本号 / README / CHANGELOG / AGENTS 声明」互相漂移，
也保证交付文档与测试入口始终存在于版本库中。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from app import __version__

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_DOCS = (
    "README.md", "AGENTS.md", "CHANGELOG.md",
    "docs/FEATURES.md", "docs/OPTIMIZATION.md", "docs/TESTING.md", "docs/OBSIDIAN.md",
    "docs/IMPLEMENTATION_NOTES.md",
    "app/obsidian.py", "run_tests.py", "requirements.txt", ".gitignore",
)


class VersionConsistencyTests(unittest.TestCase):
    def test_version_is_semver(self):
        self.assertRegex(__version__, r"^\d+\.\d+\.\d+$")

    def test_required_documents_exist(self):
        for relative in REQUIRED_DOCS:
            self.assertTrue((ROOT / relative).exists(), f"缺少交付文档：{relative}")

    def test_readme_and_agents_declare_current_version(self):
        for name in ("README.md", "AGENTS.md", "CHANGELOG.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn(__version__, text, f"{name} 未声明当前版本 {__version__}")

    def test_changelog_has_current_version_section(self):
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertRegex(text, rf"##\s*\[?{re.escape(__version__)}\]?")

    def test_gitignore_does_not_ignore_builtin_dictionary(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("/data/", text)
        self.assertNotIn("\ndata/\n", "\n" + text + "\n")
        self.assertTrue((ROOT / "app/data/aliases_builtin.json").exists())

    def test_requirements_are_parseable(self):
        lines = [l.strip() for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()]
        packages = [l for l in lines if l and not l.startswith("#")]
        self.assertGreaterEqual(len(packages), 5)
        for line in packages:
            self.assertRegex(line, r"^[A-Za-z0-9_.\-]+\s*(?:[<>=!~]=?\s*[\d.]+)?$", line)

    def test_frontend_exists_and_is_strict(self):
        js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertIn("'use strict'", js)
        self.assertIn("pywebviewready", js)

    def test_frontend_is_not_served_from_stale_cache(self):
        """升级安装后必须加载新版前端：缓存串跟随版本，且窗口不保留持久缓存。"""
        html = (ROOT / "web/index.html").read_text(encoding="utf-8")
        self.assertIn(f"app.js?v={__version__}", html)
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        self.assertIn("private_mode=True", main)

    def test_no_legacy_scheduled_queue_references(self):
        """中文名补全已改为导入时同步匹配，不应残留定时任务相关代码。"""
        for relative in ("app/api.py", "app/main.py", "app/importer.py", "web/app.js", "cli.py"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("metadata_queue", text, relative)
            self.assertNotIn("pending_titles", text, relative)

    def test_obsidian_integration_is_documented_and_wired(self):
        """Obsidian 是交付功能：文档、配置、CLI、前端入口缺一不可。"""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("Obsidian", readme)
        self.assertIn("docs/OBSIDIAN.md", readme)
        self.assertIn("Obsidian", agents)
        cfg = (ROOT / "app/config.py").read_text(encoding="utf-8")
        for key in ("obsidian_vault", "obsidian_folder", "obsidian_auto_sync"):
            self.assertIn(key, cfg)
        cli = (ROOT / "cli.py").read_text(encoding="utf-8")
        self.assertIn('"obsidian"', cli)
        js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.assertIn("obsidian-sync", js)

    def test_incremental_test_policy_is_documented_in_agents(self):
        """用户 2026-11-15 二次纠正：日常只跑增量，禁止拿「改动面大」当借口加跑全量。

        这条约定必须写进 AGENTS.md（新会话第一优先级文件），且是封闭白名单口径，
        防止以后被改回「全量是最终兜底，顺手跑一下」的模糊表述。
        """
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("run_changed_tests.py", text)
        self.assertIn("封闭白名单", text)
        self.assertIn("增量全绿", text)
        self.assertIn("禁止", text)
        # 白名单三种触发条件都要在文档里点名
        for token in ("git push", "tests/base.py", "发版"):
            self.assertIn(token, text)
        # 被明确否决的借口也要在，避免只写「尽量」留口子
        for token in ("改动面大", "兜底", "收尾"):
            self.assertIn(token, text)

    def test_memory_layers_are_documented_in_agents(self):
        """三层 memory 分层必须写进 AGENTS.md，且与 MEMORY.md 口径一致、不冲突。

        L0 冷存储 docs/IMPLEMENTATION_NOTES.md（按需读，不注入）
        L1 .workbuddy/memory/MEMORY.md（≤3000 字，每次会话注入）
        L2 .workbuddy/memory/YYYY-MM-DD.md（追加式短日志）
        """
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for token in ("IMPLEMENTATION_NOTES.md", "MEMORY.md", "YYYY-MM-DD.md", "3000"):
            self.assertIn(token, text, f"AGENTS.md 未声明分层要素：{token}")

    def test_project_memory_stays_under_budget(self):
        """L1 MEMORY.md 是每次会话全量注入的固定成本，超 3000 字会持续吃掉积分。"""
        path = ROOT / ".workbuddy" / "memory" / "MEMORY.md"
        self.assertTrue(path.exists(), "缺少 L1 长期笔记 MEMORY.md")
        char_count = len(path.read_text(encoding="utf-8"))
        self.assertLessEqual(char_count, 3000, f"MEMORY.md {char_count} 字超过 3000 字上限，需蒸馏到 L0 冷存储")

    def test_l0_cold_storage_exists(self):
        """被 MEMORY.md 剥离的实现细节必须有去处，避免信息丢失。"""
        path = ROOT / "docs" / "IMPLEMENTATION_NOTES.md"
        self.assertTrue(path.exists(), "缺少 L0 冷存储 docs/IMPLEMENTATION_NOTES.md")
        text = path.read_text(encoding="utf-8")
        self.assertIn("冷存储", text)

    def test_obsidian_doc_declares_current_version(self):
        text = (ROOT / "docs/OBSIDIAN.md").read_text(encoding="utf-8")
        self.assertIn(__version__, text)
        self.assertIn("managed-by", text)


if __name__ == "__main__":
    unittest.main()
