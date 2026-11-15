"""看过的列表「第几季」双击编辑：独立 API + 前端按字段分支的契约。"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import db
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class WatchedSeasonApiTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()

    def _row(self, **extra):
        values = {"title": "葬送的芙莉莲", **extra}
        cols = ",".join(values)
        marks = ",".join("?" for _ in values)
        rid = db.execute(f"INSERT INTO watched_list({cols}) VALUES ({marks})",
                         tuple(values.values()))
        return rid

    def test_set_season_from_digit_and_text(self):
        rid = self._row(season_no=1)
        for raw, expected in (("2", 2), ("S3", 3), ("第4季", 4)):
            r = self.api.set_watched_season(rid, raw)
            self.assertTrue(r["ok"], raw)
            self.assertEqual(r["row"]["season_no"], expected)

    def test_empty_or_zero_clears_season(self):
        rid = self._row(season_no=5)
        for raw in ("", "0", "—", "无", None):
            r = self.api.set_watched_season(rid, raw)
            self.assertTrue(r["ok"], raw)
            self.assertIsNone(r["row"]["season_no"])

    def test_invalid_values_rejected(self):
        rid = self._row(season_no=1)
        for raw in ("abc", "1000", "-3"):
            r = self.api.set_watched_season(rid, raw)
            self.assertFalse(r["ok"], raw)
        self.assertEqual(db.row("SELECT season_no FROM watched_list WHERE id = ?", (rid,))["season_no"], 1)

    def test_only_season_changes_other_fields_untouched(self):
        rid = self._row(title="原名", title_cn="中文名", raw_text="原始行",
                        episode_from=1, episode_to=12, match_by="user")
        r = self.api.set_watched_season(rid, "2")
        self.assertTrue(r["ok"])
        row = r["row"]
        self.assertEqual(row["season_no"], 2)
        self.assertEqual(row["title"], "原名")
        self.assertEqual(row["title_cn"], "中文名")
        self.assertEqual(row["raw_text"], "原始行")
        self.assertEqual(row["episode_from"], 1)
        self.assertEqual(row["episode_to"], 12)
        self.assertEqual(row["match_by"], "user")

    def test_bad_id_rejected(self):
        self.assertFalse(self.api.set_watched_season("oops", "2")["ok"])
        self.assertFalse(self.api.set_watched_season(99999, "2")["ok"])


class WatchedSeasonFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    def test_season_cell_is_double_click_editable(self):
        self.assertIn('data-field="season_no"', self.js)
        self.assertIn("双击修改第几季", self.js)

    def test_edit_state_is_field_aware(self):
        for token in ("watchedEditField = 'title'", "watchedEditSeason",
                      "watchedEditField === 'season_no'",
                      "call('set_watched_season'", "'set_watched_season'"):
            self.assertIn(token, self.js)

    def test_dblclick_and_autosave_cover_season_input(self):
        dbl = self.js.split("document.addEventListener('dblclick'", 1)[1].split("});", 1)[0]
        self.assertIn("season_no", dbl)
        self.assertIn("watchedEditSeason", dbl)
        focusout = self.js.split("document.addEventListener('focusout'", 1)[1].split("});", 1)[0]
        # keydown 有多个（含弹窗内的），取真正带编辑白名单的那一段
        keydown = self.js.split("// 改名行内编辑：Enter 保存、Esc 取消", 1)[1].split("});", 1)[0]
        self.assertIn("watchedEditSeason", focusout)
        self.assertIn("watchedEditSeason", keydown)

    def test_title_edit_contract_still_intact(self):
        # 改名入口与隐藏中文名域不能被季数编辑改动破坏
        self.assertIn('type="hidden" id="watchedEditCn"', self.js)
        self.assertIn("titleEl.value, cnEl.value", self.js)
        self.assertIn("case 'edit-watched':", self.js)

    def test_hint_mentions_season_double_click(self):
        self.assertIn('双击「季」这一格改第几季', self.js)


if __name__ == "__main__":
    unittest.main()
