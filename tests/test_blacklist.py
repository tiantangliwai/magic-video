"""视频黑名单：默认开启、名称包含匹配、全位置过滤、开关关闭后恢复。

需求原话（用户 2026-11-15）：增加一个设置，黑名单例如「daas,sone,...,ymdd」，
在所有位置默认不展示名称包含黑名单字段（不区分大小写）的视频；增加按钮开关，
默认开启，关闭后正常展示所有名称的视频。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import api, blacklist, db
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web" / "app.js"


class BlacklistParsingTests(IsolatedDbTestCase):
    def test_defaults_enable_and_carry_user_keywords(self):
        self.assertTrue(blacklist.enabled())
        kws = blacklist.keywords()
        for kw in ("daas", "sone", "三上悠亚", "fc2ppv", "mvsd.naiad", "stars-", "n房间"):
            self.assertIn(kw, kws)

    def test_split_on_comma_cjk_comma_newline_semicolon_and_dedupe(self):
        db.set_setting("video_blacklist", "AAA，bbb\nccc、AAA;ddd|eee")
        self.assertEqual(blacklist.keywords(), ["aaa", "bbb", "ccc", "ddd", "eee"])

    def test_blank_entries_are_dropped(self):
        db.set_setting("video_blacklist", " ,,a,, ,")
        self.assertEqual(blacklist.keywords(), ["a"])

    def test_hit_is_case_insensitive_and_substring(self):
        self.assertTrue(blacklist.hit("FC2PPV-123456.mp4"))
        self.assertTrue(blacklist.hit("[三上悠亚] SSIS-001.mkv"))
        self.assertTrue(blacklist.hit("MIDA-077.mp4"))
        self.assertFalse(blacklist.hit("葬送的芙莉莲 01.mkv"))
        self.assertFalse(blacklist.hit(""))

    def test_disabled_switch_never_hits_and_produces_no_sql(self):
        db.set_setting("blacklist_enabled", 0)
        self.assertFalse(blacklist.enabled())
        self.assertFalse(blacklist.active())
        self.assertFalse(blacklist.hit("FC2PPV-123456.mp4"))
        clause, params = blacklist.sql_clause("f")
        self.assertEqual(clause, "")
        self.assertEqual(params, [])

    def test_empty_keyword_list_disables_effect_but_keeps_switch_on(self):
        db.set_setting("video_blacklist", "")
        self.assertTrue(blacklist.enabled())
        self.assertFalse(blacklist.active())
        self.assertFalse(blacklist.hit("FC2PPV-1.mp4"))

    def test_sql_clause_covers_three_name_fields(self):
        clause, params = blacklist.sql_clause("f")
        self.assertIn("f.filename", clause)
        self.assertIn("title_guess", clause)
        self.assertIn("title_cn", clause)
        self.assertNotIn("f.path", clause)      # 路径不参与，避免整棵子树被隐藏
        self.assertTrue(clause.startswith("NOT ("))
        self.assertEqual(len(params), len(blacklist.keywords()) * 3)

    def test_like_wildcards_in_keyword_are_escaped(self):
        db.set_setting("video_blacklist", "100%_real")
        clause, params = blacklist.sql_clause("f")
        self.assertIn("\\%\\_real", params[0])


class BlacklistApiTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = api.Api()
        self.ok = self.media("葬送的芙莉莲 - 01.mkv", title_guess="葬送的芙莉莲")
        self.bad = self.media("FC2PPV-123456.mp4", title_guess="FC2PPV-123456")

    def _watched(self, title):
        db.execute(
            "INSERT INTO watched_list(title, title_cn, match_by, source) VALUES (?, ?, 'none', 'manual')",
            (title, title),
        )

    def test_media_page_hides_blacklisted(self):
        data = self.api.get_media_files_page({"page": 1, "page_size": 50})
        names = [r["filename"] for r in data["rows"]]
        self.assertIn("葬送的芙莉莲 - 01.mkv", names)
        self.assertNotIn("FC2PPV-123456.mp4", names)
        self.assertEqual(data["total"], 1, "筛选总数应与实际行数一致，不能还报 2")
        self.assertEqual(data["total_all"], 1, "库内总数也应扣掉黑名单文件")

    def test_media_tree_and_poster_wall_hide_blacklisted(self):
        tree = self.api.get_media_tree({"mode": "series"})
        self.assertNotIn("FC2PPV", str(tree))
        wall = self.api.get_poster_wall({})
        self.assertNotIn("FC2PPV", str(wall["cards"]))
        self.assertEqual(wall["total"], 1)

    def test_watched_list_hides_rows_without_deleting_them(self):
        self._watched("FC2PPV-123456")
        self._watched("葬送的芙莉莲")
        titles = [r["title"] for r in self.api.get_watched_list(0)]
        self.assertEqual(titles, ["葬送的芙莉莲"])
        db.set_setting("blacklist_enabled", 0)
        self.assertEqual(len(self.api.get_watched_list(0)), 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 2)

    def test_history_and_continue_are_filtered(self):
        for item in (self.ok, self.bad):
            db.execute(
                "INSERT INTO watch_progress(file_id, position_ms, completed) VALUES (?, 1000, 0)",
                (item["id"],),
            )
            db.execute(
                "INSERT INTO watch_sessions(file_id, started_at, max_ms) VALUES (?, ?, 1000)",
                (item["id"], "2026-11-15 10:00:00"),
            )
        cont = [r["filename"] for r in self.api.get_continue_watching(50)]
        self.assertEqual(cont, ["葬送的芙莉莲 - 01.mkv"])
        hist = [r["filename"] for r in self.api.get_history(50)]
        self.assertEqual(hist, ["葬送的芙莉莲 - 01.mkv"])

    def test_stats_exclude_blacklisted_files(self):
        self.assertEqual(self.api.get_stats()["files"], 1)

    def test_switch_off_restores_everything(self):
        db.set_setting("blacklist_enabled", 0)
        data = self.api.get_media_files_page({"page": 1, "page_size": 50})
        self.assertEqual(data["total"], 2)
        self.assertEqual(self.api.get_stats()["files"], 2)

    def test_watchlist_keeps_item_but_skips_blacklisted_files(self):
        db.execute("INSERT INTO watchlist(title, downloaded, sort_order) VALUES ('FC2PPV', 0, 1)")
        data = api.watchlist.items("")
        row = [r for r in data["items"] if r["title"] == "FC2PPV"][0]
        self.assertEqual(row["match_count"], 0, "清单条目要保留，但不能匹配到黑名单视频")

    def test_everything_search_filters_rows(self):
        fake = {
            "ok": True, "source": "library", "hint": "",
            "rows": [{"name": "FC2PPV-1.mp4"}, {"name": "正常番 01.mkv"}],
        }
        with mock.patch.object(api.everything, "search", return_value=fake):
            result = self.api.everything_search("mp4", 10)
        self.assertEqual([r["name"] for r in result["rows"]], ["正常番 01.mkv"])
        self.assertEqual(result["hidden"], 1)

    def test_save_settings_accepts_blacklist_keys(self):
        saved = self.api.save_settings(
            {"blacklist_enabled": 0, "video_blacklist": "abc,def"}
        )
        self.assertEqual(str(saved["blacklist_enabled"]), "0")
        self.assertEqual(saved["video_blacklist"], "abc,def")
        self.assertFalse(blacklist.enabled())


class BlacklistFrontendTests(unittest.TestCase):
    def test_settings_page_has_blacklist_editor_and_switch(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("set-video_blacklist", js)
        self.assertIn("switchHtml('blacklist_enabled'", js)
        self.assertIn("'video_blacklist'", js)

    def test_cover_uri_is_fetched_through_cache_helper(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("coverUris(", js)
        # 只允许 coverUris() 内部直接调后端的 cover_data_uris，其它视图统一走缓存
        self.assertEqual(js.count("call('cover_data_uris'"), 1)

    def test_blacklist_card_states_it_does_not_delete(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn("不删本地文件", js)
        self.assertIn("不删追剧清单条目", js)


if __name__ == "__main__":
    unittest.main()
