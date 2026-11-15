"""封面后台抓取：解析规则、缓存、降级、非定时性与 data URI 契约。"""
from __future__ import annotations

import unittest
from unittest import mock

from app import covers
from tests.base import IsolatedDbTestCase

from app.api import Api

AGEDM_HTML = """
<main>
<div class="card cata_video_item py-4">
  <div class="video_cover"><div class="video_cover_wrapper">
    <a href="http://www.agedm.io/detail/20230207" class="d-block" title="葬送的芙莉莲"><img class="video_thumbs lazyload" src="https://img.example/thumb.jpg" data-original="https://img.example/hd.jpg" alt="葬送的芙莉莲"></a>
  </div></div>
  <h5 class="card-title"><a href="#">葬送的芙莉莲</a></h5>
</div>
<div class="card cata_video_item py-4">
  <a href="#" title="完全不相干的作品"><img class="video_thumbs" src="https://img.example/other.jpg"></a>
</div>
</main>
"""
SEED_HTML = """
<a target="_blank" title="间谍过家家" class="image" href="/movies/1/"><img src="https://sh1.example/p1.webp" alt="间谍过家家"></a>
<a target="_blank" title="别的电影" class="image" href="/movies/2/"><img src="https://sh1.example/p2.webp" alt="别的电影"></a>
"""


class CoverParseTests(unittest.TestCase):

    def test_agedm_picks_matching_title_and_hd_first(self):
        hit = covers.parse_agedm(AGEDM_HTML, "葬送的芙莉莲")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0][0], "https://img.example/hd.jpg")
        self.assertIn("https://img.example/thumb.jpg", hit[0])
        self.assertEqual(hit[1], "葬送的芙莉莲")

    def test_agedm_no_match_returns_none(self):
        self.assertIsNone(covers.parse_agedm(AGEDM_HTML, "不存在的番剧"))

    def test_seeduck_pairs_title_with_image(self):
        hit = covers.parse_seeduck(SEED_HTML, "间谍过家家")
        self.assertEqual(hit[0], ["https://sh1.example/p1.webp"])

    def test_strip_season_for_search(self):
        self.assertEqual(covers._strip_season("葬送的芙莉莲 第二季"), "葬送的芙莉莲")
        self.assertEqual(covers._strip_season("间谍过家家 S2"), "间谍过家家")

    def test_cover_key_strips_season_and_episode_suffix(self):
        cases = {
            "间谍过家家 第三季": "间谍过家家",
            "葬送的芙莉莲 第二季": "葬送的芙莉莲",
            "Nagasarete Airantou 蓝兰岛漂流记 01": "Nagasarete Airantou 蓝兰岛漂流记",
            "[Group] Show - 12": "[Group] Show",
            "第 08 集": "第 08 集",
            "CLANNAD": "CLANNAD",
            "怪兽8号": "怪兽8号",
            "10天的爱人 10-Day Lover2023": "10天的爱人 10-Day Lover2023",
        }
        for raw, expected in cases.items():
            self.assertEqual(covers.cover_key(raw), expected, raw)

    def test_local_cover_uses_normalized_key_and_legacy_hash(self):
        covers.config.COVER_DIR.mkdir(parents=True, exist_ok=True)
        covers.cache_path("间谍过家家").write_bytes(b"x" * 1024)
        self.assertTrue(covers.local_cover("间谍过家家 第三季"))
        self.assertTrue(covers.local_cover("间谍过家家 第 12 集"))

    def test_cache_path_is_stable_hash_not_raw_title(self):
        a = covers.cache_path("葬送的芙莉莲/第二季")
        b = covers.cache_path("葬送的芙莉莲/第二季")
        self.assertEqual(a, b)
        self.assertTrue(a.name.endswith(".jpg"))
        self.assertNotIn("葬送", a.name)


class CoverFlowTests(IsolatedDbTestCase):

    def tearDown(self):
        # start_job 用 mock 掉 Thread 时不会跑 work()，需要手动复位模块级状态，
        # 否则会污染后续用例（running 一直是 True）
        with covers._job_lock:
            covers._JOB.update(running=False, cancel=False, done=0, ok=0, failed=0)
        super().tearDown()

    def test_fetch_one_uses_cache_without_network(self):
        config_dir = covers.config.COVER_DIR
        config_dir.mkdir(parents=True, exist_ok=True)
        p = covers.cache_path(" CLANNAD ")
        p.write_bytes(b"x" * 1024)
        with mock.patch.object(covers, "search_cover", side_effect=AssertionError("不应联网")):
            r = covers.fetch_one("CLANNAD")
        self.assertTrue(r["ok"])
        self.assertTrue(r["cached"])

    def test_fetch_one_tries_all_candidate_urls(self):
        with mock.patch.object(covers, "search_cover", return_value={
                "ok": True, "urls": ["http://a/1.jpg", "http://b/2.jpg"],
                "source": "agedm", "hit": "X"}), \
             mock.patch.object(covers, "download_cover", side_effect=[False, True]) as dl, \
             mock.patch.object(covers, "save_cover_record") as save:
            r = covers.fetch_one("某作品", force=True)
        self.assertTrue(r["ok"])
        self.assertEqual(dl.call_count, 2)
        save.assert_called_once()

    def test_fetch_one_degrades_when_no_match(self):
        with mock.patch.object(covers, "search_cover", return_value={"ok": False, "error": "无"}), \
             mock.patch.object(covers, "download_cover") as dl:
            r = covers.fetch_one("某作品", force=True)
        self.assertFalse(r["ok"])
        dl.assert_not_called()

    def test_cover_map_only_includes_cached(self):
        covers.config.COVER_DIR.mkdir(parents=True, exist_ok=True)
        covers.cache_path("A").write_bytes(b"x" * 1024)
        out = covers.cover_map(["A", "B"])
        self.assertIn("A", out)
        self.assertNotIn("B", out)

    def test_resolve_cover_bridges_card_title_and_fetched_title(self):
        """抓取标题带季标记、卡片标题不带时，仍要能显示同一张封面。"""
        from app import db
        covers.config.COVER_DIR.mkdir(parents=True, exist_ok=True)
        saved = covers.cache_path("间谍过家家 第三季").name
        (covers.config.COVER_DIR / saved).write_bytes(b"x" * 1024)
        db.execute(
            "INSERT INTO subjects(title_cn, cover_local, source, confidence, created_at, updated_at) "
            "VALUES (?, ?, 'agedm', 0.5, ?, ?)",
            ("间谍过家家 第三季", saved, "2026-01-01", "2026-01-01"),
        )
        self.assertEqual(covers.resolve_cover("间谍过家家 第三季"), saved)
        self.assertEqual(covers.resolve_cover("间谍过家家"), saved)
        self.assertEqual(covers.cover_map(["间谍过家家"]).get("间谍过家家"), saved)
        out = Api().cover_data_uris(["间谍过家家"])
        self.assertTrue(out["间谍过家家"].startswith("data:image/jpeg;base64,"))

    def test_resolve_cover_ignores_missing_files(self):
        from app import db
        db.execute(
            "INSERT INTO subjects(title_cn, cover_local, source, confidence, created_at, updated_at) "
            "VALUES ('幽灵作品', 'not-there.jpg', 'agedm', 0.5, '2026-01-01', '2026-01-01')"
        )
        self.assertEqual(covers.resolve_cover("幽灵作品"), "")

    def test_status_is_not_running_by_default(self):
        self.assertFalse(covers.status()["running"])

    def test_cover_titles_deduplicates_and_includes_watched(self):
        self.media("A - 01.mkv", title_cn="作品A")
        self.media("A - 02.mkv", title_cn="作品A")
        self.execute = None
        from app import db
        db.execute("INSERT INTO watched_list(title) VALUES ('看过的作品B')")
        titles = covers.cover_titles()
        self.assertEqual(titles.count("作品A"), 1)
        self.assertIn("看过的作品B", titles)

    def test_clean_candidate_rejects_junk_ids_and_release_tags(self):
        self.assertEqual(covers.clean_candidate("FC2PPV-3094718"), "")
        self.assertEqual(covers.clean_candidate("SSIS-12345"), "")
        self.assertEqual(covers.clean_candidate("[Group] Show [1080p].mkv"), "")
        self.assertEqual(covers.clean_candidate("CLANNAD"), "CLANNAD")

    def test_clean_candidate_rejects_marker_only_rows_from_real_list(self):
        # 用户真实清单里有大量「S01 （完）」「- 完」「剧场版 （完）」这类残缺行
        for junk in ("- 完", "Q - 完", "S01 （完）", "剧场版 （完）", "- （完）", "\u200c- 完"):
            self.assertEqual(covers.clean_candidate(junk), "", junk)
        for good in ("间谍过家家 第二季", "CLANNAD After Story （完）", "门徒", "redline"):
            self.assertEqual(covers.clean_candidate(good), good.strip("\u200c").strip(), good)

    def test_cover_titles_prioritizes_chinese_names_from_watched_list(self):
        # 英文文件名在本地库里数量极多，但刮削源只对中文名有稳定结果
        for i in range(12):
            self.media(f"Goblin Slayer - {i:02d}.mkv", title_guess="Goblin Slayer")
        from app import db
        db.execute("INSERT INTO watched_list(title, title_cn) VALUES ('原文本', '葬送的芙莉莲')")
        titles = covers.cover_titles()
        self.assertEqual(titles[0], "葬送的芙莉莲")
        self.assertIn("Goblin Slayer", titles)

    def test_cover_titles_puts_poster_card_labels_first(self):
        # 封面缓存按抓取标题哈希，只有先抓卡片标题才能在剧集墙直接显示
        for i in range(3):
            self.media(f"Frieren - {i:02d}.mkv", title_cn="葬送的芙莉莲")
        from app import db
        db.execute("INSERT INTO watched_list(title) VALUES ('别的中文作品')")
        self.assertEqual(covers.cover_titles()[0], "葬送的芙莉莲")

    def test_cover_titles_drops_junk_ids(self):
        from app import db
        db.execute("INSERT INTO watched_list(title) VALUES ('FC2PPV-3094718')")
        self.assertNotIn("FC2PPV-3094718", covers.cover_titles())

    def test_start_job_is_one_shot_and_not_scheduled(self):
        """必须用户主动触发；不能有任何自动启动/定时逻辑。"""
        import inspect
        import app.covers as mod
        src = inspect.getsource(mod)
        self.assertIn("def start_job", src)
        self.assertNotIn("Timer(", src)
        self.assertNotIn("schedule", src.lower().replace("scheduled", ""))  # 允许文档里出现「非定时」字样
        with mock.patch.object(covers, "cover_titles", return_value=["A"]), \
             mock.patch.object(covers, "fetch_one", return_value={"ok": True, "cached": True, "file": "a.jpg"}), \
             mock.patch.object(covers.threading, "Thread") as thread:
            r = covers.start_job()
        self.assertTrue(r["ok"])
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])


class CoverApiContractTests(IsolatedDbTestCase):

    def test_cover_data_uris_returns_data_uri(self):
        covers.config.COVER_DIR.mkdir(parents=True, exist_ok=True)
        covers.cache_path("电锯人").write_bytes(b"\xff\xd8\xff" + b"x" * 700)
        api = Api()
        out = api.cover_data_uris(["电锯人", "未缓存"])
        self.assertTrue(out["电锯人"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("未缓存", out)

    def test_everything_capabilities_reports_es_missing(self):
        from app import everything
        cap = everything.capabilities()
        self.assertTrue(cap["library_fallback"])
        self.assertIn("es_available", cap)


if __name__ == "__main__":
    unittest.main()
