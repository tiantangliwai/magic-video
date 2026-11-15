"""网页搜索回归：两站结果解析、来源标记、失败降级与打开链接的安全边界。

真实站点会改版，所以这里用**真实页面片段**做夹具（2026-11-15 实测结构），
网络请求一律 mock，测试不联网。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from app import db, websearch
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web/app.js"
INDEX_HTML = ROOT / "web/index.html"

PLACEHOLDER = "https://p1.bdxiguaimg.com/origin/137860000e3053288f11f"
RAW_CDN = "https://cdn.aqdstatic.com:966/age/covers/20230207.jpg"


def agedm_block(title: str, detail_id: str, status: str = "第28集(完结)") -> str:
    """复刻 AGE 搜索页单个结果块：src 是共用占位图，data-original 才是真封面。"""
    return (
        '<div class="card cata_video_item py-4">'
        '<div class="d-flex"><div class="video_cover"><div class="video_cover_wrapper">'
        f'<a href="http://www.agedm.io/detail/{detail_id}" class="d-block" title="{title}">'
        f'<img class="video_thumbs lazyload" src="{PLACEHOLDER}" '
        f'data-original="https://gimg4.baidu.com/gimg/app=2001&amp;n=0&amp;g=0n&amp;fmt=jpeg'
        f'&amp;src={quote(RAW_CDN, safe="")}" alt="{title}"></a>'
        f'<span class="video_play_status rounded text-truncate">{status}</span>'
        "</div></div></div></div>"
    )


AGEDM_PAGE = (
    "<main>"
    + agedm_block("葬送的芙莉莲 第二季", "20260029", "第10集(完结)")
    + agedm_block("葬送的芙莉莲", "20230207")
    + '</main><div class="pagination">1</div>'
)

SEEDUCK_PAGE = """
<div class="item">
  <a target="_blank" rel="noopener noreferrer" title="葬送的芙莉莲 第二季 葬送のフリーレン 第2期"
     class="image" href="/movies/119058/">
    <img src="https://sh1.pcie.pppoe.top/static/pics/p1768928612487902821.jpg"
         alt="葬送的芙莉莲 第二季 葬送のフリーレン 第2期" ></a>
  <ul>
    <li><h2><a target="_blank" href="/movies/119058/" class="header-anchor">#</a> 葬送的芙莉莲 第二季</h2></li>
    <li>2026 / 动漫 / 日本 / 日语 / 种崎敦美 市之濑加那 </li>
  </ul>
</div>
<div class="item">
  <a title="葬送的芙莉莲 葬送のフリーレン" class="image" href="/movies/110630/">
    <img src="https://sh1.pcie.pppoe.top/static/pics/p110630.jpg" alt="x"></a>
  <ul><li><h2>葬送的芙莉莲</h2></li>
    <li>2023 / 动漫 / 日本 / 日语 / 种崎敦美 </li></ul>
</div>
"""


class _Resp:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code


class ParseTests(unittest.TestCase):

    def test_agedm_items_have_title_detail_url_cover_and_status(self):
        items = websearch.parse_agedm(AGEDM_PAGE)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["source"], "agedm")
        self.assertEqual(first["source_label"], "AGE动漫")
        self.assertEqual(first["title"], "葬送的芙莉莲 第二季")
        # http 详情页升级成 https，避免浏览器拦混合内容
        self.assertEqual(first["url"], "https://www.agedm.io/detail/20260029")
        self.assertEqual(first["cover"], RAW_CDN)      # 必须是原始 CDN，不是占位图
        self.assertEqual(first["meta"], "第10集(完结)")

    def test_agedm_placeholder_src_never_becomes_cover(self):
        for item in websearch.parse_agedm(AGEDM_PAGE):
            self.assertNotEqual(item["cover"], PLACEHOLDER)

    def test_seeduck_uses_h2_short_title_and_absolute_url(self):
        items = websearch.parse_seeduck(SEEDUCK_PAGE)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["source"], "seeduck")
        self.assertEqual(first["source_label"], "Seeduck")
        self.assertEqual(first["title"], "葬送的芙莉莲 第二季")   # 不是带日文副标题的长 title
        self.assertEqual(first["url"], "https://seeduck.cc/movies/119058/")
        self.assertTrue(first["cover"].startswith("https://"))
        self.assertIn("2026", first["meta"])

    def test_empty_page_yields_no_items(self):
        self.assertEqual(websearch.parse_agedm("<html></html>"), [])
        self.assertEqual(websearch.parse_seeduck("<html></html>"), [])

    def test_search_urls_match_the_two_official_forms(self):
        self.assertEqual(websearch.search_url("agedm", "零之使魔"),
                         "https://www.agedm.io/search?query=%E9%9B%B6%E4%B9%8B%E4%BD%BF%E9%AD%94")
        self.assertEqual(websearch.search_url("seeduck", "零之使魔"),
                         "https://seeduck.cc/s/%E9%9B%B6%E4%B9%8B%E4%BD%BF%E9%AD%94/")


class SearchTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        db.init_db()

    def _fake_get(self, mapping):
        def _get(url, **kwargs):
            for token, resp in mapping.items():
                if token in url:
                    if isinstance(resp, Exception):
                        raise resp
                    return resp
            return _Resp("<html></html>")
        return mock.patch.object(websearch.covers, "_get", side_effect=_get)

    def test_both_sources_are_merged_and_labelled(self):
        with self._fake_get({"agedm.io": _Resp(AGEDM_PAGE), "seeduck.cc": _Resp(SEEDUCK_PAGE)}):
            result = websearch.search("葬送的芙莉莲")
        self.assertTrue(result["ok"])
        sources = {it["source"] for it in result["items"]}
        self.assertEqual(sources, {"agedm", "seeduck"})
        self.assertEqual(len(result["items"]), 4)
        self.assertEqual(result["errors"], {})
        self.assertEqual(result["links"]["agedm"],
                         "https://www.agedm.io/search?query=%E8%91%AC%E9%80%81%E7%9A%84%E8%8A%99%E8%8E%89%E8%8E%B2")

    def test_one_source_failing_keeps_the_other(self):
        with self._fake_get({"agedm.io": RuntimeError("timeout"),
                             "seeduck.cc": _Resp(SEEDUCK_PAGE)}):
            result = websearch.search("葬送的芙莉莲")
        self.assertTrue(result["ok"])
        self.assertEqual({it["source"] for it in result["items"]}, {"seeduck"})
        self.assertIn("agedm", result["errors"])
        # 失败方仍然给出搜索页链接，前端据此显示「去站点搜索」
        self.assertIn("agedm", result["links"])

    def test_http_error_is_reported_not_raised(self):
        with self._fake_get({"agedm.io": _Resp("blocked", 403),
                             "seeduck.cc": _Resp(SEEDUCK_PAGE)}):
            result = websearch.search("零之使魔")
        self.assertIn("403", result["errors"]["agedm"])

    def test_blacklisted_titles_are_filtered_out(self):
        db.set_setting("video_blacklist", "芙莉莲")
        blacklist_page = ("<main>" + agedm_block("芙莉莲 第一季", "111")
                          + agedm_block("进击的巨人", "222") + "</main>")
        with self._fake_get({"agedm.io": _Resp(blacklist_page),
                             "seeduck.cc": _Resp("<html></html>")}):
            result = websearch.search("随便")
        titles = [it["title"] for it in result["items"]]
        self.assertNotIn("芙莉莲 第一季", titles)
        self.assertIn("进击的巨人", titles)

    def test_blank_keyword_is_rejected(self):
        result = websearch.search("   ")
        self.assertFalse(result["ok"])
        self.assertEqual(result["items"], [])

    def test_cache_avoids_second_network_round(self):
        with self._fake_get({"agedm.io": _Resp(AGEDM_PAGE), "seeduck.cc": _Resp(SEEDUCK_PAGE)}) as get:
            websearch.search("葬送的芙莉莲")
            first = get.call_count
            websearch.search("葬送的芙莉莲")
            self.assertEqual(get.call_count, first)

    def test_cache_can_be_bypassed(self):
        with self._fake_get({"agedm.io": _Resp(AGEDM_PAGE), "seeduck.cc": _Resp(SEEDUCK_PAGE)}) as get:
            websearch.search("葬送的芙莉莲")
            first = get.call_count
            websearch.search("葬送的芙莉莲", use_cache=False)
            self.assertGreater(get.call_count, first)


class ApiTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        db.init_db()
        self.api = Api()

    def test_web_search_delegates(self):
        with mock.patch.object(websearch, "search",
                               return_value={"ok": True, "items": []}) as search:
            self.assertTrue(self.api.web_search("进击", 10)["ok"])
        search.assert_called_once_with("进击", 10)

    def test_open_url_rejects_non_http_schemes(self):
        for bad in ("file:///C:/Windows/win.ini", "javascript:alert(1)",
                    "ms-settings:", "C:/temp/a.mp4", "", "http://"):
            self.assertFalse(self.api.open_url(bad)["ok"], bad)

    def test_open_url_uses_webbrowser_for_https(self):
        with mock.patch("webbrowser.open", return_value=True) as opened:
            result = self.api.open_url("https://seeduck.cc/movies/119058/")
        self.assertTrue(result["ok"], result)
        opened.assert_called_once()


class FrontendContractTests(unittest.TestCase):

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_view_is_registered_in_all_three_places(self):
        self.assertIn('data-view="websearch"', self.html)
        self.assertIn("websearch: ['网页搜索'", self.js)
        self.assertIn("websearch: renderWebSearch", self.js)
        self.assertIn("async function renderWebSearch()", self.js)

    def test_actions_are_wired(self):
        for token in ("case 'web-search'", "case 'web-filter'", "case 'web-link'",
                      "case 'web-open'", "call('web_search'", "call('open_url'"):
            self.assertIn(token, self.js, token)

    def test_source_badge_is_rendered_per_card(self):
        # 每张卡都要标来源：这是用户明确要求的「转化到页面中并标记来源」
        self.assertIn("it.source_label", self.js)
        self.assertIn("poster-tag", self.js)

    def test_enter_key_triggers_search(self):
        block = self.js.split("document.addEventListener('keydown'", 1)[1]
        self.assertIn("ev.target.id === 'webSearch'", block)
        self.assertIn("data-action=\"web-search\"", self.js)

    def test_failed_parse_still_offers_site_search_buttons(self):
        self.assertIn('data-action="web-link" data-source="agedm"', self.js)
        self.assertIn('data-action="web-link" data-source="seeduck"', self.js)


if __name__ == "__main__":
    unittest.main()
