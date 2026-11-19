"""「后台进程」页的 Obsidian 积累卡片契约（不写真库、不打真包）。

需求（2026-11-19）：在后台进程页加一张卡片，用户可以过一段时间自己点一次
重新积累对应数据；导出本身由 app/snapshot.py 完成，这里只验证 API 转发与前端接线。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app.api import Api

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web" / "app.js"


class SnapshotApiTests(unittest.TestCase):
    """用 object.__new__ 绕过 Api.__init__，避免 init_db 写库。"""

    @staticmethod
    def _api() -> Api:
        return object.__new__(Api)

    def test_process_status_includes_snapshot_state(self):
        with mock.patch("app.api.covers.status", return_value={}), \
             mock.patch("app.api.snapshot.status", return_value={
                 "ok": True, "generated_at": "2026-11-19 23:55:11", "total": 1}), \
             mock.patch.object(Api, "_daemon_auto_start_enabled", return_value=False):
            with mock.patch.object(self._api(), "daemon_running", return_value={"running": False}):
                api = self._api()
                st = api.process_status()
        self.assertEqual(st["snapshot"]["generated_at"], "2026-11-19 23:55:11")
        self.assertEqual(st["snapshot"]["total"], 1)

    def test_export_snapshot_forwards_result(self):
        expected = {"ok": True, "watched": 133, "unseen": 166, "images": 295,
                    "package": "docs/封面积累_x.tar.gz"}
        with mock.patch("app.api.snapshot.export", return_value=expected) as run:
            got = self._api().export_snapshot()
        self.assertEqual(got, expected)
        run.assert_called_once_with()

    def test_export_snapshot_swallows_errors(self):
        with mock.patch("app.api.snapshot.export", side_effect=RuntimeError("boom")):
            got = self._api().export_snapshot()
        self.assertFalse(got["ok"])
        self.assertIn("boom", got["error"])

    def test_open_notes_rejects_out_of_vault_folder(self):
        vault = ROOT / "docs" / "知识积累"
        with mock.patch("app.api.snapshot.read_settings",
                        return_value=(vault, Path("Magic Video") / ".." / "..")):
            got = self._api().open_snapshot_notes()
        self.assertFalse(got["ok"])


class SnapshotCardFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_processes_page_has_snapshot_card(self):
        block = self.js.split("async function renderProcesses()", 1)[1].split(
            "const RENDERERS =", 1)[0]
        self.assertIn("Obsidian 积累快照", block)
        for field in ("procSnapState", "procSnapCounts", "procSnapLatest", "procSnapNotes",
                      "procSnapResult"):
            self.assertIn(field, block)
        self.assertIn('data-action="snapshot-export"', block)
        self.assertIn('data-action="snapshot-open"', block)
        self.assertIn("不是定时任务", block)

    def test_status_refresh_accepts_snapshot_field(self):
        block = self.js.split("async function loadProcessStatus()", 1)[1].split(
            "async function renderProcesses()", 1)[0]
        self.assertIn("st.snapshot", block)
        self.assertIn("snapshot: st.snapshot", block)
        render = self.js.split("function renderSnapshotStatus(snap)", 1)[1].split(
            "function renderProcessStatus()", 1)[0]
        self.assertIn("watched_works", render)
        self.assertIn("unseen_works", render)
        self.assertIn("snap.latest", render)

    def test_export_handler_disables_only_its_own_button(self):
        block = self.js.split("case 'snapshot-export':", 1)[1].split(
            "case 'snapshot-open':", 1)[0]
        self.assertIn("call('export_snapshot')", block)
        self.assertIn("procSnapExport", block)
        self.assertIn("disabled = true", block)
        self.assertIn("finally", block)
        self.assertIn("loadProcessStatus()", block)
        opened = self.js.split("case 'snapshot-open':", 1)[1].split("case 'proc-daemon-start':", 1)[0]
        self.assertIn("call('open_snapshot_notes')", opened)


if __name__ == "__main__":
    unittest.main()
