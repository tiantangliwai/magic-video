"""媒体库全量浏览：分页 / 搜索 / 筛选 / 排序 / 点击播放 的行为测试。

所有用例都通过 js_api 的公开接口验证，不直接断言内部 SQL 实现。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import covers, db, potplayer
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

    def test_library_shows_back_button_after_keyword_jump(self):
        """从剧集墙「查看分集」跳进媒体库后要能一键返回，不用重新找一遍。"""
        js = self.js
        self.assertIn("let viewReturn = null", js)
        self.assertIn("function backRow", js)
        self.assertIn("${backRow()}", js)             # 列表卡片里真的插入了这一行
        self.assertIn('data-action="goto-back"', js)
        self.assertIn("case 'goto-back'", js)
        # 只有带关键词的跳转才记录来源；直接点导航进媒体库不显示
        self.assertIn("viewReturn = (prev && prev !== view && VIEW_META[prev])", js)
        self.assertIn("currentView !== 'library' || !viewReturn", js)
        # 来源名取 VIEW_META，不写死「剧集墙」
        self.assertLess(js.index("function backRow"), js.index("function fileListCard"))
        back = js[js.index("case 'goto-back'"):]
        self.assertLess(back.index("viewReturn = null"), back.index("switchView(target)"))


JPEG = b"\xff\xd8\xff" + b"x" * 1024


class CoverFilterTests(IsolatedDbTestCase):
    """媒体库「有封面 / 没封面」筛选：三视图共用同一套判断。

    封面是作品级的，是否有图走 covers.resolve_cover_many（与卡片显示一致），
    这里用真实的 set_manual_cover 落一张缓存图来验证，不 mock 判定逻辑。
    """

    def setUp(self):
        super().setUp()
        self.api = Api()

    def _seed_two(self):
        for title in ("WithCover", "NoCover"):
            db.execute(
                "INSERT INTO media_files(path, filename, title_raw, title_guess, status) "
                "VALUES (?,?,?,?,?)",
                (str(self.tmp / f"{title} - 01.mkv"), f"{title} - 01.mkv",
                 f"{title} 01", title, "new"),
            )

    def test_with_and_no_cover_split_library(self):
        self._seed_two()
        r = covers.set_manual_cover("WithCover", JPEG)
        self.assertTrue(r["ok"])

        def ids(state):
            page = self.api.get_media_files_page({"match_state": state, "page_size": 0})
            return {row["title_guess"] for row in page["rows"]}, page["total"]

        self.assertEqual(ids("with_cover"), ({"WithCover"}, 1))
        self.assertEqual(ids("no_cover"), ({"NoCover"}, 1))
        self.assertEqual(ids(""), ({"WithCover", "NoCover"}, 2))

    def test_no_cover_when_library_has_no_covers_at_all(self):
        self._seed_two()
        # 全库没有封面：with_cover 空，no_cover 全保留（不能因空集合报错或漏掉）
        self.assertEqual(self.api.get_media_files_page(
            {"match_state": "with_cover", "page_size": 0})["total"], 0)
        self.assertEqual(self.api.get_media_files_page(
            {"match_state": "no_cover", "page_size": 0})["total"], 2)

    def test_tree_view_honors_cover_filter(self):
        self._seed_two()
        covers.set_manual_cover("WithCover", JPEG)
        tree = self.api.get_media_tree({"match_state": "with_cover", "mode": "series"})
        self.assertEqual([g.get("label") for g in tree["groups"]], ["WithCover"])
        tree2 = self.api.get_media_tree({"match_state": "no_cover", "mode": "series"})
        self.assertEqual([g.get("label") for g in tree2["groups"]], ["NoCover"])

    def test_poster_wall_honors_cover_filter(self):
        self._seed_two()
        covers.set_manual_cover("WithCover", JPEG)
        wall = self.api.get_poster_wall({"match_state": "with_cover", "limit": 50})
        self.assertEqual([c["title"] for c in wall["cards"]], ["WithCover"])
        wall2 = self.api.get_poster_wall({"match_state": "no_cover", "limit": 50})
        self.assertEqual([c["title"] for c in wall2["cards"]], ["NoCover"])

    def test_manual_cover_on_folder_named_group_is_detected(self):
        """手动封面按作品分组 label 存（剧集墙卡片标题）；当 label 来自文件夹名、
        文件行无中文名时，筛选候选里没有这个 label，卡片能显示却会被判成没封面。
        用户 2026-11-19 反馈「很多手动设封面的剧集被过滤为没封面」。"""
        folder = self.tmp / "葬送的芙莉莲"
        for ep in (1, 2):
            name = f"[Subs]Frieren - {ep:02d}.mkv"
            db.execute(
                "INSERT INTO media_files(path, filename, title_raw, title_guess, status) "
                "VALUES (?,?,?,?,?)",
                (str(folder / name), name, f"[Subs]Frieren {ep:02d}", "Frieren", "scanned"),
            )
        # 分组 label 应来自文件夹名「葬送的芙莉莲」，而不是文件的英文 guess
        tree = self.api.get_media_tree({"mode": "series"})
        self.assertEqual([g["label"] for g in tree["groups"]], ["葬送的芙莉莲"])
        label = tree["groups"][0]["label"]
        # 用户就是照着剧集墙卡片标题设封面的
        self.assertTrue(covers.set_manual_cover(label, JPEG)["ok"])
        # 卡片能看到这张图
        wall = self.api.get_poster_wall({})
        self.assertTrue(wall["covers"].get(label))
        # 筛选也必须认这张封面——这是本轮修复点，改动前会返回 0
        page = self.api.get_media_files_page({"match_state": "with_cover", "page_size": 0})
        self.assertEqual(page["total"], 2)
        page2 = self.api.get_media_files_page({"match_state": "no_cover", "page_size": 0})
        self.assertEqual(page2["total"], 0)

    def test_renamed_work_manual_cover_is_detected(self):
        """改名后封面仍按 group label 存，筛选候选里的 display_title 是新名；
        两者不一致时同样要能命中（改名为放大器场景）。"""
        dir_id = None
        row_id = db.execute(
            "INSERT INTO media_files(path, filename, title_raw, title_guess, title_cn, status) "
            "VALUES (?,?,?,?,?,?)",
            (str(self.tmp / "old - 01.mkv"), "old - 01.mkv", "old 01", "OldName",
             "旧中文名", "scanned"),
        )
        tree = self.api.get_media_tree({"mode": "series"})
        gid = tree["groups"][0]["id"]
        old_label = tree["groups"][0]["label"]
        self.assertEqual(old_label, "旧中文名")
        self.assertTrue(covers.set_manual_cover(old_label, JPEG)["ok"])
        self.assertTrue(self.api.rename_work(gid, "我改的新名")["ok"])
        # 改了名后卡片标题变成新名，封面仍能显示
        wall = self.api.get_poster_wall({})
        self.assertTrue(wall["covers"].get("我改的新名"))
        # 筛选也应命中这张手动封面
        page = self.api.get_media_files_page({"match_state": "with_cover", "page_size": 0})
        self.assertEqual(page["total"], 1)

    def test_cover_filter_combines_with_search(self):
        self._seed_two()
        covers.set_manual_cover("WithCover", JPEG)
        page = self.api.get_media_files_page(
            {"match_state": "with_cover", "keyword": "NoCover", "page_size": 0})
        self.assertEqual(page["total"], 0)  # 有封面的作品里搜不到 NoCover


class CoverFilterFrontendTests(unittest.TestCase):
    def setUp(self):
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")

    def test_match_select_offers_cover_options(self):
        for token in ("option('with_cover', '有封面'", "option('no_cover', '没封面'"):
            self.assertIn(token, self.js)


class LibraryEditModeFrontendTests(unittest.TestCase):
    """媒体库「纯查看 / 编辑模式」：默认纯查看，切换只重绘不重查。

    用户要求：媒体库也要有和剧集墙一样的模式切换，默认纯查看，避免误点
    改名 / 删除文件；切模式不能重新查库、重拉封面（那会很慢）。
    """

    def setUp(self):
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")

    def test_state_defaults_to_view_only(self):
        block = self.js.split("const fileListState = {", 1)[1].split("};", 1)[0]
        self.assertIn("editMode: false", block)

    def test_toolbar_has_mode_buttons(self):
        block = self.js.split("function fileListCard(", 1)[1].split("function fileListRows(", 1)[0]
        self.assertIn('data-action="file-edit-mode"', block)
        self.assertIn('data-edit="0"', block)
        self.assertIn('data-edit="1"', block)

    def test_delete_file_button_guarded_by_edit_mode(self):
        block = self.js.split("function fileListRows(", 1)[1].split("function fileListPagerHtml", 1)[0]
        self.assertIn('data-action="delete-local-file"', block)
        # 模板里必须先判 editMode 才输出删除按钮
        idx = block.index('data-action="delete-local-file"')
        head = block[max(0, idx - 260):idx]
        self.assertIn("fileListState.editMode", head)

    def test_tree_rename_and_cleanup_guarded(self):
        self.assertIn("const cleanup = fileListState.editMode &&", self.js)
        self.assertIn("const rename = fileListState.editMode &&", self.js)

    def test_cards_view_passes_library_edit_mode(self):
        self.assertIn("posterCardHtml(c, (coverMap || {})[c.title], fileListState.editMode)", self.js)

    def test_toggle_repaints_cache_instead_of_reloading(self):
        block = self.js.split("function repaintFileList()", 1)[1].split(
            "function syncFileEditModeButtons()", 1)[0]
        self.assertNotIn("await call(", block)
        self.assertIn("box.innerHTML", block)
        # 缓存不可用时才允许退回重新载入
        handler = self.js.split("case 'file-edit-mode': {", 1)[1].split("case '", 1)[0]
        self.assertIn("repaintFileList()", handler)
        self.assertIn("loadFileList()", handler)
