"""中文名匹配引擎测试：离线词典、季数映射、用户修正优先级、结果写回。"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from app import config, db, metadata
from tests.base import IsolatedDbTestCase


class NormalizeTests(unittest.TestCase):
    def test_case_width_brackets_and_year(self):
        self.assertEqual(metadata.normalize("Ａｔｔａｃｋ  ｏｎ  Ｔｉｔａｎ"), "attack on titan")
        self.assertEqual(metadata.normalize("Frieren (2023) [1080p]"), "frieren")
        self.assertEqual(metadata.normalize("SPY×FAMILY"), "spy family")

    def test_noise_tags_removed(self):
        self.assertEqual(metadata.normalize("Show WEB-DL x265 10bit AAC"), "show")

    def test_empty_input(self):
        self.assertEqual(metadata.normalize(""), "")
        self.assertEqual(metadata.normalize(None), "")

    def test_strip_season(self):
        self.assertEqual(metadata.strip_season("Sono Bisque Doll wa Koi wo Suru S2"),
                         "sono bisque doll wa koi wo suru")
        self.assertEqual(metadata.strip_season("Show Season 3"), "show")

    def test_season_hint(self):
        cases = {
            "Show S2": 2, "Show Season 3": 3, "第2季": 2, "第2期": 2,
            "第三季": 3, "Show": None, "Sono Bisque Doll wa Koi wo Suru": None,
        }
        for text, expected in cases.items():
            self.assertEqual(metadata.season_hint(text), expected, text)


class DictionaryTests(IsolatedDbTestCase):
    def test_builtin_dictionary_is_real_and_well_formed(self):
        items = metadata.builtin_items()
        self.assertGreaterEqual(len(items), 50)
        for item in items:
            self.assertTrue(item["title_cn"])
            self.assertEqual(item["source"], "builtin")

    def test_builtin_loaded_once(self):
        """首次写入词典；重复调用不应重复插入。"""
        total = db.scalar("SELECT COUNT(*) FROM name_alias")
        self.assertGreater(total, 50)
        self.assertEqual(metadata.ensure_builtin_loaded(), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM name_alias"), total)

    def test_lookup_local_hits_english_romaji_and_chinese(self):
        self.assertEqual(metadata.lookup_local("Attack on Titan")["title_cn"], "进击的巨人")
        self.assertEqual(metadata.lookup_local("Frieren")["title_cn"], "葬送的芙莉莲")
        self.assertEqual(metadata.lookup_local("进击的巨人")["title_cn"], "进击的巨人")

    def test_lookup_season_returns_second_season_name(self):
        self.assertEqual(metadata.lookup_season("Sono Bisque Doll wa Koi wo Suru", 2),
                         "更衣人偶坠入爱河 第二季")

    def test_unlisted_season_falls_back_to_base_title_without_inventing_a_name(self):
        """词典没有收录该季时，不能把第一季中文名硬套成第二季，也不编造季名。"""
        self.assertIsNone(metadata.lookup_local("Frieren S2", exact_only=True))
        r = metadata.match_one("Frieren S2", allow_online=False)
        self.assertTrue(r["matched"])
        self.assertEqual(r["by"], "base")
        self.assertEqual(r["title_cn"], "葬送的芙莉莲")

    def test_match_one_native_chinese(self):
        r = metadata.match_one("孤独摇滚！")
        self.assertTrue(r["matched"])
        self.assertEqual(r["by"], "native")

    def test_match_one_unknown_foreign_title(self):
        r = metadata.match_one("Totally Unknown Show XYZ", allow_online=False)
        self.assertFalse(r["matched"])
        self.assertEqual(r["title_cn"], "Totally Unknown Show XYZ")

    def test_local_only_mode_reports_online_unavailable(self):
        db.set_setting("metadata_source", "local_only")
        metadata.reset_online()
        out = metadata.match_titles(["Totally Unknown Show XYZ"])
        self.assertFalse(out["online_available"], "local_only 模式下不应宣称在线可用")
        self.assertEqual(out["total"], 1)
        self.assertFalse(out["results"]["Totally Unknown Show XYZ"]["matched"])

    def test_match_titles_deduplicates_input(self):
        out = metadata.match_titles(["Frieren", "Frieren", " frieren "])
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["matched"], 1)

    def test_online_result_is_learned_on_the_spot(self):
        fake = mock.Mock(side_effect=lambda title: metadata._entry(
            "在线查到的中文名", title, (title,), 12345, 1, "online"))
        with mock.patch.object(metadata, "search_online", fake):
            out = metadata.match_titles(["Brand New Foreign Show"], allow_online=True)
            self.assertEqual(out["by_online"], 1)
            self.assertEqual(metadata.lookup_local("Brand New Foreign Show")["title_cn"],
                             "在线查到的中文名")

    def test_user_override_beats_builtin(self):
        metadata.remember("Attack on Titan", "我自定义的名字", source="user")
        metadata.rebuild_index()
        self.assertEqual(metadata.lookup_local("Attack on Titan")["title_cn"], "我自定义的名字")
        metadata.ensure_builtin_loaded()
        self.assertEqual(metadata.lookup_local("Attack on Titan")["title_cn"], "我自定义的名字")

    def test_apply_to_media_counts_real_changes(self):
        self.media("Frieren - 01.mkv", title_guess="Frieren")
        out = metadata.match_titles(["Frieren", "Attack on Titan"])
        changed = metadata.apply_to_media(out["results"])
        self.assertEqual(changed, 1, "只有真正更新了行的条目才该计入")
        self.assertEqual(db.scalar("SELECT title_cn FROM media_files"), "葬送的芙莉莲")

    def test_rematch_all_updates_media_and_watched(self):
        self.media("Frieren - 01.mkv", title_guess="Frieren")
        db.execute("INSERT INTO watched_list(title, title_cn, match_by) VALUES (?, ?, ?)",
                   ("Attack on Titan", "", "none"))
        out = metadata.rematch_all()
        self.assertEqual(out["applied_media"], 1)
        self.assertEqual(db.scalar("SELECT title_cn FROM media_files"), "葬送的芙莉莲")
        self.assertEqual(db.scalar("SELECT title_cn FROM watched_list"), "进击的巨人")

    def test_stats_report_consistent_numbers(self):
        stats = metadata.stats()
        self.assertGreater(stats["builtin_terms"], 50)
        self.assertEqual(stats["alias_total"], stats["builtin"] + stats["learned"])
        self.assertIn("mode", stats)

    def test_import_alias_file_accepts_real_payload(self):
        config_path = self.tmp / "alias_import.json"
        config_path.write_text(json.dumps({
            "version": 1,
            "items": [{"raw_title": "My Custom Title", "title_cn": "我的自定义作品",
                       "title_orig": "My Custom Title", "bgm_id": None}],
        }, ensure_ascii=False), encoding="utf-8")
        config.ALIAS_IMPORT_FILE = config_path
        result = metadata.import_alias_file()
        self.assertTrue(result["ok"])
        self.assertEqual(result["imported"], 1)
        self.assertEqual(metadata.lookup_local("My Custom Title")["title_cn"], "我的自定义作品")


if __name__ == "__main__":
    unittest.main()
