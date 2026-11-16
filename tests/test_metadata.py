"""中文名匹配引擎测试：离线词典、季数映射、用户修正优先级、结果写回。"""
from __future__ import annotations

import json
import time
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


class _HttpStatusError(Exception):
    """模拟 requests.HTTPError：带 response.status_code，不改测试依赖。"""

    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.response = mock.Mock(status_code=status)


class OnlineScrapeTests(IsolatedDbTestCase):
    """P1-5：刮削限速、缓存（含负缓存）、429 退避、强制重刮。

    这些用例只 mock 网络层 ``requests.get``，``search_online`` 跑真实实现——
    否则限速与缓存分支根本不会被执行，测了等于没测。
    """

    def _run(self, fake_get, titles):
        with mock.patch.object(metadata, "search_online", self.real_search_online), \
                mock.patch("requests.get", side_effect=fake_get):
            return [metadata.search_online(t) for t in titles]

    @staticmethod
    def _resp(items=None):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"list": items or []}
        resp.raise_for_status.return_value = None
        return resp

    def test_hit_is_cached_and_not_requested_twice(self):
        calls = []

        def fake_get(*args, **kwargs):
            calls.append(1)
            return self._resp([{"id": 999, "name": "Cached Show", "name_cn": "缓存作品"}])

        first, second = self._run(fake_get, ["Cached Show", "Cached Show"])
        self.assertEqual(first["title_cn"], "缓存作品")
        self.assertEqual(second["title_cn"], "缓存作品")
        self.assertEqual(len(calls), 1, "第二次应直接命中缓存，不再发包")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM scrape_cache"), 1)

    def test_miss_is_negatively_cached_until_ttl(self):
        calls = []

        def fake_get(*args, **kwargs):
            calls.append(1)
            return self._resp([])

        self._run(fake_get, ["Unknown Show A", "Unknown Show A"])
        self.assertEqual(len(calls), 1, "查无结果也要缓存，避免反复打接口")
        self.assertEqual(db.scalar("SELECT status FROM scrape_cache"), "miss")

        # 负缓存过期后应重新联网（这里直接把过期时间改到过去）
        db.execute("UPDATE scrape_cache SET expires_at = '2000-01-01 00:00:00'")
        self._run(fake_get, ["Unknown Show A"])
        self.assertEqual(len(calls), 2)

    def test_rate_limit_pauses_temporarily_instead_of_blocking_forever(self):
        def fake_get(*args, **kwargs):
            raise _HttpStatusError(429)

        self._run(fake_get, ["Limited Show"])
        self.assertFalse(metadata._online_usable(), "限流期间应暂停在线刮削")
        self.assertFalse(metadata._ONLINE_BLOCKED, "429 不应被当成永久熔断")
        self.assertGreater(metadata.stats()["paused_sec"], 0)

        metadata._PAUSED_UNTIL = 0.0  # 模拟退避到期
        self.assertTrue(metadata._online_usable(), "退避到期后应自动恢复，不需要用户改设置")

    def test_other_network_errors_still_degrade_whole_batch(self):
        """断网/超时维持旧行为：整批快速降级，避免每条都等 3 秒超时。"""

        def fake_get(*args, **kwargs):
            raise TimeoutError("timed out")

        self._run(fake_get, ["Offline Show"])
        self.assertTrue(metadata._ONLINE_BLOCKED)
        self.assertFalse(metadata._online_usable())

    def test_throttle_enforces_minimum_gap(self):
        db.set_setting("bangumi_rate_ms", 120)
        metadata.reset_online()
        stamps = []

        def fake_get(*args, **kwargs):
            stamps.append(time.monotonic())
            return self._resp([])

        self._run(fake_get, ["Rate Show A", "Rate Show B"])
        self.assertEqual(len(stamps), 2)
        self.assertGreaterEqual(stamps[1] - stamps[0], 0.10,
                                "两次发包应至少间隔一个限速窗口")

    def test_force_rescrape_clears_cache_but_keeps_user_edits(self):
        record = self.media("Forced - 01.mkv", title_guess="Forced Show")
        db.execute(
            "UPDATE media_files SET title_cn = ?, match_by = 'user' WHERE id = ?",
            ("我手改的名字", record["id"]),
        )
        db.execute(
            "INSERT INTO scrape_cache(raw_title, status, title_cn) VALUES ('Forced Show', 'hit', '旧缓存')"
        )

        out = metadata.rematch_all(force=True)
        self.assertTrue(out["forced"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM scrape_cache WHERE raw_title = 'Forced Show'"), 0,
                         "强制重刮应清空旧缓存")
        self.assertEqual(
            db.scalar("SELECT title_cn FROM media_files WHERE id = ?", (record["id"],)),
            "我手改的名字",
            "用户手改（match_by='user'）永不被强制重刮覆盖",
        )

    def test_clear_scrape_cache_reports_count(self):
        db.execute("INSERT INTO scrape_cache(raw_title, status) VALUES ('A', 'miss')")
        db.execute("INSERT INTO scrape_cache(raw_title, status) VALUES ('B', 'hit')")
        out = metadata.clear_scrape_cache()
        self.assertTrue(out["ok"])
        self.assertEqual(out["cleared"], 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM scrape_cache"), 0)

    def test_stats_expose_cache_and_pause_state(self):
        stats = metadata.stats()
        self.assertIn("scrape_cache", stats)
        self.assertIn("paused_sec", stats)
        self.assertIn("total", stats["scrape_cache"])


class IdGroupTests(IsolatedDbTestCase):
    """P1-4：作品 ID 作为稳定键，同组共享匹配结果。"""

    def test_same_tmdb_id_shares_title_within_group(self):
        source = self.media("Ep01.mkv", title_guess="Write-Up A", tmdb_id=555)
        db.execute("UPDATE media_files SET title_cn = '共享中文名', match_by = 'local' WHERE id = ?",
                   (source["id"],))
        target = self.media("Ep02.mkv", title_guess="Totally Different B", tmdb_id=555)
        other = self.media("Other.mkv", title_guess="Other Show", tmdb_id=999)

        changed = metadata.apply_id_groups()
        self.assertEqual(changed, 1, "只有同组里缺中文名的那一条该被填充")
        self.assertEqual(
            db.scalar("SELECT title_cn FROM media_files WHERE id = ?", (target["id"],)),
            "共享中文名")
        self.assertEqual(
            db.scalar("SELECT match_by FROM media_files WHERE id = ?", (target["id"],)), "id")
        self.assertIsNone(
            db.scalar("SELECT title_cn FROM media_files WHERE id = ?", (other["id"],)),
            "别的作品 ID 不能被牵连")

    def test_existing_title_is_never_overwritten(self):
        source = self.media("A.mkv", title_guess="A", tmdb_id=777)
        db.execute("UPDATE media_files SET title_cn = '源名字' WHERE id = ?", (source["id"],))
        target = self.media("B.mkv", title_guess="B", tmdb_id=777)
        db.execute("UPDATE media_files SET title_cn = '自己的名字', match_by = 'user' WHERE id = ?",
                   (target["id"],))

        metadata.apply_id_groups()
        self.assertEqual(
            db.scalar("SELECT title_cn FROM media_files WHERE id = ?", (target["id"],)),
            "自己的名字",
            "按 ID 扩散只能填空，不能覆盖已有值（更不能覆盖手改）")

    def test_lookup_by_ids_reads_subjects_first(self):
        db.execute(
            "INSERT INTO subjects(tmdb_id, title_cn, title_orig, verified) VALUES (?, ?, ?, 1)",
            (888, "ID 直查中文名", "ID Original"))
        hit = metadata.lookup_by_ids({"tmdb_id": 888})
        self.assertEqual(hit["title_cn"], "ID 直查中文名")

        r = metadata.match_one("Totally Unknown Show XYZ", allow_online=False,
                               ids={"tmdb_id": 888})
        self.assertTrue(r["matched"])
        self.assertEqual(r["by"], "id")
        self.assertEqual(r["title_cn"], "ID 直查中文名")

    def test_match_one_without_ids_keeps_old_behaviour(self):
        r = metadata.match_one("Totally Unknown Show XYZ", allow_online=False)
        self.assertFalse(r["matched"])


if __name__ == "__main__":
    unittest.main()
