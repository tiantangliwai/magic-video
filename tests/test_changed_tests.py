"""增量测试入口的映射契约：保证「改动 -> 受影响用例」不会被写漏。"""
from __future__ import annotations

import unittest
from unittest import mock

from run_changed_tests import (
    ALL_TESTS_SENTINEL, changed_files, modules_for, select_modules,
)


class ImpactMappingTests(unittest.TestCase):
    def test_app_module_maps_to_same_named_test(self):
        self.assertIn("tests.test_scanner", modules_for("app/scanner.py"))

    def test_shared_api_maps_cross_module_contracts(self):
        mods = modules_for("app/api.py")
        for expected in ("tests.test_api", "tests.test_media_browser",
                         "tests.test_manual_cleanup", "tests.test_watched_cleanup",
                         "tests.test_player_controls"):
            self.assertIn(expected, mods)

    def test_frontend_file_only_maps_frontend_contracts(self):
        mods = modules_for("web/app.js")
        self.assertIn("tests.test_theme", mods)
        self.assertIn("tests.test_quicksearch", mods)
        self.assertIn("tests.test_watchlist", mods)
        self.assertNotIn("tests.test_obsidian", mods)
        self.assertIn("tests.test_theme", modules_for("web/style.css"))

    def test_continue_dismiss_map_to_contracts(self):
        for path in ("app/api.py", "app/db.py", "web/app.js"):
            self.assertIn("tests.test_continue_dismiss", modules_for(path))
        self.assertEqual(modules_for("tests/test_continue_dismiss.py"),
                         {"tests.test_continue_dismiss"})

    def test_player_controls_map_to_contracts(self):
        self.assertIn("tests.test_player_controls", modules_for("app/potplayer.py"))
        self.assertEqual(modules_for("tests/test_player_controls.py"),
                         {"tests.test_player_controls"})
        self.assertIn("tests.test_player_controls", modules_for("web/app.js"))

    def test_watchlist_and_keyword_modules_map_to_contracts(self):
        """追剧列表 / 关键词模块共用同一套契约，改任一处都要连带验证。"""
        self.assertIn("tests.test_watchlist", modules_for("app/watchlist.py"))
        self.assertEqual(modules_for("tests/test_watchlist.py"),
                         {"tests.test_watchlist"})
        shared = {"tests.test_watchlist", "tests.test_covers"}
        self.assertTrue(shared <= modules_for("app/textmatch.py"))
        self.assertTrue(shared <= modules_for("app/covers.py"))

    def test_watched_folder_module_is_in_frontend_and_api_mapping(self):
        """看过的列表 / 目录跳转契约不能被共用文件改动漏掉。"""
        for path in ("web/app.js", "web/style.css"):
            mods = modules_for(path)
            self.assertIn("tests.test_watched_list", mods)
            self.assertIn("tests.test_watched_folders", mods)
        for path in ("app/api.py", "app/file_ops.py", "app/watchlist.py"):
            self.assertIn("tests.test_watched_folders", modules_for(path))
        self.assertEqual(modules_for("tests/test_watched_folders.py"),
                         {"tests.test_watched_folders"})

    def test_changed_test_file_runs_itself(self):
        self.assertEqual(modules_for("tests/test_api.py"), {"tests.test_api"})

    def test_base_module_change_requires_full_run(self):
        self.assertEqual(modules_for("tests/base.py"), {ALL_TESTS_SENTINEL})

    def test_docs_and_entrypoints(self):
        for path in ("README.md", "AGENTS.md", "docs/FEATURES.md"):
            self.assertEqual(modules_for(path), {"tests.test_docs"})
        self.assertEqual(modules_for("start"), {"tests.test_startup", "tests.test_packaging"})
        self.assertEqual(modules_for("build_dist.py"),
                         {"tests.test_startup", "tests.test_packaging"})
        self.assertEqual(modules_for("run_changed_tests.py"),
                         {"tests.test_changed_tests"})
        self.assertEqual(modules_for("run_tests.py"), set())

    def test_ignored_paths_produce_no_tests(self):
        for path in (".workbuddy/memory/MEMORY.md", "data/test_last.json",
                     "build/pyi-dist/x.exe", "app/data/aliases_builtin.json"):
            self.assertEqual(modules_for(path), set())

    def test_unknown_path_is_empty(self):
        self.assertEqual(modules_for("some/random/file.bin"), set())

    def test_select_modules_dedupes_and_filters_missing(self):
        modules, mapping, need_all = select_modules(
            ["app/api.py", "tests/test_api.py", "web/app.js"],
            ["tests.test_docs", "tests.test_does_not_exist"],
        )
        self.assertFalse(need_all)
        self.assertNotIn("tests.test_does_not_exist", modules)
        self.assertIn("tests.test_docs", modules)
        self.assertEqual(len(modules), len(set(modules)))
        self.assertIn("tests.test_api", mapping["app/api.py"])

    def test_select_modules_flags_full_run(self):
        _, _, need_all = select_modules(["app/api.py", "tests/base.py"], [])
        self.assertTrue(need_all)

    def test_only_mode_ignores_automatic_mapping(self):
        modules, mapping, need_all = select_modules([], ["tests.test_docs"])
        self.assertEqual(modules, ["tests.test_docs"])
        self.assertEqual(mapping, {})
        self.assertFalse(need_all)

    def test_changed_files_includes_tracked_and_untracked(self):
        def fake_git(*args):
            if args[:2] == ("diff", "--name-only"):
                return "app/api.py\nweb/app.js\n"
            if args[:2] == ("ls-files", "--others"):
                return "tests/test_new.py\n"
            return ""
        with mock.patch("run_changed_tests._git", side_effect=fake_git):
            files = changed_files("HEAD")
        self.assertEqual(files, ["app/api.py", "tests/test_new.py", "web/app.js"])


if __name__ == "__main__":
    unittest.main()
