"""看过的列表：快速搜索与行内改名。

通过 js_api 的公开接口验证行为，全部使用临时库，不发网络请求、不碰真实记录。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import db, metadata
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class WatchedListTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def _watched(self, title: str, *, title_cn=None, raw_text=None,
                 match_by="none", note=None) -> dict:
        item_id = db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, match_by, note) "
            "VALUES (?, ?, ?, ?, ?)",
            (raw_text if raw_text is not None else title, title, title_cn, match_by, note),
        )
        return db.row("SELECT * FROM watched_list WHERE id = ?", (item_id,))

    def test_keyword_searches_title_cn_raw_text_and_note(self):
        a = self._watched("Attack on Titan", title_cn="进击的巨人",
                          raw_text="Attack on Titan S1 1-25")
        b = self._watched("Frieren", title_cn="葬送的芙莉莲")
        c = self._watched("Bocchi the Rock", note="2023 年补番")

        def ids(keyword):
            return {r["id"] for r in self.api.get_watched_list(0, keyword)}

        self.assertEqual(ids("巨人"), {a["id"]})
        self.assertEqual(ids("ATTACK"), {a["id"]})       # 大小写不敏感
        self.assertEqual(ids("S1 1-25"), {a["id"]})      # 导入原文
        self.assertEqual(ids("FRIEREN"), {b["id"]})
        self.assertEqual(ids("补番"), {c["id"]})          # 备注
        self.assertEqual(ids("不存在的作品"), set())

    def test_limit_zero_returns_every_row_and_default_still_works(self):
        for i in range(12):
            self._watched(f"Show {i:02d}")
        self.assertEqual(len(self.api.get_watched_list(0)), 12)
        self.assertEqual(len(self.api.get_watched_list()), 12)
        self.assertEqual(len(self.api.get_watched_list(5)), 5)
        self.assertEqual(len(self.api.get_watched_list(0, "Show 0")), 10)

    def test_like_wildcards_are_literal(self):
        special = self._watched("Special_100%")
        self._watched("Normal Show")

        def ids(keyword):
            return {r["id"] for r in self.api.get_watched_list(0, keyword)}

        # 含通配符的关键词按字面量命中且只命中含该字面量的记录，不会放大成全表
        self.assertEqual(ids("%"), {special["id"]})
        self.assertEqual(ids("_"), {special["id"]})
        self.assertEqual(ids("100%"), {special["id"]})
        self.assertEqual(ids("Special_100%"), {special["id"]})

    def test_update_renames_both_titles_and_remembers_user_choice(self):
        item = self._watched("Attack on Titan", title_cn="进击的巨人",
                             raw_text="Attack on Titan S1 1-25", match_by="builtin")
        result = self.api.update_watched(item["id"], "Shingeki no Kyojin", "进击的巨人·修正版")
        self.assertTrue(result["ok"])
        row = db.row("SELECT * FROM watched_list WHERE id = ?", (item["id"],))
        self.assertEqual(row["title"], "Shingeki no Kyojin")
        self.assertEqual(row["title_cn"], "进击的巨人·修正版")
        self.assertEqual(row["match_by"], "user")
        self.assertEqual(row["raw_text"], "Attack on Titan S1 1-25")  # 导入原文保留
        alias = db.row("SELECT * FROM name_alias WHERE raw_title = ?",
                       ("Shingeki no Kyojin",))
        self.assertEqual(alias["title_cn"], "进击的巨人·修正版")
        self.assertEqual(alias["source"], "user")
        # 下次导入同名作品应命中用户指定的中文名
        preview = self.api.preview_import("Shingeki no Kyojin")
        self.assertEqual(preview["rows"][0]["title_cn"], "进击的巨人·修正版")

    def test_update_can_change_only_title_or_only_cn(self):
        item = self._watched("Frieren", title_cn="葬送的芙莉莲", match_by="builtin")
        self.assertTrue(self.api.update_watched(item["id"], "Sousou no Frieren")["ok"])
        row = db.row("SELECT * FROM watched_list WHERE id = ?", (item["id"],))
        self.assertEqual(row["title"], "Sousou no Frieren")
        self.assertEqual(row["title_cn"], "葬送的芙莉莲")
        self.assertTrue(self.api.update_watched(item["id"], None, "芙莉莲")["ok"])
        row = db.row("SELECT * FROM watched_list WHERE id = ?", (item["id"],))
        self.assertEqual(row["title"], "Sousou no Frieren")
        self.assertEqual(row["title_cn"], "芙莉莲")
        self.assertEqual(row["match_by"], "user")

    def test_clearing_cn_resets_match_state_without_learning_alias(self):
        item = self._watched("Frieren", title_cn="葬送的芙莉莲", match_by="builtin")
        result = self.api.update_watched(item["id"], None, "")
        self.assertTrue(result["ok"])
        row = db.row("SELECT * FROM watched_list WHERE id = ?", (item["id"],))
        self.assertIsNone(row["title_cn"])
        self.assertEqual(row["match_by"], "none")
        # 内置词典里本来就有 Frieren，只断言没有新增用户手改对照
        self.assertIsNone(db.row(
            "SELECT * FROM name_alias WHERE raw_title = ? AND source = 'user'", ("Frieren",)))

    def test_update_rejects_empty_title_and_missing_id(self):
        item = self._watched("Frieren")
        bad = self.api.update_watched(item["id"], "   ", "随便")
        self.assertFalse(bad["ok"])
        self.assertEqual(db.row("SELECT title FROM watched_list WHERE id = ?",
                                (item["id"],))["title"], "Frieren")
        missing = self.api.update_watched(999999, "X", "Y")
        self.assertFalse(missing["ok"])
        self.assertIn("不存在", missing["error"])

    def test_update_only_touches_the_edited_row(self):
        a = self._watched("Frieren", title_cn="葬送的芙莉莲")
        b = self._watched("Frieren S2", title_cn="葬送的芙莉莲 第二季")
        self.assertTrue(self.api.update_watched(a["id"], None, "芙莉莲")["ok"])
        self.assertEqual(db.row("SELECT title_cn FROM watched_list WHERE id = ?",
                               (b["id"],))["title_cn"], "葬送的芙莉莲 第二季")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 2)

    def test_renamed_row_is_found_by_new_and_old_names_as_expected(self):
        item = self._watched("Old Name", title_cn="旧中文名")
        self.api.update_watched(item["id"], "New Name", "新中文名")
        self.assertEqual({r["id"] for r in self.api.get_watched_list(0, "New Name")}, {item["id"]})
        self.assertEqual({r["id"] for r in self.api.get_watched_list(0, "新中文名")}, {item["id"]})
        # 导入原文 raw_text 保留，用旧名也能搜到（改名不会让记录「消失」）
        self.assertEqual({r["id"] for r in self.api.get_watched_list(0, "Old Name")}, {item["id"]})


class WatchedListFrontendTests(unittest.TestCase):
    """前端契约：搜索只局部刷新表格、行内改名直接调用后端。"""

    def setUp(self):
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/style.css").read_text(encoding="utf-8")

    def test_search_box_and_local_refresh_contract(self):
        for token in ('id="watchedSearch"', "watchedRows", "renderWatchedRows",
                      "watchedListBody", "get_watched_list', 0"):
            self.assertIn(token, self.js)
        self.assertNotIn("get_watched_list', 500", self.js)

    def test_inline_rename_controls_exist(self):
        for token in ('data-action="edit-watched"', 'data-action="save-watched"',
                      'data-action="cancel-watched"', "update_watched",
                      "watchedEditTitle", "watchedEditCn"):
            self.assertIn(token, self.js)

    def test_search_input_does_not_rebuild_itself(self):
        self.assertIn("watchedSearch", self.js)
        self.assertIn("watchedListBody", self.js)
        self.assertIn(".watched-edit", self.css)
