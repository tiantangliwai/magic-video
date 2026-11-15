"""媒体库全量浏览：分页 / 搜索 / 筛选 / 排序 / 点击播放 的行为测试。

所有用例都通过 js_api 的公开接口验证，不直接断言内部 SQL 实现。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import db, potplayer
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class MediaBrowserTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def _seed(self, count: int) -> None:
        rows = []
        for i in range(count):
            name = f"Show {i:04d} - {i:02d}.mkv"
            rows.append((str(self.tmp / name), name, f"Show {i:04d}", f"Show {i:04d}", "new"))
        db.executemany(
            "INSERT INTO media_files(path, filename, title_raw, title_guess, status) "
            "VALUES (?, ?, ?, ?, ?)", rows,
        )

    def test_empty_library_reports_zero_and_one_empty_page(self):
        page = self.api.get_media_files_page({})
        self.assertEqual(page["total"], 0)
        self.assertEqual(page["total_all"], 0)
        self.assertEqual(page["rows"], [])
        self.assertEqual(page["pages"], 1)

    def test_pagination_exposes_full_count_beyond_legacy_400_cap(self):
        self._seed(450)
        first = self.api.get_media_files_page({"page": 1, "page_size": 50})
        self.assertEqual(first["total"], 450)
        self.assertEqual(first["total_all"], 450)
        self.assertEqual(first["pages"], 9)
        self.assertEqual(len(first["rows"]), 50)
        last = self.api.get_media_files_page({"page": 9, "page_size": 50})
        self.assertEqual(len(last["rows"]), 50)
        self.assertEqual(last["from"], 401)
        self.assertEqual(last["to"], 450)

    def _seed_mixed(self):
        db.execute("INSERT INTO directories(path) VALUES (?)", (str(self.tmp / "Anime"),))
        dir_a = db.scalar("SELECT id FROM directories WHERE path = ?", (str(self.tmp / "Anime"),))
        db.execute("INSERT INTO directories(path) VALUES (?)", (str(self.tmp / "Other"),))
        dir_b = db.scalar("SELECT id FROM directories WHERE path = ?", (str(self.tmp / "Other"),))
        a = self.media(
            "Frieren - 05.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
            title_raw="Frieren 05", path=str(self.tmp / "Anime" / "Season1" / "Frieren - 05.mkv"),
            dir_id=dir_a,
        )
        b = self.media(
            "Attack on Titan - 01.mkv", title_guess="Attack on Titan",
            path=str(self.tmp / "Other" / "Attack on Titan - 01.mkv"), dir_id=dir_b,
        )
        c = self.media(
            "Shingeki - 02.mkv", title_guess="Shingeki", title_cn="进击的巨人",
            path=str(self.tmp / "Anime" / "Season2" / "Shingeki - 02.mkv"), dir_id=dir_a,
        )
        return a, b, c, dir_a, dir_b

    def test_search_covers_filename_cn_guess_raw_and_path(self):
        a, b, c, _, _ = self._seed_mixed()

        def ids(keyword):
            page = self.api.get_media_files_page({"keyword": keyword, "page_size": 0})
            return {r["id"] for r in page["rows"]}

        self.assertEqual(ids("芙莉莲"), {a["id"]})       # 中文名
        self.assertEqual(ids("FRIEREN"), {a["id"]})      # 文件名/原名，大小写不敏感
        self.assertEqual(ids("Season1"), {a["id"]})      # 完整路径
        self.assertEqual(ids("Attack"), {b["id"]})       # 解析出的标题
        self.assertEqual(ids("进击"), {c["id"]})          # 中文名
        self.assertEqual(ids("不存在的片名"), set())

    def test_directory_and_match_filters_combine_with_search(self):
        a, b, c, dir_a, dir_b = self._seed_mixed()
        wrong_dir = self.api.get_media_files_page(
            {"keyword": "Frieren", "directory_id": dir_b, "page_size": 0})
        self.assertEqual(wrong_dir["total"], 0)

        matched = self.api.get_media_files_page({"match_state": "matched", "page_size": 0})
        self.assertEqual({r["id"] for r in matched["rows"]}, {a["id"], c["id"]})
        unmatched = self.api.get_media_files_page({"match_state": "unmatched", "page_size": 0})
        self.assertEqual({r["id"] for r in unmatched["rows"]}, {b["id"]})
        by_dir = self.api.get_media_files_page({"directory_id": dir_a, "page_size": 0})
        self.assertEqual(by_dir["total"], 2)

        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent) VALUES (?, ?, ?)",
                   (b["id"], 120000, 10))
        progress = self.api.get_media_files_page({"match_state": "progress", "page_size": 0})
        self.assertEqual({r["id"] for r in progress["rows"]}, {b["id"]})

    def test_show_all_mode_sorting_and_directory_options(self):
        a, b, c, dir_a, dir_b = self._seed_mixed()
        for file_id, size in ((a["id"], 999), (b["id"], 10), (c["id"], 500)):
            db.execute("UPDATE media_files SET size = ? WHERE id = ?", (size, file_id))

        everything = self.api.get_media_files_page({"page_size": 0})
        self.assertEqual(everything["total"], 3)
        self.assertEqual(len(everything["rows"]), 3)
        self.assertEqual(everything["pages"], 1)
        self.assertEqual((everything["from"], everything["to"]), (1, 3))
        self.assertEqual([d["id"] for d in everything["directories"]], [dir_a, dir_b])

        newest = self.api.get_media_files_page({"page_size": 0})
        self.assertEqual([r["id"] for r in newest["rows"]], [c["id"], b["id"], a["id"]])
        by_name = self.api.get_media_files_page({"sort": "name", "page_size": 0})
        self.assertEqual(
            [r["filename"] for r in by_name["rows"]],
            ["Attack on Titan - 01.mkv", "Frieren - 05.mkv", "Shingeki - 02.mkv"],
        )
        by_size = self.api.get_media_files_page({"sort": "size", "page_size": 0})
        self.assertEqual([r["id"] for r in by_size["rows"]], [a["id"], c["id"], b["id"]])

    def test_unsafe_sort_and_out_of_range_page_are_safe(self):
        self._seed(5)
        weird = self.api.get_media_files_page({"sort": "id; DROP TABLE media_files", "page_size": 0})
        self.assertEqual(weird["total"], 5)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 5)
        far = self.api.get_media_files_page({"page": 999, "page_size": 2})
        self.assertEqual(far["total"], 5)
        self.assertEqual(far["pages"], 3)
        self.assertEqual(far["rows"], [])
        self.assertEqual((far["from"], far["to"]), (0, 0))

    def test_like_wildcards_in_keyword_are_literal(self):
        self._seed_mixed()
        # 没有字面 % 时，单独搜 % 不能变成「匹配全部」的通配符
        self.assertEqual(self.api.get_media_files_page({"keyword": "%", "page_size": 0})["total"], 0)
        special = self.media("Special_100%.mkv", title_guess="Special_100%")
        hit = self.api.get_media_files_page({"keyword": "Special_100%", "page_size": 0})
        self.assertEqual({r["id"] for r in hit["rows"]}, {special["id"]})
        db.execute("UPDATE media_files SET path = ? WHERE id = ?",
                   (r"Z:\UniqueFolder\Clip.mkv", special["id"]))
        path_hit = self.api.get_media_files_page({"keyword": "UniqueFolder\\", "page_size": 0})
        self.assertEqual({r["id"] for r in path_hit["rows"]}, {special["id"]})

    def test_row_from_search_result_opens_player_directly(self):
        video = self.tmp / "Frieren - 05.mkv"
        video.write_bytes(b"")
        record = self.media("Frieren - 05.mkv", path=str(video), title_cn="葬送的芙莉莲")
        page = self.api.get_media_files_page({"keyword": "芙莉莲"})
        row = page["rows"][0]
        self.assertEqual(row["id"], record["id"])
        with mock.patch.object(potplayer, "play", return_value={"ok": True}) as play:
            result = self.api.play_file(row["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(play.call_args[0][0], str(video))


class FrontendFileListTests(unittest.TestCase):
    """前端契约：两个页面共用全量列表组件，且点击文件名即可播放。"""

    def setUp(self):
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/style.css").read_text(encoding="utf-8")

    def test_scan_and_library_pages_share_full_list_component(self):
        self.assertIn("get_media_files_page", self.js)
        self.assertIn("fileListBox", self.js)
        self.assertIn("loadFileList", self.js)
        self.assertIn("renderScan", self.js)
        # 旧的 400 条硬上限必须消失
        self.assertNotIn("get_media_files', keyword, 400", self.js)

    def test_search_filter_and_show_all_controls_exist(self):
        for token in ('id="fileSearch"', 'id="fileDir"', 'id="fileMatch"',
                      'id="fileSort"', 'id="filePageSize"', "file-all"):
            self.assertIn(token, self.js)

    def test_file_name_is_clickable_to_play(self):
        self.assertIn('class="link-btn" data-action="play"', self.js)
        self.assertIn(".link-btn", self.css)
