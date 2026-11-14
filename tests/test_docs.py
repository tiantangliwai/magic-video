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
    "docs/FEATURES.md", "docs/OPTIMIZATION.md", "docs/TESTING.md",
    "run_tests.py", "requirements.txt", ".gitignore",
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

    def test_no_legacy_scheduled_queue_references(self):
        """中文名补全已改为导入时同步匹配，不应残留定时任务相关代码。"""
        for relative in ("app/api.py", "app/main.py", "app/importer.py", "web/app.js", "cli.py"):
            text = (ROOT / relative).read_text(encoding="utf-8")
            self.assertNotIn("metadata_queue", text, relative)
            self.assertNotIn("pending_titles", text, relative)


if __name__ == "__main__":
    unittest.main()
