"""视频黑名单：默认开启、名称包含匹配、全位置过滤、开关关闭后恢复。

需求原话（用户 2026-11-15）：增加一个设置，黑名单例如「daas,sone,...,ymdd」，
在所有位置默认不展示名称包含黑名单字段（不区分大小写）的视频；增加按钮开关，
默认开启，关闭后正常展示所有名称的视频。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import api, blacklist, db, library_tree
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
        # 长词 / 中文词：每词 3 个字段各 1 个 LIKE 参数；
        # 短英文词：每字段 3 个 GLOB 参数（包含 + 左右字母边界）。
        expected = sum(9 if blacklist._is_short_ascii(kw) else 3
                       for kw in blacklist.keywords())
        self.assertEqual(len(params), expected)

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


class BlacklistNormalizationTests(IsolatedDbTestCase):
    """连接符归一化 + 目录名派生标题：用户报「剧集墙还能看到黑名单剧集」的两个真因。"""

    def setUp(self):
        super().setUp()

    def test_hyphenated_name_matches_compact_keyword(self):
        # 词表里写的是 FC2PPV，实际文件名是 FC2-PPV-1288053
        self.assertTrue(blacklist.hit("FC2-PPV-1288053.mp4"))
        self.assertTrue(blacklist.hit("SSIS-001.mkv"))
        self.assertTrue(blacklist.hit("mvsd.naiad.mp4"))       # 词表里本来就带点
        self.assertTrue(blacklist.hit("MIDA_077.mp4"))

    def test_normalization_does_not_hit_unrelated_titles(self):
        for name in ("葬送的芙莉莲 01.mkv", "更衣人偶坠入爱河", "间谍过家家 S02E03.mkv",
                     "Spy x Family - 05.mkv"):
            self.assertFalse(blacklist.hit(name), name)

    def test_normalize_name_strips_only_separators(self):
        self.assertEqual(blacklist.normalize_name("FC2-PPV_1288.053 ·A"),
                         "fc2ppv1288053a")

    def test_keywords_keep_user_spelling_but_dedupe_by_normalized_form(self):
        db.set_setting("video_blacklist", "FC2PPV，fc2-ppv，三上悠亚")
        # 归一化后 fc2ppv / fc2ppv 视为同一个词，只留第一次写法
        self.assertEqual(blacklist.keywords(), ["fc2ppv", "三上悠亚"])
        self.assertEqual(blacklist.norm_keywords(), ["fc2ppv", "三上悠亚"])

    def test_folder_derived_group_title_is_hidden_in_wall_and_tree(self):
        """文件名干净、但发布目录名带黑名单词：卡片/分组也要消失。"""
        client = api.Api()
        folder = self.tmp / "三上悠亚 精选"
        folder.mkdir()
        # 目录里有 2 个文件才会被当成发布目录、用目录名做作品名
        for idx in (1, 2):
            name = f"clip {idx}.mkv"
            self.media(name, path=str(folder / name), title_guess=f"clip {idx}",
                       title_cn=f"一段视频{idx}")
        other = self.tmp / "葬送的芙莉莲"
        other.mkdir()
        self.media("葬送的芙莉莲 - 01.mkv", path=str(other / "葬送的芙莉莲 - 01.mkv"),
                   title_guess="葬送的芙莉莲", title_cn="葬送的芙莉莲")
        wall = client.get_poster_wall({})
        titles = [c["title"] for c in wall["cards"]]
        self.assertIn("葬送的芙莉莲", titles)
        self.assertFalse([t for t in titles if "三上悠亚" in t], titles)
        tree = client.get_media_tree({"mode": "series"})
        labels = [g["label"] for g in tree["groups"]]
        self.assertFalse([l for l in labels if "三上悠亚" in l], labels)

    def test_switch_off_restores_folder_derived_group(self):
        client = api.Api()
        folder = self.tmp / "三上悠亚 精选"
        folder.mkdir()
        for idx in (1, 2):
            name = f"clip {idx}.mkv"
            self.media(name, path=str(folder / name), title_guess=f"clip {idx}")
        db.set_setting("blacklist_enabled", 0)
        wall = client.get_poster_wall({})
        self.assertTrue([c["title"] for c in wall["cards"] if "三上悠亚" in c["title"]])


class BlacklistVariantTests(IsolatedDbTestCase):
    """用户报「黑名单里有三上悠亚，为什么剧集墙里还是有」：

    片名来自日语资源，写的是新字体 `三上悠亜`（U+4E9C），词表里是简体
    `三上悠亚`（U+4E9A）。三个码位互不相等，纯 `in` 判断永远不命中。
    同时收紧短英文词的词边界，避免 `future`(ure) / `BDripX264`(ipx) 被误伤。
    """

    def test_japanese_shinjitai_hits_simplified_keyword(self):
        self.assertTrue(blacklist.hit("三上悠亜"))
        self.assertTrue(blacklist.hit("三上悠亞"))
        self.assertTrue(blacklist.hit("三上悠亚"))
        self.assertTrue(blacklist.hit("三上悠亜 精选"))   # 目录名派生标题

    def test_normalize_folds_variants_and_fullwidth(self):
        self.assertEqual(blacklist.normalize_name("三上悠亜"), "三上悠亚")
        self.assertEqual(blacklist.normalize_name("ＡＢＰ１２３"), "abp123")

    def test_short_ascii_keyword_needs_word_boundary(self):
        # 正常番：ure / ipx 只是更长单词的一部分，必须放行
        for name in ("The Return Of The Native (1994) DVDRip",
                     "Time Trap-Escape to the future ITA-ENG",
                     "徒然喜欢你 Tsurezure Children",
                     "[漏勺rip] Gate Jieitai 01 [BDrip X264 10bit].mkv",
                     "Ownerless.Flower.2014.720p.UNCUT.HDRip"):
            self.assertFalse(blacklist.hit(name), name)

    def test_short_ascii_keyword_still_hits_real_codes(self):
        # 番号的正常写法：连字符 / 下划线 / 点 / @ / 词+数字粘连 都要能挡住
        for name in ("IPX-123.mp4", "SSIS-001.mkv", "MIDA_077.mp4",
                     "kfa55.com@PRED733.mkv", "WoXav.Com@IPX.mp4",
                     "(S1)(SSNI-463) 某某.mkv"):
            self.assertTrue(blacklist.hit(name), name)

    def test_sql_clause_pushes_short_word_boundary_down(self):
        """SQL 一旦把行删掉，Python 就没机会放回来 → 短词必须用 GLOB 做边界。"""
        sql, params = blacklist.sql_clause("f")
        self.assertIn("GLOB", sql.upper())
        self.assertIn("*[a-z]ure*", params)
        self.assertIn("*ure[a-z]*", params)
        # 长词 / 中文词仍然走便宜的 LIKE
        self.assertIn("%fc2ppv%", params)
        self.assertIn("%三上悠亚%", params)

    def test_sql_clause_really_keeps_future_and_drops_codes(self):
        self.media("The Return Of The Native (1994) DVDRip.mkv", title_guess="Return")
        self.media("Time Trap future ITA-ENG.mkv", title_guess="Time Trap")
        self.media("SSIS-001.mkv", title_guess="SSIS-001")
        client = api.Api()
        names = [r["filename"] for r in client.get_media_files_page({"page_size": 0})["rows"]]
        self.assertTrue(any("Return" in n for n in names), names)
        self.assertTrue(any("Time Trap" in n for n in names), names)
        self.assertFalse(any("SSIS-001" in n for n in names), names)

    def test_wall_hides_group_titled_with_shinjitai(self):
        client = api.Api()
        folder = self.tmp / "三上悠亜"
        folder.mkdir()
        for idx in (1, 2):
            name = f"clip {idx}.mkv"
            self.media(name, path=str(folder / name), title_guess=f"clip {idx}")
        titles = [c["title"] for c in client.get_poster_wall({})["cards"]]
        self.assertFalse([t for t in titles if "三上" in t], titles)
        db.set_setting("blacklist_enabled", 0)
        titles = [c["title"] for c in client.get_poster_wall({})["cards"]]
        self.assertTrue([t for t in titles if "三上" in t], titles)

    def test_prune_also_checks_season_child_labels(self):
        """作品目录名叫「合集」、季目录才带词时，只看 label 会漏。"""
        groups = [
            {"label": "合集", "sub": "", "children": [{"label": "三上悠亜 精选"}]},
            {"label": "葬送的芙莉莲", "sub": "", "children": [{"label": "第1季"}]},
        ]
        kept = api.Api._prune_blacklisted_groups(groups)
        self.assertEqual([g["label"] for g in kept], ["葬送的芙莉莲"])


class MediaCacheTests(IsolatedDbTestCase):
    """媒体库 / 剧集墙的结果缓存：必须真的省掉重算，且数据一动就失效。"""

    def setUp(self):
        super().setUp()
        self.api = api.Api()
        self._add("葬送的芙莉莲")

    def _add(self, title):
        """每个作品一个独立目录：同目录下的文件会被归并成同一张卡片。"""
        folder = self.tmp / title
        folder.mkdir(exist_ok=True)
        name = f"{title} - 01.mkv"
        return self.media(name, path=str(folder / name),
                          title_guess=title, title_cn=title)

    def test_second_call_is_served_from_cache(self):
        first = self.api.get_poster_wall({})
        self.assertTrue(first["cards"])
        self.assertTrue(api._MEDIA_CACHE)
        # 缓存命中后不应该再跑 SQL：把 build_tree 打断，命中时不会走到它
        with mock.patch.object(library_tree, "build_tree",
                               side_effect=AssertionError("不应重算")):
            second = self.api.get_poster_wall({})
        self.assertEqual([c["title"] for c in second["cards"]],
                         [c["title"] for c in first["cards"]])

    def test_write_invalidates_cache(self):
        self.api.get_poster_wall({})
        self._add("进击的巨人")
        titles = [c["title"] for c in self.api.get_poster_wall({})["cards"]]
        self.assertIn("进击的巨人", titles)

    def test_other_options_do_not_share_cache(self):
        self.api.get_poster_wall({})
        with mock.patch.object(library_tree, "build_tree",
                               side_effect=library_tree.build_tree) as build:
            self.api.get_poster_wall({"limit": 1})
        build.assert_called_once()

    def test_disabling_blacklist_invalidates_media_cache(self):
        db.set_setting("video_blacklist", "芙莉莲")
        self.assertEqual(self.api.get_poster_wall({})["total"], 0)
        db.set_setting("blacklist_enabled", 0)
        self.assertEqual(self.api.get_poster_wall({})["total"], 1)


if __name__ == "__main__":
    unittest.main()
