"""追剧列表、后台进程管理页与封面关键词抓取的回归契约。

覆盖：清单按书写顺序入库 / 幂等 / 顺序与状态调整 / 本地文件匹配、
关键词变体与站点多轮搜索、导航 ↔ 视图一致性、剧集墙按钮迁移。
所有数据库用例都在临时库；所有网络调用都 mock，绝不真发请求。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock

from app import covers, db, textmatch, watchlist
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = """随便一行标题前的说明
已下载：
更衣人偶坠入爱河
石纪元
《分裂》主演：詹姆斯·麦卡沃伊
更衣人偶坠入爱河
未下载：
进击的巨人
租借女友 s1-6
进击的巨人
"""


class TextMatchTests(unittest.TestCase):

    def test_book_title_is_first_query(self):
        variants = textmatch.query_variants("电影名《分裂》主演：詹姆斯·麦卡沃伊")
        self.assertEqual(variants[0], "分裂")
        self.assertLess(len(variants[0]), len(variants[1]))

    def test_full_name_is_not_first_query(self):
        """用户明确要求「不要全名」：完整长标题只能当最后兜底。"""
        variants = textmatch.query_variants("物语系列 2nd Season（2013 年）：包含猫物语（白）")
        self.assertGreater(len(variants), 1)
        self.assertNotEqual(variants[0], "物语系列 2nd Season（2013 年）：包含猫物语（白）")

    def test_season_and_episode_suffix_stripped(self):
        self.assertEqual(textmatch.query_variants("租借女友 s1-6")[0], "租借女友")
        self.assertEqual(textmatch.query_variants("Re：Zero s4 part2")[0], "Re：Zero")
        self.assertEqual(textmatch.query_variants("间谍过家家 第二季")[0], "间谍过家家")

    def test_normal_title_is_not_truncated(self):
        self.assertEqual(textmatch.query_variants("更衣人偶坠入爱河"), ["更衣人偶坠入爱河"])

    def test_lead_markers_and_zero_width_removed(self):
        self.assertEqual(textmatch.query_variants("番名：实况主的逃脱游戏")[0], "实况主的逃脱游戏")
        self.assertEqual(textmatch.clean_title("\u200b- 完 "), "完")
        self.assertEqual(textmatch.clean_title("  2. 剧场版《亲爱好朋友》：说明"),
                         "剧场版《亲爱好朋友》：说明")

    def test_hash_tag_mix_prefers_chinese_segment(self):
        variants = textmatch.query_variants("Inside...#VinlandSAGA#冰海战记")
        self.assertEqual(variants[0], "冰海战记")
        self.assertIn("VinlandSAGA", variants)

    def test_numbered_titles_are_not_treated_as_list_markers(self):
        """「2. 剧场版…」是清单编号，但「2.5次元的诱惑」是作品名，不能被削成 5 次元。"""
        self.assertEqual(textmatch.query_variants("2.5次元的诱惑")[0], "2.5次元的诱惑")
        self.assertEqual(textmatch.query_variants("22/7那一天的她们")[0], "22/7那一天的她们")
        self.assertEqual(textmatch.clean_title("  2. 剧场版《亲爱好朋友》：说明"),
                         "剧场版《亲爱好朋友》：说明")

    def test_match_score_is_lenient_for_sequel_but_rejects_unrelated(self):
        self.assertGreaterEqual(textmatch.match_score("葬送的芙莉莲 第二季", "葬送的芙莉莲"), 0.85)
        self.assertLess(textmatch.match_score("完全不相干的作品", "不存在的番剧"), 0.55)

    def test_local_hit_requires_three_chars(self):
        self.assertFalse(textmatch.local_hit("somehaystack", ["ab"]))
        self.assertTrue(textmatch.local_hit("xx目标作品xx", ["目标作品"]))


class WatchlistParseTests(unittest.TestCase):

    def test_sections_split_and_keep_write_order(self):
        rows = watchlist.parse_sections(SAMPLE)
        self.assertEqual([r["title"] for r in rows], [
            "更衣人偶坠入爱河", "石纪元", "《分裂》主演：詹姆斯·麦卡沃伊",
            "进击的巨人", "租借女友 s1-6",
        ])
        self.assertEqual([r["downloaded"] for r in rows], [1, 1, 1, 0, 0])

    def test_duplicates_only_removed_within_same_section(self):
        rows = watchlist.parse_sections("已下载：\nA\n未下载：\nA\nA\n")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["downloaded"] for r in rows], [1, 0])

    def test_text_before_first_section_ignored(self):
        rows = watchlist.parse_sections("随便的说明\n已下载：\n作品A")
        self.assertEqual([r["title"] for r in rows], ["作品A"])

    def test_preview_counts_without_writing(self):
        info = watchlist.preview(SAMPLE)
        self.assertEqual(info["total"], 5)
        self.assertEqual(info["downloaded"], 3)
        self.assertEqual(info["pending"], 2)
        self.assertEqual(info["sample"][0], "更衣人偶坠入爱河")

    def test_empty_text_reports_error(self):
        self.assertFalse(watchlist.preview("没有任何分段")["total"])


class WatchlistDbTests(IsolatedDbTestCase):

    def import_sample(self):
        return watchlist.import_text(SAMPLE)

    def test_import_keeps_order_and_is_idempotent(self):
        first = self.import_sample()
        self.assertTrue(first["ok"])
        self.assertEqual((first["added"], first["downloaded"], first["pending"]), (5, 3, 2))
        again = self.import_sample()
        self.assertEqual(again["added"], 0)
        self.assertEqual(again["skipped"], 5)
        rows = db_rows()
        self.assertEqual([r["sort_order"] for r in rows], [1, 2, 3, 4, 5])
        self.assertEqual(rows[0]["title"], "更衣人偶坠入爱河")
        self.assertEqual(rows[-1]["title"], "租借女友 s1-6")

    def test_replace_rebuilds_in_new_order(self):
        self.import_sample()
        r = watchlist.import_text("未下载：\n新的第一部\n新的第二部", replace=True)
        self.assertEqual((r["added"], r["total"], r["pending"]), (2, 2, 2))
        self.assertEqual([x["title"] for x in db_rows()], ["新的第一部", "新的第二部"])

    def test_import_does_not_touch_playback_records(self):
        f = self.media("Show - 01.mkv", title_cn="展示作品")
        db.execute("INSERT INTO watch_progress(file_id, position_ms, completed) VALUES (?, 5000, 1)", (f["id"],))
        db.execute("INSERT INTO watch_sessions(file_id, started_at) VALUES (?, '2026-01-01')", (f["id"],))
        self.import_sample()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 1)

    def test_toggle_only_changes_flag_not_order_or_count(self):
        self.import_sample()
        row = db_rows()[3]           # 未下载第一条
        result = watchlist.set_downloaded(row["id"], True)
        self.assertEqual(result["downloaded"], 1)
        after = db_rows()
        self.assertEqual(len(after), 5)
        self.assertEqual([r["sort_order"] for r in after], [1, 2, 3, 4, 5])
        self.assertEqual(after[3]["downloaded"], 1)

    def test_move_swaps_with_neighbor_in_same_section(self):
        self.import_sample()
        rows = db_rows()
        moved = watchlist.move(rows[1]["id"], -1)
        self.assertTrue(moved["moved"])
        self.assertEqual([r["title"] for r in db_rows()][:2], ["石纪元", "更衣人偶坠入爱河"])
        self.assertTrue(watchlist.move(db_rows()[0]["id"], -1)["moved"] is False)

    def test_move_pending_item_does_not_swap_with_downloaded(self):
        self.import_sample()
        rows = db_rows()
        result = watchlist.move(rows[3]["id"], -1)   # 未下载首条上移，应到头
        self.assertFalse(result["moved"])

    def test_add_rename_remove(self):
        self.import_sample()
        self.assertTrue(watchlist.add("新追的番")["ok"])
        row = [r for r in db_rows() if r["title"] == "新追的番"][0]
        self.assertEqual(row["downloaded"], 0)
        self.assertEqual(row["sort_order"], 6)
        self.assertTrue(watchlist.rename(row["id"], "改过的名字")["ok"])
        self.assertEqual([r["title"] for r in db_rows()][-1], "改过的名字")
        self.assertTrue(watchlist.remove(row["id"])["ok"])
        self.assertEqual(len(db_rows()), 5)
        self.assertFalse(watchlist.remove(999999)["ok"])

    def test_items_match_local_files_and_expose_search_keyword(self):
        self.media("更衣人偶坠入爱河 - 01.mkv", title_cn="更衣人偶坠入爱河")
        self.media("更衣人偶坠入爱河 - 02.mkv", title_cn="更衣人偶坠入爱河")
        self.import_sample()
        data = watchlist.items()
        self.assertEqual(data["total"], 5)
        first = data["items"][0]
        self.assertEqual(first["match_count"], 2)
        self.assertEqual(first["search_keyword"], "更衣人偶坠入爱河")
        self.assertEqual(len(first["files"]), 2)
        self.assertIn("id", first["files"][0])
        self.assertEqual(data["downloaded"], 3)

    def test_items_keyword_filters_locally(self):
        self.import_sample()
        self.assertEqual(len(watchlist.items("巨人")["items"]), 1)

    def test_deleted_local_files_are_not_counted_as_matches(self):
        self.media("石纪元 - 01.mkv", title_cn="石纪元", deleted_at="2026-11-15 10:00:00")
        self.import_sample()
        data = watchlist.items()
        stone = [x for x in data["items"] if x["title"] == "石纪元"][0]
        self.assertEqual(stone["match_count"], 0)

    def test_short_english_word_is_not_a_wildcard_match(self):
        for i in range(6):
            self.media(f"Some Other Series - {i:02d}.mkv", title_guess="ZeroTV Release")
        self.import_sample()
        data = watchlist.items()
        zero = [x for x in data["items"] if x["title"] == "《分裂》主演：詹姆斯·麦卡沃伊"]
        self.assertEqual(zero[0]["match_count"], 0)

    def test_api_bridges_exist(self):
        api = Api()
        for name in ("watchlist_preview", "watchlist_import", "get_watchlist",
                     "watchlist_set_downloaded", "watchlist_move", "watchlist_add",
                     "watchlist_rename", "watchlist_remove", "process_status"):
            self.assertTrue(callable(getattr(api, name)), name)
        self.assertTrue(api.watchlist_import(SAMPLE)["ok"])
        self.assertEqual(api.get_watchlist()["total"], 5)

    def test_process_status_is_read_only_and_never_starts_daemon(self):
        api = Api()
        with mock.patch.object(api, "daemon_running", return_value={"running": False}), \
             mock.patch("app.api.subprocess.Popen", side_effect=AssertionError("不应启动进程")):
            st = api.process_status()
        self.assertTrue(st["ok"])
        self.assertFalse(st["daemon"]["running"])
        self.assertIn("covers", st)
        self.assertIn("cover_dir", st)

    def test_poster_card_search_keyword_uses_clean_work_title(self):
        """分组名是「1.36G」这种脏串时，跳转搜索词要退回组内中文名。"""
        api = Api()
        self.assertEqual(api._card_search_keyword({"label": "进击的巨人"}), "进击的巨人")
        self.assertEqual(api._card_search_keyword({
            "label": "1.36G",
            "files": [{"title_cn": "紫罗兰永恒花园", "title_guess": "Violet Evergarden"}],
        }), "紫罗兰永恒花园")
        self.assertEqual(api._card_search_keyword({"label": "间谍过家家 第二季"}), "间谍过家家")

    def test_dirty_folder_label_falls_back_to_bracketed_chinese_title(self):
        """真实库案例：发布目录 [1.36G] 被当成作品名，文件名里其实有 [爱的躯壳]。"""
        api = Api()
        group = {
            "label": "1.36G",
            "children": [{"files": [{
                "filename": "[爱的躯壳].La.Belle.2000.XviD.AC3.CD1-WAF.avi",
                "title_raw": "[爱的躯壳].La.Belle.2000.XviD.AC3.CD1-WAF",
                "title_guess": "La Belle XviD AC3 CD1-WAF",
                "title_cn": None,
            }]}],
        }
        self.assertEqual(api._group_bracket_titles(group)[0], "爱的躯壳")
        self.assertEqual(api._card_search_keyword(group), "爱的躯壳")

    def test_bracket_noise_words_are_not_used_as_search_keyword(self):
        api = Api()
        group = {"label": "1.36G", "files": [
            {"filename": "[韩语中字幕WAF].avi", "title_raw": "[韩语中字幕WAF]"},
            {"filename": "[紫罗兰永恒花园][01].mkv", "title_raw": "[紫罗兰永恒花园][01]"},
        ]}
        self.assertEqual(api._card_search_keyword(group), "紫罗兰永恒花园")

    def test_poster_wall_exposes_search_keyword(self):
        self.media("进击的巨人 - 01.mkv", title_cn="进击的巨人")
        wall = Api().get_poster_wall({})
        self.assertTrue(wall["cards"])
        self.assertTrue(all("search_keyword" in c for c in wall["cards"]))

    def test_poster_wall_shows_clean_title_for_dirty_group(self):
        """脏分组卡片展示与跳转搜索词都要用推断出的中文片名。"""
        self.media("placeholder.avi")
        group = {
            "label": "1.36G",
            "children": [{"files": [{
                "filename": "[爱的躯壳].La.Belle.2000.XviD.AC3.CD1-WAF.avi",
                "title_raw": "[爱的躯壳].La.Belle.2000.XviD.AC3.CD1-WAF",
                "title_guess": "La Belle XviD AC3 CD1-WAF",
            }]}],
        }
        with mock.patch("app.api.library_tree.build_tree",
                        return_value={"groups": [group]}):
            wall = Api().get_poster_wall({})
        card = wall["cards"][0]
        self.assertEqual(card["title"], "爱的躯壳")
        self.assertEqual(card["search_keyword"], "爱的躯壳")


def db_rows():
    from app import db
    return db.rows("SELECT id, title, downloaded, sort_order FROM watchlist ORDER BY sort_order, id")


class CoverKeywordTests(unittest.TestCase):

    def test_search_uses_short_keyword_not_full_name(self):
        """站点搜索的 query 不能是带说明的完整长标题。"""
        seen = []

        class Resp:
            status_code = 200
            text = ""

        def fake_get(url, **kwargs):
            seen.append(url)
            return Resp()

        with mock.patch.object(covers, "_get", side_effect=fake_get):
            result = covers.search_cover("物语系列 2nd Season（2013 年）：包含猫物语（白）")
        self.assertFalse(result["ok"])
        self.assertTrue(seen)
        from urllib.parse import unquote
        queries = [unquote(u.split("query=", 1)[1]) for u in seen if "query=" in u]
        self.assertTrue(queries)
        self.assertNotIn("物语系列 2nd Season（2013 年）：包含猫物语（白）", queries[0])
        self.assertLessEqual(len([u for u in seen if "query=" in u]), covers.MAX_AGEDM_QUERIES)

    def test_both_sources_are_tried_when_agedm_misses(self):
        visited = []
        covers.clear_seed_cache()

        class Resp:
            status_code = 200
            text = "<html></html>"

        def fake_get(url, **kwargs):
            visited.append(url)
            return Resp()

        with mock.patch.object(covers, "_get", side_effect=fake_get):
            covers.search_cover("完全没有的作品XYZ")
        self.assertTrue(any("agedm.io" in u for u in visited))
        self.assertTrue(any("seeduck.cc" in u for u in visited))
        covers.clear_seed_cache()

    def test_seed_pages_are_fetched_only_once_per_batch(self):
        """300 部作品 × 5 页会把代理打挂：备源结果必须整批共享。"""
        covers.clear_seed_cache()
        calls = []

        class Resp:
            status_code = 200
            text = ("<a title=\"别的东西\" class=\"image\">"
                    "<img src=\"https://img.example/x.webp\"></a>")

        def fake_get(url, **kwargs):
            calls.append(url)
            return Resp()

        with mock.patch.object(covers, "_get", side_effect=fake_get):
            first = covers.seed_pairs()
            second = covers.seed_pairs()
        self.assertEqual(len(first), len(second))
        self.assertEqual(len([u for u in calls if "seeduck.cc" in u]), len(covers.SEEDUCK_PAGES))
        covers.clear_seed_cache()

    def test_empty_seed_result_is_not_cached(self):
        """403 / Cloudflare 空页不能缓存，否则下一轮也不会重试。"""
        covers.clear_seed_cache()

        class Resp:
            status_code = 403
            text = "blocked"

        with mock.patch.object(covers, "_get", return_value=Resp()):
            covers.seed_pairs()
        self.assertEqual(covers._SEED_CACHE, {})

    def test_network_failure_retries_then_raises(self):
        calls = []

        def boom(*args, **kwargs):
            calls.append(1)
            raise OSError("proxy dropped")

        with mock.patch.object(covers, "_session") as sess:
            sess.return_value.get.side_effect = boom
            with self.assertRaises(OSError):
                covers._get("https://example.test/x", retries=2)
        self.assertEqual(len(calls), 3)

    def test_second_keyword_hit_is_accepted(self):
        """第一轮全名搜不到、第二轮短关键词命中时也能返回封面。"""
        calls = []

        class Resp:
            def __init__(self, text):
                self.status_code = 200
                self.text = text

        hit_html = ('<div class="card cata_video_item"><a href="x" title="冰海战记">'
                    '<img class="video_thumbs" data-original="https://img.example/v.jpg"></a></div>')

        def fake_get(url, **kwargs):
            calls.append(url)
            return Resp(hit_html if len(calls) >= 2 else "<html></html>")

        with mock.patch.object(covers, "_get", side_effect=fake_get):
            r = covers.search_cover("Inside...#VinlandSAGA#冰海战记")
        self.assertTrue(r["ok"])
        self.assertEqual(r["hit"], "冰海战记")
        self.assertEqual(r["urls"], ["https://img.example/v.jpg"])

    def test_rank_pick_rejects_unrelated_result(self):
        html = ('<div class="card cata_video_item"><a href="x" title="完全不相干的作品">'
                '<img class="video_thumbs" src="https://img.example/n.jpg"></a></div>')
        self.assertIsNone(covers.rank_pick(html, ["不存在的番剧"]))

    def test_generic_anchor_fallback_when_site_markup_changes(self):
        html = ('<a href="/x" title="间谍过家家" class="d-block">'
                '<img data-original="https://img.example/a.jpg"></a>')
        pairs = covers._pairs(html)
        self.assertEqual(pairs[0][0], "间谍过家家")
        self.assertEqual(pairs[0][1], ["https://img.example/a.jpg"])

    def test_job_status_exposes_query_and_failures(self):
        self.assertIn("last_query", covers._JOB)
        self.assertIn("recent_failures", covers._JOB)
        snap = covers.status()
        self.assertIsInstance(snap["recent_failures"], list)


class FrontendContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")

    def test_two_new_nav_items_and_colors(self):
        self.assertIn('data-view="watchlist"', self.html)
        self.assertIn('data-view="processes"', self.html)
        self.assertIn('>追剧列表</button>', self.html)
        self.assertIn('>后台进程</button>', self.html)
        self.assertIn(".ni-track", self.css)
        self.assertIn('html[data-theme="dark"] .ni-process', self.css)

    def test_title_menu_holds_utility_entries(self):
        """导入 / 扫描 / 设置收进标题旁下拉，侧栏只留浏览页面。"""
        head = self.html.split('<nav class="nav" id="mainNav">', 1)[0]
        self.assertNotIn('data-view="import"', head)
        self.assertNotIn('data-view="scan"', head)
        sidebar = self.html.split('<aside class="sidebar">', 1)[1].split('</aside>', 1)[0]
        self.assertNotIn('data-view="settings"', sidebar)
        menu = self.html.split('<div class="title-menu"', 1)[1].split('<p id="viewSubtitle"', 1)[0]
        for view in ('import', 'scan', 'settings'):
            self.assertIn(f'data-view="{view}"', menu)
        # 按钮文本必须紧贴 </button>，跟导航/脚本依赖的既有形状一致
        for label in ('导入列表', '扫描目录', '设置'):
            self.assertIn(f'>{label}</button>', menu)
        self.assertIn('data-action="toggle-title-menu"', self.html)
        main = self.html.split('<nav class="nav" id="mainNav">', 1)[1].split('</nav>', 1)[0]
        for view in ('import', 'scan', 'settings'):
            self.assertNotIn(f'data-view="{view}"', main)
        self.assertIn(".title-menu { position: absolute;", self.css)
        self.assertIn(".title-menu[hidden] { display: none; }", self.css)
        self.assertIn(".title-menu .menu-item {", self.css)
        for token in ('toggle-title-menu', 'closeTitleMenu()',
                      "ev.target.closest('#titleMenu')", "aria-expanded"):
            self.assertIn(token, self.js)
        # 侧栏项变少后主导航仍要能自己滚，否则矮窗口会顶到底栏
        self.assertIn("#mainNav { flex: 1 1 auto; min-height: 0; overflow-y: auto;", self.css)

    def test_nav_meta_renderers_stay_in_sync(self):
        nav = set(re.findall(r'class="nav-item[^"]*"\s+data-view="([^"]+)"', self.html))
        meta = set(re.findall(r"^\s*([a-z]+):\s*\[",
                              self.js.split("const VIEW_META = {", 1)[1].split("};", 1)[0], re.M))
        rend = set(re.findall(r"([a-z]+):\s*render\w+",
                              self.js.split("const RENDERERS = {", 1)[1].split("};", 1)[0]))
        self.assertEqual(nav, meta)
        self.assertEqual(nav, rend)
        self.assertEqual(len(nav), 14)

    def test_cover_controls_moved_out_of_poster_wall(self):
        block = self.js.split("async function renderPosters()", 1)[1].split(
            "// 全盘快搜是左侧独立入口", 1)[0]
        self.assertNotIn('data-action="cover-fetch"', block)
        self.assertNotIn('data-action="cover-cancel"', block)
        self.assertNotIn('id="coverProgress"', block)
        # 后台进程入口只在左侧导航，剧集墙不再放跳转/刷新按钮（点了没实际作用）。
        self.assertNotIn('data-view="processes"', block)
        self.assertNotIn('data-action="poster-refresh"', block)
        self.assertNotIn("case 'poster-refresh'", self.js)

    def test_poster_mode_button_exists_and_uses_btn(self):
        """「按季」按钮修复后必须保留：后端 season 分组能力完整，只是曾被 el 笔误弄假死。"""
        block = self.js.split("async function renderPosters()", 1)[1].split(
            "// 全盘快搜是左侧独立入口", 1)[0]
        self.assertIn('data-action="poster-mode" data-mode="season"', block)
        self.assertIn("case 'poster-mode'", self.js)

    def test_handle_action_cases_use_btn_not_undefined_el(self):
        """handleAction 形参是 btn；这 6 个 case 曾误用未声明的 el，严格模式下抛错致按钮假死。"""
        body = self.js.split("async function handleAction(action, btn) {", 1)[1]
        for case in ("save-episode", "export-csv", "stats-range",
                     "calendar-shift", "poster-mode", "rate-work"):
            block = body.split(f"case '{case}':", 1)[1].split("case '", 1)[0]
            self.assertNotIn("el.dataset", block, f"{case} 仍在用未声明的 el")
            self.assertIn("btn.dataset", block, f"{case} 没有从 btn 取 dataset")

    def test_process_page_has_start_stop_and_progress(self):
        for token in ("async function renderProcesses()", "call('process_status')",
                      "id=\"coverProgress\"", "id=\"coverStatusText\"",
                      "proc-daemon-start", "proc-daemon-stop", "proc-daemon-auto",
                      "cover-fetch", "cover-force", "cover-cancel",
                      "recent_failures", "procPollStart"):
            self.assertIn(token, self.js)

    def test_no_permanent_poll_after_leaving_process_page(self):
        self.assertIn("if (currentView !== 'processes')", self.js)
        self.assertIn("clearInterval(procPollTimer); procPollTimer = null;", self.js)

    def test_poster_card_has_no_play_next_button(self):
        block = self.js.split("function posterCardHtml(", 1)[1].split("function renderPosterCards", 1)[0]
        self.assertNotIn('>播放下一集</button>', block)
        self.assertNotIn('data-action="play"', block)
        self.assertNotIn("next_file_id", block)
        self.assertIn('data-keyword="${esc(card.search_keyword || card.title)}"', block)

    def test_goto_carries_keyword_into_library_search(self):
        self.assertIn("switchView(btn.dataset.view, { keyword: btn.dataset.keyword });", self.js)
        self.assertIn("fileListState.keyword = opts.keyword.trim();", self.js)
        self.assertIn("fileListState.view_mode = 'series';", self.js)

    def test_watchlist_ui_contracts(self):
        for token in ("async function renderWatchlist()", "call('get_watchlist'",
                      "call('watchlist_import'", "watchlist-up", "watchlist-down",
                      "watchlist-toggle", "watchlist-files", "watchlist-remove",
                      "data-keyword=\"${esc(r.search_keyword || r.title)}\"",
                      "id=\"wlDownloaded\"", "id=\"wlPending\""):
            self.assertIn(token, self.js)


class LooseTitleHitTests(unittest.TestCase):
    """``textmatch.loose_title_hit`` 的口径：只救「词序不同但用字相同」的同一译名。

    起因：清单写「会长大人是女仆」，磁盘目录是「[SAIO-Raws] 会长是女仆大人
    Kaichou wa Maid-sama!」，两者子串互不包含；而这类文件的 ``title_cn`` 是空的
    （中文名只存在于目录名里），纯包含判断会整条漏掉，追剧列表点不到本地文件。
    """

    SHOW = "[SAIO-Raws] 会长是女仆大人Kaichou wa Maid-sama! [BD 1920x1080 HEVC-10bit OPUS]"

    def test_reordered_translation_hits(self):
        self.assertTrue(textmatch.loose_title_hit("会长大人是女仆", textmatch.normalize(self.SHOW)))

    def test_short_english_word_never_hits(self):
        """「nana」字符集只有 {n,a}，曾经把 267 个无关文件算成命中。"""
        hay = textmatch.normalize("Melody Marks Loves When People Watch Her")
        self.assertFalse(textmatch.loose_title_hit("nana", hay))

    def test_two_char_cjk_is_rejected(self):
        """少于 4 个汉字不做词序容错，否则「安娜」会撞上一切含这两个字的目录。"""
        self.assertFalse(textmatch.loose_title_hit("安娜", textmatch.normalize("安娜2022 全6集 简繁英字幕")))

    def test_first_bigram_is_required(self):
        """首尾二字组必须都在：只凑齐中间的字说明是撞词，不是同一部剧。"""
        hay = textmatch.normalize("我的女友的妹妹的朋友")
        self.assertFalse(textmatch.loose_title_hit("女朋友的朋友", hay))

    def test_mixed_latin_title_is_rejected(self):
        self.assertFalse(textmatch.loose_title_hit("魔法少女奈叶vivid", textmatch.normalize("魔法少女奈叶vividstrike")))


class LooseTitleMatchTests(IsolatedDbTestCase):
    """追剧列表的词序兜底：真命中的要出现「本地 N 个文件」，误配的一条都不能多。"""

    DIR = r"D:\magic\[SAIO-Raws] 会长是女仆大人Kaichou wa Maid-sama! [BD 1920x1080 HEVC-10bit OPUS]"

    def setUp(self):
        super().setUp()
        for ep in (1, 2, 3):
            self.media(
                f"[SAIO-Raws] Kaichou wa Maid-sama! {ep:02d}.mkv",
                title_guess=f"Kaichou wa Maid-sama! {ep:02d}",
                path=f"{self.DIR}\\[SAIO-Raws] Kaichou wa Maid-sama! {ep:02d}.mkv",
                episode_no=ep,
            )

    def test_reordered_title_matches_local_files(self):
        count, files, keyword = watchlist.match_files("会长大人是女仆", watchlist.media_index())
        self.assertEqual(count, 3)
        # 搜索词换成目录里的写法，「媒体库搜索」才搜得到（清单写法的结果是 0 条）
        self.assertEqual(keyword, "会长是女仆大人")
        self.assertTrue(all(f["path"].startswith(self.DIR) for f in files))

    def test_strict_match_still_wins_first(self):
        """能用原关键词命中时不能走兜底，避免把 search_keyword 换掉。"""
        count, _files, keyword = watchlist.match_files("Kaichou wa Maid-sama!", watchlist.media_index())
        self.assertEqual(count, 3)
        self.assertEqual(keyword, "Kaichou wa Maid-sama")   # clean_title 会剥掉尾部感叹号

    def test_short_title_does_not_fall_back(self):
        count, _files, _kw = watchlist.match_files("安娜", watchlist.media_index())
        self.assertEqual(count, 0)

    def test_partial_overlap_with_other_show_does_not_match(self):
        """真实误配场景：「女朋友的朋友」与「超超超…100个女朋友」共享 4 个字。"""
        self.media("超超超超超喜欢你的100个女朋友 - 01.mkv",
                   title_guess="超超超超超喜欢你的100个女朋友", episode_no=1)
        count, _files, _kw = watchlist.match_files("女朋友的朋友", watchlist.media_index())
        self.assertEqual(count, 0)

    def test_items_exposes_match_for_reordered_title(self):
        watchlist.import_text("已下载：\n会长大人是女仆\n", replace=True)
        data = watchlist.items()
        row = [x for x in data["items"] if x["title"] == "会长大人是女仆"][0]
        self.assertEqual(row["match_count"], 3)
        self.assertEqual(row["search_keyword"], "会长是女仆大人")
        self.assertTrue(row["files"])


if __name__ == "__main__":
    unittest.main()
