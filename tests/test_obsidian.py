"""Obsidian 同步引擎单元测试：安全边界、幂等、批注保留、结果留痕。"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from app import config, db, obsidian
from tests.base import IsolatedDbTestCase

FAKE_GIT = {
    "commit": "abc1234", "subject": "test: 示例提交", "committed_at": "2026-11-15 01:00:00",
    "branch": "master", "tag": "v0.4.0", "recent": ["abc1234 test: 示例提交"], "dirty": False,
}


class ObsidianTestCase(IsolatedDbTestCase):
    """在临时 vault + 临时数据库里同步，绝不写用户真实的 Obsidian 库。"""

    def setUp(self):
        super().setUp()
        self.vault = self.tmp / "vault"
        self.vault.mkdir()
        self.folder = "Magic Video Tests"
        self.notes = self.vault / self.folder
        # 屏蔽 git 子进程与播放器窗口探测，保证测试快速且无副作用
        self._patches = [
            mock.patch.object(obsidian, "git_info", return_value=dict(FAKE_GIT)),
            mock.patch.object(obsidian.potplayer, "read_status", return_value={"available": False}),
        ]
        for p in self._patches:
            p.start()
        # 造一份测试结果快照，模拟 run_tests.py 已跑过
        (self.tmp / "test_last.json").write_text(json.dumps({
            "timestamp": "2026-11-15 01:00:00", "version": "0.4.0", "python": "3.13.14",
            "total": 128, "passed": 128, "failures": 0, "errors": 0, "skipped": 0,
            "success": True, "duration_sec": 1.9,
            "modules": {"tests.test_db": 12, "tests.test_obsidian": 16},
        }), encoding="utf-8")

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        super().tearDown()

    def sync(self, **kw):
        return obsidian.sync(vault=self.vault, folder=self.folder, **kw)

    def text(self, name: str) -> str:
        return (self.notes / name).read_text(encoding="utf-8")


class VaultDetectionTests(ObsidianTestCase):
    def test_detect_vaults_reads_obsidian_json(self):
        cfg = self.tmp / "obsidian.json"
        cfg.write_text(json.dumps({"vaults": {
            "a1": {"path": str(self.vault), "open": True},
            "b2": {"path": str(self.tmp / "missing"), "ts": 123},
        }}), encoding="utf-8")
        found = obsidian.detect_vaults(cfg)
        self.assertEqual(len(found), 2)
        self.assertTrue(found[0]["open"])
        self.assertTrue(found[0]["exists"])
        self.assertFalse(found[1]["exists"])

    def test_detect_vaults_handles_missing_or_broken_config(self):
        self.assertEqual(obsidian.detect_vaults(self.tmp / "nope.json"), [])
        bad = self.tmp / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        self.assertEqual(obsidian.detect_vaults(bad), [])

    def test_default_vault_only_autoselects_when_unique(self):
        with mock.patch.object(obsidian, "detect_vaults", return_value=[{"path": "A", "exists": True}]):
            self.assertEqual(obsidian.default_vault(), "A")
        with mock.patch.object(obsidian, "detect_vaults", return_value=[
            {"path": "A", "exists": True}, {"path": "B", "exists": True},
        ]):
            self.assertEqual(obsidian.default_vault(), "", "多个 vault 时不应擅自猜")

    def test_resolve_vault_refuses_multiple_candidates(self):
        with mock.patch.object(obsidian, "detect_vaults", return_value=[
            {"path": "A", "exists": True}, {"path": "B", "exists": True},
        ]):
            r = obsidian.resolve_vault()
        self.assertFalse(r["ok"])
        self.assertIn("多个", r["error"])

    def test_safe_folder_rejects_traversal_and_absolute_paths(self):
        self.assertEqual(obsidian.safe_folder("Magic Video/notes"), "Magic Video/notes")
        for bad in ("../outside", "a/../../b", "C:\\Windows", "/etc"):
            with self.assertRaises(ValueError, msg=bad):
                obsidian.safe_folder(bad)

    def test_resolve_vault_rejects_missing_directory(self):
        r = obsidian.resolve_vault(self.tmp / "does-not-exist")
        self.assertFalse(r["ok"])

    def test_set_options_validates_vault_and_persists_settings(self):
        bad = obsidian.set_options(vault=str(self.tmp / "nope"))
        self.assertFalse(bad["ok"])
        good = obsidian.set_options(vault=str(self.vault), folder="Notes", auto_sync="on")
        self.assertTrue(good["ok"])
        self.assertEqual(db.get_setting("obsidian_folder"), "Notes")
        self.assertTrue(obsidian.auto_sync_enabled())


class SyncSafetyTests(ObsidianTestCase):
    def test_first_sync_creates_all_notes_with_frontmatter_and_marker(self):
        r = self.sync()
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["created"], len(obsidian.NOTE_FILES))
        self.assertEqual(r["roadmap_items"] > 10, True)
        for name in obsidian.NOTE_FILES.values():
            text = self.text(name)
            self.assertIn("---\n", text[:5])
            self.assertIn("managed: true", text)
            self.assertIn(obsidian.MARKER, text)
            self.assertIn(obsidian.BEGIN_AUTO, text)
            self.assertIn(obsidian.END_AUTO, text)
            self.assertIn("我的批注", text)
        self.assertTrue((self.notes / obsidian.LOG_DIR_NAME / f"{r['generated_at'][:10]}.md").exists())

    def test_user_note_with_same_name_is_never_overwritten(self):
        target = self.notes / obsidian.NOTE_FILES["overview"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# 我自己写的笔记\n\n不要动我。\n", encoding="utf-8")
        r = self.sync()
        self.assertTrue(r["ok"])
        self.assertEqual(r["written"][obsidian.NOTE_FILES["overview"]], "skipped_unmanaged")
        self.assertIn("不要动我", self.text(obsidian.NOTE_FILES["overview"]))
        self.assertEqual(r["created"], len(obsidian.NOTE_FILES) - 1)

    def test_human_annotations_survive_resync(self):
        self.sync()
        path = self.notes / obsidian.NOTE_FILES["overview"]
        text = path.read_text(encoding="utf-8")
        path.write_text(text + "\n- 我的想法：先做备份导出，再考虑海报墙\n", encoding="utf-8")
        r = self.sync(reason="第二次同步")
        self.assertTrue(r["ok"])
        self.assertIn("我的想法：先做备份导出", self.text(obsidian.NOTE_FILES["overview"]))
        self.assertIn(obsidian.END_AUTO, self.text(obsidian.NOTE_FILES["overview"]))

    def test_frontmatter_refresh_keeps_user_added_fields(self):
        self.sync()
        path = self.notes / obsidian.NOTE_FILES["gaps"]
        text = path.read_text(encoding="utf-8").replace(
            "managed: true", "managed: true\naliases: [我的项目]\ncssclasses: [wide]"
        )
        path.write_text(text, encoding="utf-8")
        self.sync(reason="再次同步")
        after = self.text(obsidian.NOTE_FILES["gaps"])
        self.assertIn("aliases: [我的项目]", after)
        self.assertIn("cssclasses: [wide]", after)

    def test_devlog_events_are_deduplicated(self):
        first = self.sync()
        second = self.sync()
        self.assertIn(first["log"], ("created", "appended"))
        self.assertEqual(second["log"], "duplicate")
        log = (self.notes / obsidian.LOG_DIR_NAME / f"{first['generated_at'][:10]}.md").read_text(encoding="utf-8")
        self.assertEqual(log.count("<!-- event:"), 1)

    def test_devlog_reason_change_adds_new_event(self):
        self.sync(reason="cli test", kind="test")
        self.sync(reason="界面手动同步", kind="manual")
        logs = list((self.notes / obsidian.LOG_DIR_NAME).glob("*.md"))
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0].read_text(encoding="utf-8").count("<!-- event:"), 2)

    def test_history_snapshot_is_written_and_deduplicated(self):
        self.sync()
        self.sync()
        data = json.loads(obsidian.history_path().read_text(encoding="utf-8"))
        self.assertEqual(len(data["events"]), 1)
        self.assertEqual(data["events"][0]["test_total"], 128)

    def test_sync_never_touches_files_outside_namespace(self):
        outside = self.vault / "已有笔记.md"
        outside.write_text("用户内容", encoding="utf-8")
        self.sync()
        self.assertEqual(outside.read_text(encoding="utf-8"), "用户内容")
        self.assertFalse((self.vault / "00 项目总览.md").exists())

    def test_sync_reports_error_instead_of_raising_on_bad_vault(self):
        r = obsidian.sync(vault=self.tmp / "missing", folder=self.folder)
        self.assertFalse(r["ok"])
        self.assertIn("error", r)


class SnapshotAndRenderTests(ObsidianTestCase):
    def test_parse_roadmap_extracts_priorities_from_real_doc(self):
        items = obsidian.parse_roadmap()
        self.assertGreaterEqual(len(items), 15)
        self.assertEqual({i["priority"] for i in items}, {"P0", "P1", "P2", "P3"})
        self.assertTrue(all(i["title"] for i in items))

    def test_load_last_test_handles_missing_and_corrupt_files(self):
        self.assertIsNone(obsidian.load_last_test(self.tmp / "nope.json"))
        bad = self.tmp / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        self.assertIsNone(obsidian.load_last_test(bad))
        self.assertEqual(obsidian.load_last_test()["total"], 128)

    def test_collect_snapshot_contains_real_database_numbers(self):
        self.media("Frieren - 01.mkv")
        snap = obsidian.collect_snapshot()
        self.assertEqual(snap["stats"]["files"], 1)
        self.assertEqual(snap["test"]["total"], 128)
        self.assertEqual(snap["git"]["commit"], "abc1234")
        self.assertGreater(snap["dictionary"]["alias_total"], 0)

    def test_testing_note_contains_latest_result_and_timeline(self):
        r = self.sync()
        self.assertTrue(r["ok"])
        text = self.text(obsidian.NOTE_FILES["testing"])
        self.assertIn("128", text)
        self.assertIn("全绿", text)
        self.assertIn("结果时间线", text)
        self.assertIn("abc1234", text)

    def test_roadmap_note_contains_checkboxes_and_source_note(self):
        self.sync()
        text = self.text(obsidian.NOTE_FILES["roadmap"])
        self.assertIn("- [ ]", text)
        self.assertIn("docs/OPTIMIZATION.md", text)
        self.assertIn("### P0", text)

    def test_status_reports_vault_and_last_test(self):
        st = obsidian.status()
        self.assertTrue(st["ok"])
        self.assertEqual(st["folder"], "Magic Video")
        self.assertEqual(st["last_test"]["total"], 128)
        self.assertIsInstance(st["vaults"], list)

    def test_open_notes_folder_fails_gracefully_before_first_sync(self):
        r = obsidian.open_notes_folder(self.vault, folder=self.folder)
        self.assertFalse(r["ok"])
        self.sync()
        with mock.patch.object(obsidian.os, "startfile", create=True) as starter:
            r = obsidian.open_notes_folder(self.vault, folder=self.folder)
        self.assertTrue(r["ok"])
        starter.assert_called_once()


class ApiBridgeTests(ObsidianTestCase):
    def test_api_exposes_obsidian_methods(self):
        from app.api import Api
        api = Api()
        for name in ("obsidian_status", "obsidian_sync", "obsidian_set_vault", "obsidian_open_folder"):
            self.assertTrue(callable(getattr(api, name, None)), name)
        self.assertIn("obsidian", api.get_settings())

    def test_api_sync_writes_to_configured_vault(self):
        from app.api import Api
        api = Api()
        self.assertTrue(api.obsidian_set_vault(str(self.vault))["ok"])
        api.obsidian_set_vault(str(self.vault))
        db.set_setting("obsidian_folder", self.folder)
        r = api.obsidian_sync("API 自测")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertTrue((self.notes / obsidian.NOTE_FILES["overview"]).exists())

    def test_save_settings_accepts_folder_and_rejects_traversal(self):
        from app.api import Api
        api = Api()
        api.save_settings({"obsidian_folder": "My Notes", "obsidian_vault": str(self.vault)})
        self.assertEqual(db.get_setting("obsidian_folder"), "My Notes")
        api.save_settings({"obsidian_folder": "../evil"})
        self.assertEqual(db.get_setting("obsidian_folder"), "My Notes")


if __name__ == "__main__":
    unittest.main()
