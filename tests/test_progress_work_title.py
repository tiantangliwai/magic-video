"""总览 / 继续观看的进度项要同时显示「作品名 + 单集名」。

用户场景：把 Kaichou wa Maid-sama! 的文件夹改名成「会长是女仆大人Kaichou wa
Maid-sama! [BD...]」后，剧集墙显示改过的作品名，但继续观看只显示从文件名推导的
旧单集名，每次都要再想一次是哪部剧。作品名必须与剧集墙同源（series 分组 label）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import api as api_mod
from app import db
from app.api import Api, _work_titles_from_tree
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
APP_JS = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "web" / "style.css").read_text(encoding="utf-8")


class WorkTitleTreeTests(unittest.TestCase):
    """纯函数：从 series 树提取 file_id -> 作品名。"""

    def test_extracts_group_label_for_root_and_season_files(self):
        tree = {"groups": [{
            "id": "series::folder::会长是女仆大人", "label": "会长是女仆大人",
            "files": [{"id": 1}],
            "children": [
                {"id": "…::season::1", "label": "第 1 季", "files": [{"id": 2}, {"id": 3}]},
                {"id": "…::season::2", "label": "第 2 季", "files": [{"id": 4}]},
            ],
        }]}
        got = _work_titles_from_tree(tree)
        self.assertEqual(got, {1: "会长是女仆大人", 2: "会长是女仆大人",
                               3: "会长是女仆大人", 4: "会长是女仆大人"})

    def test_empty_label_group_is_skipped(self):
        tree = {"groups": [{"label": "  ", "files": [{"id": 9}], "children": []}]}
        self.assertEqual(_work_titles_from_tree(tree), {})


class ProgressWorkTitleTests(IsolatedDbTestCase):
    """端到端：改过文件夹名后，进度项带作品名、不改单集名。"""

    def setUp(self):
        super().setUp()
        self.api = Api()
        self.root = self.tmp / "root"
        self.root.mkdir()
        db.execute("INSERT INTO directories(path, enabled, file_count) VALUES (?, 1, 0)",
                   (str(self.root),))

    def _episode(self, no: int) -> int:
        folder = self.root / "[SAIO-Raws] 会长是女仆大人Kaichou wa Maid-sama! [BD 1920x1080 HEVC-10bit OPUS]"
        folder.mkdir(exist_ok=True)
        name = f"Kaichou wa Maid-sama! {no:02d}.mkv"
        row = self.media(name, title_guess=f"Kaichou wa Maid-sama! {no:02d}",
                         path=str(folder / name), season_no=1, episode_no=no)
        return int(row["id"])

    def test_folder_renamed_work_title_shows_in_continue_watching(self):
        ids = [self._episode(n) for n in (1, 2, 3)]
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, completed, last_played_at) "
                   "VALUES (?, 1000, 5, 0, '2026-11-20 00:00:00')", (ids[0],))

        cont = self.api.get_continue_watching(10)
        self.assertEqual(len(cont), 1)
        item = cont[0]
        # 单集名仍是文件名推导的旧名（不能改，它是"到底播到哪一集"的事实）
        self.assertEqual(item["display_title"], "Kaichou wa Maid-sama! 01")
        # 作品名来自文件夹名，与剧集墙同源（含中文「女仆」）
        self.assertIn("女仆", item.get("work_title") or "")
        self.assertNotEqual(item["work_title"], item["display_title"])

    def test_completed_items_also_carry_work_title(self):
        ids = [self._episode(n) for n in (1, 2, 3)]
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, completed, last_played_at) "
                   "VALUES (?, 0, 100, 1, '2026-11-20 00:00:00')", (ids[0],))
        done = self.api.get_completed(10)
        self.assertEqual(len(done), 1)
        self.assertIn("女仆", done[0].get("work_title") or "")

    def test_attach_keeps_existing_work_title_and_display_title(self):
        rows = [{"file_id": 1, "display_title": "Ep 01", "work_title": "手改的作品名"}]
        out = self.api._attach_work_titles(rows)
        self.assertEqual(out[0]["work_title"], "手改的作品名")
        self.assertEqual(out[0]["display_title"], "Ep 01")


class ProgressWorkTitleFrontendTests(unittest.TestCase):
    """前端契约：两处进度项都渲染作品名，且仅当与单集名不同时出现。"""

    def test_progress_item_renders_work_line(self):
        block = APP_JS.split("function progressItem(r, actions) {", 1)[1].split(
            "function statsBlock(s)", 1)[0]
        self.assertIn('const work = String(r.work_title', block)
        self.assertIn('work && work !== name', block)
        self.assertIn('class="work-name"', block)
        self.assertIn("${workLine}", block)

    def test_completed_card_renders_work_line(self):
        block = APP_JS.split("done.length ? done.map(r => {", 1)[1].split(".join('')", 1)[0]
        self.assertIn('const work = String(r.work_title', block)
        self.assertIn('class="work-name"', block)
        self.assertIn("${workLine}", block)

    def test_work_name_css_exists(self):
        self.assertIn(".item .work-name", STYLE_CSS)


if __name__ == "__main__":
    unittest.main()
