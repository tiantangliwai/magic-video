"""人工设定封面 + 真实站点搜索页解析：用户 2026-11-15 需求回归。

覆盖两件事：
1. 用官方搜索 URL 抓到「对」的图：AGE 结果块不丢最后一条、排除共用占位
   ``src``、解出百度代理里的原始 CDN；Seeduck 搜索页用 ``<h2>`` 短标题配对；
   无关标题不再被低相似度凑数命中。
2. 人工粘贴/选择/填网址设定封面：三种来源都校验魔数与大小，
   ``source='manual'`` 优先展示且后台 force 重抓也绝不覆盖用户选择。
"""
from __future__ import annotations

import base64
import unittest
from pathlib import Path
from unittest import mock

from app import covers
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent

# 真实 AGE 搜索页形状：4 个结果块，src 全部相同（占位图），
# data-original 是百度图片代理，&src= 参数里是原始 CDN。
PLACEHOLDER = "https://p1.bdxiguaimg.com/origin/137860000e3053288f11f"
BAIDU_PROXY = ("https://gimg3.baidu.com/gimg/app=2001&amp;n=0&amp;g=0n&amp;fmt=jpeg"
               "&amp;src=https%3A%2F%2Fcdn.aqdstatic.com%3A966%2Fage%2Fcovers%2F20080003.jpg")
RAW_CDN = "https://cdn.aqdstatic.com:966/age/covers/20080003.jpg"


def agedm_block(title: str, detail: str) -> str:
    proxy = BAIDU_PROXY.replace("20080003.jpg", f"{detail}.jpg")
    return (f'<div class="card cata_video_item py-4">'
            f'<a href="http://www.agedm.io/detail/{detail}" class="d-block" title="{title}">'
            f'<img class="video_thumbs lazyload" src="{PLACEHOLDER}" '
            f'data-original="{proxy}" alt="{title}"></a></div>')


AGEDM_REAL = "<main>" + "".join([
    agedm_block("零之使魔 第三季", "20080003"),
    agedm_block("零之使魔 第四季", "20120012"),
    agedm_block("零之使魔 第二季", "20070001"),
    agedm_block("零之使魔", "20060001"),   # 精确匹配恰好是最后一块
]) + '<div class="pagination">1</div>'   # 没有 </main>，旧正则会丢掉最后一块

# 真实 Seeduck 搜索页形状：<a class="image"> 里 img 是真封面，紧随的 <h2> 是干净短标题
SEEDUCK_SEARCH_HTML = """
<a target="_blank" title="零之使魔2 ～双月的骑士～ ゼロの使い魔 ～双月の騎士～" class="image"
   href="/movies/21343/"><img src="https://sh1.example/p2.jpg" alt="长标题"></a>
<ul><li><h2><a href="#" aria-hidden="true" class="header-anchor">#</a> 零之使魔2 ～双月的骑士～</h2></li></ul>
<a target="_blank" title="零之使魔 ゼロの使い魔" class="image" href="/movies/21340/">
   <img src="https://sh1.example/p1.jpg" alt="长标题"></a>
<ul><li><h2><a href="#" aria-hidden="true" class="header-anchor">#</a> 零之使魔</h2></li></ul>
"""

JPEG = b"\xff\xd8\xff" + b"x" * 1024


class RealSiteParsingTests(unittest.TestCase):
    """真实站点结构：修复「很多封面都是错的」的根因。"""

    def test_block_split_keeps_last_result_without_closing_main(self):
        blocks = covers._blocks(AGEDM_REAL)
        self.assertEqual(len(blocks), 4)
        self.assertIn('title="零之使魔"', blocks[-1])

    def test_placeholder_src_is_never_a_candidate(self):
        pairs = covers._pairs(AGEDM_REAL)
        self.assertEqual(len(pairs), 4)
        for _name, urls in pairs:
            self.assertNotIn(PLACEHOLDER, urls)
            self.assertTrue(any(covers._RAW_CDN_HOSTS[0] in u for u in urls))

    def test_baidu_proxy_resolves_original_cdn_first(self):
        pairs = covers._pairs(AGEDM_REAL)
        self.assertEqual(pairs[0][1][0], RAW_CDN)
        self.assertTrue(pairs[0][1][1].startswith("https://gimg3.baidu.com/"))

    def test_exact_match_wins_even_when_it_is_last_block(self):
        hit = covers.exact_pick(covers._pairs(AGEDM_REAL), "零之使魔")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[1], "零之使魔")
        self.assertIn(RAW_CDN.replace("20080003", "20060001"), hit[0])

    def test_strict_pick_rejects_unrelated_titles(self):
        self.assertIsNone(covers.strict_pick(
            [("完全不相干的作品", ["https://img.example/x.jpg"])], "零之使魔"))
        self.assertIsNone(covers.strict_pick(
            [("进击的巨人 最终季", ["https://img.example/x.jpg"])], "葬送的芙莉莲"))

    def test_seeduck_search_page_uses_h2_short_title(self):
        pairs = covers._seed_pairs(SEEDUCK_SEARCH_HTML)
        self.assertEqual(len(pairs), 2)
        self.assertIn(("零之使魔", ["https://sh1.example/p1.jpg"]), pairs)
        # 长日文 title 不参与主匹配
        self.assertFalse(any("ゼロ" in name for name, _ in pairs))

    def test_parse_seeduck_search_page_finds_exact(self):
        hit = covers.parse_seeduck(SEEDUCK_SEARCH_HTML, "零之使魔")
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], ["https://sh1.example/p1.jpg"])

    def test_search_uses_both_search_urls_and_exact_match(self):
        """主源用 query=，备源用 /s/<kw>/；都按关键词搜，不再是固定首页池。"""
        visited = []

        class Resp:
            status_code = 200

            def __init__(self, text):
                self.text = text

        def fake_get(url, **kwargs):
            visited.append(url)
            return Resp(AGEDM_REAL if "agedm.io" in url else "<html></html>")

        covers.clear_seed_cache()
        with mock.patch.object(covers, "_get", side_effect=fake_get):
            r = covers.search_cover("零之使魔")
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "agedm")
        self.assertEqual(r["hit"], "零之使魔")
        self.assertTrue(any("agedm.io/search?query=" in u for u in visited))
        covers.clear_seed_cache()

    def test_seeduck_search_url_is_tried_when_agedm_misses(self):
        visited = []

        class Resp:
            status_code = 200

            def __init__(self, text):
                self.text = text

        def fake_get(url, **kwargs):
            visited.append(url)
            if "seeduck.cc/s/" in url:
                return Resp(SEEDUCK_SEARCH_HTML)
            return Resp("<html></html>")

        covers.clear_seed_cache()
        with mock.patch.object(covers, "_get", side_effect=fake_get):
            r = covers.search_cover("零之使魔")
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["source"], "seeduck")
        self.assertEqual(r["hit"], "零之使魔")
        self.assertTrue(any("seeduck.cc/s/" in u for u in visited))
        covers.clear_seed_cache()


class ManualCoverTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()
        covers.config.COVER_DIR.mkdir(parents=True, exist_ok=True)

    def test_set_manual_cover_writes_file_and_subject(self):
        r = covers.set_manual_cover("间谍过家家 第二季", JPEG)
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "manual")
        self.assertTrue((covers.config.COVER_DIR / r["file"]).is_file())
        # 键带季：给第二季设图不会替第一季作主（用户 2026-11-18 反馈需求）
        self.assertTrue(covers.is_manual("间谍过家家 第二季"))
        self.assertFalse(covers.is_manual("间谍过家家"))
        self.assertTrue(covers.resolve_cover("间谍过家家 第二季"))
        # 作品级卡片会「回落显示」这张（有图总比白板好），但那只是显示，
        # 不是写入：第一季单独设图时仍是自己的文件。
        self.assertEqual(covers.resolve_cover("间谍过家家"),
                         covers.resolve_cover("间谍过家家 第二季"))

    def test_validate_image_rejects_bad_payloads(self):
        for bad in (b"", b"x" * 100, b"<html>not an image</html>" + b"x" * 1024,
                    b"\xff\xd8\xff" + b"x" * (covers.MAX_BYTES + 1)):
            ok, _why = covers.validate_image_bytes(bad)
            self.assertFalse(ok)
        self.assertTrue(covers.validate_image_bytes(JPEG)[0])
        self.assertTrue(covers.validate_image_bytes(b"RIFF" + b"\x00" * 4 + b"WEBP" + b"x" * 1024)[0])

    def test_fetch_one_never_overwrites_manual_even_with_force(self):
        covers.set_manual_cover("间谍过家家", JPEG)
        with mock.patch.object(covers, "search_cover", side_effect=AssertionError("不应联网")):
            r = covers.fetch_one("间谍过家家", force=True)
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "manual")

    def test_cover_titles_excludes_manual_works(self):
        from app import db
        db.execute("INSERT INTO watched_list(title) VALUES ('人工设定的作品')")
        covers.set_manual_cover("人工设定的作品", JPEG)
        self.assertNotIn("人工设定的作品", covers.cover_titles())

    def test_manual_marker_handles_spaces_and_mixed_case(self):
        """标题含空格/英文时，lower(title_cn) 与归一化键不一致也不能让保护失效。"""
        title = "10天的爱人 10-Day Lover2023"
        covers.set_manual_cover(title, JPEG)
        self.assertTrue(covers.is_manual(title))
        self.assertTrue(covers.is_manual(title.replace(" ", "")))
        with mock.patch.object(covers, "search_cover", side_effect=AssertionError("不应联网")):
            self.assertEqual(covers.fetch_one(title, force=True)["source"], "manual")
        self.assertTrue(covers.clear_manual_cover(title)["ok"])
        self.assertFalse(covers.is_manual(title))

    def test_clear_manual_cover_restores_automatic_fetch(self):
        covers.set_manual_cover("间谍过家家", JPEG)
        r = covers.clear_manual_cover("间谍过家家")
        self.assertTrue(r["ok"])
        self.assertFalse(covers.is_manual("间谍过家家"))

    def test_api_set_cover_accepts_data_uri(self):
        uri = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
        r = self.api.set_cover("葬送的芙莉莲", uri, "datauri")
        self.assertTrue(r["ok"], r)
        self.assertTrue(r["is_manual"])
        out = self.api.cover_data_uris(["葬送的芙莉莲"])
        self.assertTrue(out["葬送的芙莉莲"].startswith("data:image/jpeg;base64,"))

    def test_api_set_cover_accepts_local_path(self):
        p = covers.config.COVER_DIR / "chosen.jpg"
        p.write_bytes(JPEG)
        r = self.api.set_cover("作品A", str(p), "path")
        self.assertTrue(r["ok"], r)
        self.assertTrue(covers.is_manual("作品A"))

    def test_api_set_cover_rejects_bad_inputs(self):
        for kind, payload in (("datauri", "not-a-data-uri"),
                              ("datauri", "data:image/png;base64,!!!!"),
                              ("path", "relative/path.jpg"),
                              ("path", r"C:\Windows\system32\notimage.txt"),
                              ("url", "javascript:alert(1)"),
                              ("whatever", JPEG.decode("latin-1"))):
            r = self.api.set_cover("作品B", payload, kind)
            self.assertFalse(r["ok"], (kind, payload[:30]))

    def test_api_manual_marker_and_clear(self):
        uri = "data:image/jpeg;base64," + base64.b64encode(JPEG).decode("ascii")
        self.api.set_cover("作品C", uri, "datauri")
        self.assertTrue(self.api.cover_is_manual(["作品C", "其它"])["作品C"])
        self.assertFalse(self.api.cover_is_manual(["作品C", "其它"])["其它"])
        self.assertTrue(self.api.clear_cover("作品C")["ok"])
        self.assertFalse(covers.is_manual("作品C"))


class SeasonCoverTests(IsolatedDbTestCase):
    """同一部番的不同季各存一张封面：用户 2026-11-18 反馈「两季强制一致」。"""

    PNG_HEAD = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89")

    def setUp(self):
        super().setUp()
        self.api = Api()
        covers.config.COVER_DIR.mkdir(parents=True, exist_ok=True)

    def png(self, tag: bytes) -> bytes:
        return self.PNG_HEAD + tag * 700

    def test_season_key_keeps_season_and_strips_episode(self):
        base = "哥布林杀手"
        self.assertEqual(covers.season_key(base), base)          # 无季：与旧键一致
        self.assertEqual(covers.season_key("哥布林杀手 S2"), f"{base}#s2")
        self.assertEqual(covers.season_key("哥布林杀手 第二季"), f"{base}#s2")
        self.assertEqual(covers.season_key("哥布林杀手 · 第 2 季"), f"{base}#s2")
        self.assertEqual(covers.season_key("哥布林杀手 S2 · 第 2 季"), f"{base}#s2")
        self.assertEqual(covers.season_key("哥布林杀手 第3季"), f"{base}#s3")
        # 集号仍然要去掉：同一季各集共用一个键
        self.assertEqual(covers.season_key("哥布林杀手 S2 - 01"), f"{base}#s2")
        # 不带季标记的标题不能被误伤
        for title in ("魔王2099", "10天的爱人 10-Day Lover2023", "Goblin Slayer"):
            self.assertEqual(covers.season_key(title), covers.cover_key(title))

    def test_two_seasons_store_different_files(self):
        s1 = covers.set_manual_cover("哥布林杀手", self.png(b"A"))
        s2 = covers.set_manual_cover("哥布林杀手 S2", self.png(b"B"))
        self.assertTrue(s1["ok"] and s2["ok"])
        self.assertNotEqual(s1["file"], s2["file"])
        # 后设的那张绝不覆盖先设的
        self.assertEqual((covers.config.COVER_DIR / s1["file"]).read_bytes()[-1:], b"A")
        self.assertEqual((covers.config.COVER_DIR / s2["file"]).read_bytes()[-1:], b"B")
        self.assertEqual(covers.resolve_cover("哥布林杀手"), s1["file"])
        self.assertEqual(covers.resolve_cover("哥布林杀手 S2"), s2["file"])
        self.assertEqual(covers.resolve_cover("哥布林杀手 第二季"), s2["file"])

    def test_unset_season_falls_back_to_work_cover(self):
        """只给作品设了图时，第二季仍能显示它（默认共享，不是强行覆盖）。"""
        s1 = covers.set_manual_cover("哥布林杀手", self.png(b"A"))
        self.assertEqual(covers.resolve_cover("哥布林杀手 S2"), s1["file"])
        self.assertFalse(covers.is_manual("哥布林杀手 S2"))

    def test_clearing_one_season_keeps_the_other(self):
        s1 = covers.set_manual_cover("哥布林杀手", self.png(b"A"))
        s2 = covers.set_manual_cover("哥布林杀手 S2", self.png(b"B"))
        self.assertTrue(covers.clear_manual_cover("哥布林杀手 S2")["ok"])
        self.assertFalse(covers.is_manual("哥布林杀手 S2"))
        self.assertTrue(covers.is_manual("哥布林杀手"))
        self.assertTrue((covers.config.COVER_DIR / s1["file"]).is_file())
        self.assertFalse((covers.config.COVER_DIR / s2["file"]).is_file())

    def test_poster_wall_season_cards_resolve_own_cover(self):
        """剧集墙季卡片必须拿自己那张：候选里自身标题要排在作品名之前。"""
        self.media("01.mkv", title_guess="Goblin Slayer", title_cn="哥布林杀手",
                   season_no=1, episode_no=1)
        self.media("02.mkv", title_guess="Goblin Slayer", title_cn="哥布林杀手",
                   season_no=2, episode_no=1)
        s1 = covers.set_manual_cover("哥布林杀手 · 第 1 季", self.png(b"A"))
        s2 = covers.set_manual_cover("哥布林杀手 · 第 2 季", self.png(b"B"))
        data = self.api.get_poster_wall({"limit": 50})
        resolved = data.get("covers") or {}
        by_season = {int(c.get("season_no") or 0): resolved.get(c.get("title"), "")
                     for c in data.get("season_cards") or []}
        self.assertEqual(by_season.get(1), s1["file"], data.get("season_cards"))
        self.assertEqual(by_season.get(2), s2["file"], data.get("season_cards"))

    def test_season_candidates_put_own_title_first(self):
        """源码契约：季卡片候选顺序不许再退回「作品名优先」。"""
        import inspect
        src = inspect.getsource(Api.get_poster_wall)
        block = src.split("for card in season_cards:", 1)[1].split("resolved =", 1)[0]
        self.assertLess(block.index('card["title"]'), block.index('card["work_title"]'))


class ManualCoverFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web" / "style.css").read_text(encoding="utf-8")

    def test_set_cover_button_and_dialog_are_wired(self):
        for token in ('data-action="set-cover"', "case 'set-cover':",
                      "coverDialog(", "call('set_cover'", "call('clear_cover'",
                      "call('choose_image_file')", "addEventListener('paste'",
                      "readAsDataURL", "setChosen('datauri'", "srcType: 'path'",
                      "setChosen('url'"):
            self.assertIn(token, self.js)
        # 剧集墙和看过的列表卡片墙都要能改封面
        self.assertGreaterEqual(self.js.count('data-action="set-cover"'), 2)

    def test_poster_card_still_has_no_play_next_button(self):
        block = self.js.split("function posterCardHtml(", 1)[1].split("function renderPosterCards", 1)[0]
        self.assertNotIn('data-action="play"', block)
        self.assertNotIn(">播放下一集</button>", block)

    def test_cover_modal_has_styles(self):
        self.assertIn(".cover-modal {", self.css)
        self.assertIn(".cover-preview img", self.css)

    def test_covers_module_has_no_scheduler(self):
        import inspect
        import app.covers as mod
        src = inspect.getsource(mod)
        self.assertNotIn("Timer(", src)
        self.assertNotIn("schedule", src.lower().replace("scheduled", ""))


if __name__ == "__main__":
    unittest.main()
