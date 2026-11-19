"""剧集墙「是否看过」标记：本地进度 + 看过的列表，两路合起来判定。

需求原话（用户 2026-11-15）：剧集墙中增加是否已经看过的标记。

设计要点：
* 后端 ``_watch_state()`` 算好五态（done / partial / watching / listed / none），
  前端只按字符串渲染，不在 JS 里重算优先级——两处判断迟早会不一致。
* 「看过的列表」复用 ``watchlist.match_files`` 的宽松匹配，但**必须再过一层标题
  校验**：否则《斯巴达克斯》会被标成《斯巴达300勇士》、《内情 The inside story》
  会被标成《After Story》、一部英文片会被 `real真实` 标成看过。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import api, db, watchlist
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
APP_JS = ROOT / "web" / "app.js"
STYLE = ROOT / "web" / "style.css"


class PosterWatchStateTests(IsolatedDbTestCase):
    def _show(self, name: str, files: int = 2, *, done: int = 0, half: int = 0) -> list[int]:
        """造一部作品：独立子目录 + 至少两个文件（单文件目录不算发布目录）。"""
        folder = self.tmp / name
        folder.mkdir(exist_ok=True)
        ids: list[int] = []
        for idx in range(1, files + 1):
            filename = f"{name} {idx:02d}.mkv"
            row = self.media(filename, path=str(folder / filename), title_guess=name)
            ids.append(int(row["id"]))
        for pos, fid in enumerate(ids):
            completed = 1 if pos < done else 0
            position = 1000 if (completed or pos < half) else 0
            db.execute(
                "INSERT INTO watch_progress(file_id, position_ms, percent, completed, "
                "play_count, last_played_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (fid, position, 0, completed, 1 if position else 0, db.now(), db.now()),
            )
        return ids

    def _add_watched(self, title: str, title_cn: str | None = None) -> None:
        db.execute(
            "INSERT INTO watched_list(title, title_cn, source, status) VALUES (?, ?, ?, ?)",
            (title, title_cn if title_cn is not None else title, "manual", "watched"),
        )

    def _card(self, client, name: str) -> dict:
        wall = client.get_poster_wall({})
        for card in wall["cards"]:
            if name in card["title"]:
                return card
        self.fail(f"没找到作品卡片 {name}；当前：{[c['title'] for c in wall['cards']]}")

    # ---- 本地播放进度 ---------------------------------------------------
    def test_all_episodes_completed_marks_done(self):
        self._show("全看完的作品", files=3, done=3)
        card = self._card(api.Api(), "全看完的作品")
        self.assertEqual(card["watch_state"], "done")
        self.assertTrue(card["all_done"])
        self.assertEqual(card["watched_count"], 3)

    def test_some_episodes_completed_marks_partial(self):
        self._show("看了一部分的作品", files=4, done=2)
        card = self._card(api.Api(), "看了一部分的作品")
        self.assertEqual(card["watch_state"], "partial")
        self.assertEqual(card["watched_count"], 2)
        self.assertEqual(card["file_count"], 4)

    def test_half_played_episode_marks_watching(self):
        self._show("看了一半的作品", files=2, half=1)
        card = self._card(api.Api(), "看了一半的作品")
        self.assertEqual(card["watch_state"], "watching")
        self.assertEqual(card["playing_count"], 1)
        self.assertEqual(card["watched_count"], 0)

    def test_untouched_show_is_none(self):
        self._show("没碰过的作品")
        card = self._card(api.Api(), "没碰过的作品")
        self.assertEqual(card["watch_state"], "none")
        self.assertFalse(card["watched_listed"])

    # ---- 看过的列表 -----------------------------------------------------
    def test_watched_list_marks_card_as_listed(self):
        self._show("更衣人偶坠入爱河")
        self._add_watched("更衣人偶坠入爱河")
        card = self._card(api.Api(), "更衣人偶坠入爱河")
        self.assertEqual(card["watch_state"], "listed")
        self.assertTrue(card["watched_listed"])

    def test_loose_keyword_match_does_not_mark_unrelated_show(self):
        """回归：watchlist 的宽松匹配会把「斯巴达300勇士」算到「斯巴达克斯」头上。"""
        self._show("斯巴达克斯")
        self._add_watched("斯巴达300勇士")
        # 先确认宽松匹配这一层**确实**命中了（否则这条用例根本没验证到防护逻辑）
        count, _hits, keyword = watchlist.match_files("斯巴达300勇士", watchlist.media_index())
        self.assertTrue(count, "宽松匹配本应命中，说明用例前提不成立")
        self.assertEqual(keyword, "斯巴达")
        card = self._card(api.Api(), "斯巴达克斯")
        self.assertEqual(card["watch_state"], "none")
        self.assertFalse(card["watched_listed"])

    def test_progress_wins_over_watched_list(self):
        self._show("既在看又导入过的作品", files=3, done=1)
        self._add_watched("既在看又导入过的作品")
        card = self._card(api.Api(), "既在看又导入过的作品")
        self.assertEqual(card["watch_state"], "partial")
        self.assertTrue(card["watched_listed"])

    def test_adding_watched_entry_invalidates_wall_cache(self):
        """导入 / 同步看过的列表后，标记必须立刻变，不能停在旧缓存。"""
        self._show("后补进看过的列表")
        client = api.Api()
        self.assertEqual(self._card(client, "后补进看过的列表")["watch_state"], "none")
        self._add_watched("后补进看过的列表")
        self.assertEqual(self._card(client, "后补进看过的列表")["watch_state"], "listed")


class PosterWatchedCardsTests(IsolatedDbTestCase):
    """剧集墙纳入「看过的列表」里本地无文件的作品 + 来源 / 状态过滤。"""

    def setUp(self):
        super().setUp()
        self.client = api.Api()

    def _add_watched(self, title, title_cn=None):
        db.execute(
            "INSERT INTO watched_list(title, title_cn, source, status) VALUES (?, ?, ?, ?)",
            (title, title_cn if title_cn is not None else title, "manual", "watched"),
        )

    def _titles(self, opt):
        return [c["title"] for c in self.client.get_poster_wall(opt)["cards"]]

    def test_default_all_merges_local_and_watched_only(self):
        # 本地一部 + 看过列表两部（其中一部本地已有，一部只有记录）
        folder = self.tmp / "本地作品A"
        folder.mkdir(exist_ok=True)
        self.media("本地作品A 01.mkv", path=str(folder / "本地作品A 01.mkv"),
                   title_guess="本地作品A")
        self.media("本地作品A 02.mkv", path=str(folder / "本地作品A 02.mkv"),
                   title_guess="本地作品A")
        self._add_watched("本地作品A")            # Duplicate of local
        self._add_watched("只有记录的番B")          # watched-only
        titles = self._titles({})                   # 默认 source=all
        self.assertIn("本地作品A", titles)
        self.assertIn("只有记录的番B", titles)
        # 本地已有的不重复补成只读卡
        self.assertEqual(titles.count("本地作品A"), 1)

    def test_source_local_excludes_watched_only(self):
        self._add_watched("仅看过列表的作品")
        self.assertNotIn("仅看过列表的作品", self._titles({"source": "local"}))
        self.assertIn("仅看过列表的作品", self._titles({"source": "all"}))

    def test_source_watched_returns_only_readonly_cards(self):
        folder = self.tmp / "本地作品C"
        folder.mkdir(exist_ok=True)
        self.media("本地作品C 01.mkv", path=str(folder / "本地作品C 01.mkv"),
                   title_guess="本地作品C")
        self._add_watched("仅看过列表的作品")
        wall = self.client.get_poster_wall({"source": "watched"})
        self.assertEqual([c["title"] for c in wall["cards"]], ["仅看过列表的作品"])
        card = wall["cards"][0]
        self.assertTrue(card["readonly"])
        self.assertEqual(card["file_count"], 0)
        self.assertEqual(card["watch_state"], "listed")
        # 合成卡的标题也纳入封面解析字典（有封面则出现，无封面则不在，均不报错）
        self.assertIsInstance(wall["covers"], dict)

    def test_watched_only_card_is_readonly_with_no_file_fields(self):
        self._add_watched("纯记录作品")
        card = self.client.get_poster_wall({"source": "watched"})["cards"][0]
        self.assertTrue(card["readonly"])
        self.assertIsNone(card["next_file_id"])
        self.assertEqual(card["removable_count"], 0)
        self.assertTrue(card["group_id"].startswith("watched::"))

    def test_match_state_matched_unmatched_on_watched_cards(self):
        self._add_watched("有中文名的番", "有中文名的番")
        # 无中文名：title_cn 留空，卡片标题回落到 title
        db.execute(
            "INSERT INTO watched_list(title, title_cn, source, status) VALUES (?, ?, ?, ?)",
            ("RAW-TITLE-ONLY", "", "manual", "watched"),
        )
        matched = self._titles({"source": "watched", "match_state": "matched"})
        unmatched = self._titles({"source": "watched", "match_state": "unmatched"})
        self.assertIn("有中文名的番", matched)
        self.assertNotIn("RAW-TITLE-ONLY", matched)
        self.assertIn("RAW-TITLE-ONLY", unmatched)
        self.assertNotIn("有中文名的番", unmatched)

    def test_keyword_filters_watched_cards(self):
        self._add_watched("搜索关键词测试番")
        self._add_watched("另一个番")
        titles = self._titles({"source": "watched", "keyword": "关键词"})
        self.assertEqual(titles, ["搜索关键词测试番"])

    def test_progress_state_excludes_watched_only(self):
        self._add_watched("没进度的番")
        self.assertEqual(self._titles({"source": "all", "match_state": "progress"}), [])

    def test_default_all_limit_is_high_enough_to_show_watched_cards(self):
        # 真库约 228 本地组 + 130 看过条目 > 旧的 300 上限；默认 all 的 limit
        # 必须放宽，否则合成卡排在本地卡后面会被整段截断，用户根本看不到。
        for i in range(310):
            folder = self.tmp / f"本地{i}"
            folder.mkdir(exist_ok=True)
            self.media(f"本地{i} 01.mkv", path=str(folder / f"本地{i} 01.mkv"),
                       title_guess=f"本地{i}")
        self._add_watched("压在最后的看过番")
        wall = self.client.get_poster_wall({})   # 默认 all，不传 limit
        self.assertIn("压在最后的看过番", [c["title"] for c in wall["cards"]])


class WatchStateRuleTests(unittest.TestCase):
    def test_priority_order(self):
        self.assertEqual(api._watch_state(True, 5, 2, True), "done")
        self.assertEqual(api._watch_state(False, 2, 1, True), "partial")
        self.assertEqual(api._watch_state(False, 0, 1, True), "watching")
        self.assertEqual(api._watch_state(False, 0, 0, True), "listed")
        self.assertEqual(api._watch_state(False, 0, 0, False), "none")

    def test_title_contains_is_strict(self):
        self.assertTrue(api._title_contains(["更衣人偶坠入爱河"], "更衣人偶坠入爱河"))
        # 归一化后连字符 / 空格不同也算同一部
        self.assertTrue(api._title_contains(["克雷瓦提斯 魔兽之王与婴儿与尸之勇者"],
                                            "克雷瓦提斯-魔兽之王与婴儿与尸之勇者"))
        # 脏目录名里含真片名：4 个字以上认包含
        self.assertTrue(api._title_contains(["crazecat下载加速器]《隐藏的面孔》HD"], "隐藏的面孔"))
        # 通用前缀 / 短词不认
        self.assertFalse(api._title_contains(["斯巴达克斯 第二季"], "斯巴达300勇士"))
        self.assertFalse(api._title_contains(["内情 The inside story"], "After Story"))
        self.assertFalse(api._title_contains(["Melody Marks"], "real真实"))
        self.assertFalse(api._title_contains(["新妹魔王的契约者2"], "魔王2099"))

    def test_empty_matches_never_listed(self):
        self.assertFalse(api.Api._listed_in_watched_list({"files": []}, [], []))


class PosterMarkerFrontendTests(unittest.TestCase):
    """前端只渲染后端给的 watch_state，样式跟主题走。"""

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.css = STYLE.read_text(encoding="utf-8")

    def test_five_states_have_tags(self):
        for cls_name in ("is-done", "is-part", "is-watching", "is-listed", "is-none"):
            self.assertIn(cls_name, self.js, cls_name)
            self.assertIn(cls_name, self.css, cls_name)

    def test_tag_reads_backend_watch_state(self):
        self.assertIn("card.watch_state", self.js)
        self.assertNotIn("card.watched_count ? 'done'", self.js)

    def test_tag_is_pinned_to_cover_corner(self):
        self.assertIn(".poster-card { position: relative", self.css)
        self.assertIn(".poster-state { position: absolute", self.css)

    def test_source_and_match_filters_in_poster_toolbar(self):
        block = self.js.split("async function renderPosters()", 1)[1].split(
            "// 全盘快搜是左侧独立入口", 1)[0]
        self.assertIn('id="posterSource"', block)
        self.assertIn('id="posterMatch"', block)
        for value in ("全部来源", "本地有文件", "看过列表"):
            self.assertIn(value, block)
        for value in ("已匹配中文", "未匹配中文", "有播放进度", "有封面", "没封面"):
            self.assertIn(value, block)

    def test_load_poster_wall_passes_source_and_match_state(self):
        block = self.js.split("async function loadPosterWall()", 1)[1].split(
            "posterState.cards = data.cards", 1)[0]
        self.assertIn("source: posterState.source", block)
        self.assertIn("match_state: posterState.match_state", block)

    def test_filter_change_triggers_reload(self):
        self.assertIn("el.id === 'posterSource'", self.js)
        self.assertIn("el.id === 'posterMatch'", self.js)

    def test_readonly_card_has_no_action_buttons(self):
        block = self.js.split("if (card.readonly) {", 1)[1].split("const seasons =", 1)[0]
        self.assertIn("is-readonly", block)
        for action in ("rename-work", "delete-series-files", "set-cover",
                       "rate-work", "data-action=\"goto\""):
            self.assertNotIn(action, block)
        self.assertIn("本地暂无文件", block)


class PosterEditModeFrontendTests(unittest.TestCase):
    """剧集墙「纯查看 / 编辑」切换 + 移除继续观看跳转的静态契约。

    需求原话（用户 2026-11-19）：增加纯查看模式和编辑模式的切换；两种模式都不需要
    「继续观看」直接跳转播放。默认纯查看，编辑按钮收进编辑模式。
    """

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_default_is_view_mode(self):
        state = self.js.split("let posterState = {", 1)[1].split("};", 1)[0]
        self.assertIn("editMode: false", state)
        self.assertNotIn("continueRow", state)

    def test_toggle_buttons_and_handler_exist(self):
        block = self.js.split("async function renderPosters()", 1)[1].split(
            "// 全盘快搜是左侧独立入口", 1)[0]
        self.assertIn('data-action="poster-edit-mode" data-edit="0"', block)
        self.assertIn('data-action="poster-edit-mode" data-edit="1"', block)
        self.assertIn("纯查看", block)
        self.assertIn("编辑模式", block)
        handler = self.js.split("case 'poster-edit-mode':", 1)[1].split("case 'rate-work':", 1)[0]
        self.assertIn("btn.dataset.edit === '1'", handler)
        self.assertIn("renderPosterCards()", handler)

    def test_edit_buttons_are_gated_by_edit_mode(self):
        block = self.js.split("const editButtons = posterState.editMode ?", 1)[1].split(
            "return `<div class=\"poster-card\">", 1)[0]
        for action in ("rename-work", "rate-work", "set-cover"):
            self.assertIn(action, block)
        self.assertIn("${cleanup}", block)
        # 清理按钮本身也带 delete-series-files，且只在 editMode 分支被拼进来
        cleanup_block = self.js.split("const cleanup = card.all_done", 1)[1].split(
            "const removedTag =", 1)[0]
        self.assertIn("delete-series-files", cleanup_block)
        # 四个按钮全在 editMode 分支里；返回模板只拼 editButtons，没有裸按钮
        returned = self.js.split('<div class="poster-actions">${editButtons}', 1)[1].split(
            "</div></div>`;", 1)[0]
        for action in ("rename-work", "rate-work", "set-cover", "delete-series-files"):
            self.assertNotIn(action, returned)
        self.assertIn('data-action="goto"', returned)

    def test_continue_row_fully_removed_from_poster_wall(self):
        block = self.js.split("// ---------- 剧集墙：卡片式展示", 1)[1].split(
            "// ---------- 观影报告", 1)[0]
        self.assertNotIn("get_continue_row", block)
        self.assertNotIn("renderContinueRow", block)
        self.assertNotIn("continueRow", block)
        self.assertNotIn('id="continueRow"', block)


class CoverUriKeyTests(unittest.TestCase):
    """封面 data URI map 的 key 必须与调用方查询用的标题完全一致（不 trim）。

    需求原话（用户 2026-11-19）：「有两个外出的剧集…给其中一个设定封面，设定会成功
    但是展示的时候还是没有封面」。根因：真实卡片标题「外出 … CHS 」末尾带空格，
    coverUris 内部 trim 后当 key，调用方用原始标题查 map 落空——封面明明存好了却
    显示占位图。修复：key 原样保留，trim 只用于判空。
    """

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_cover_uris_keeps_original_title_as_key(self):
        block = self.js.split("async function coverUris(titles) {", 1)[1].split(
            "async function invalidateCoverCache", 1)[0]
        # 构造 list 时 map 不再 trim（只 String 化），filter 用 t.trim() 判空
        self.assertIn("map(t => String(t == null ? '' : t))", block)
        self.assertIn("filter(t => t.trim())", block)
        self.assertNotIn("map(t => String(t || '').trim())", block)
        # 返回 map 的 key 就是 list 里的原样标题
        self.assertIn("list.forEach(t => { if (coverUriCache.has(t)) out[t] = coverUriCache.get(t); })",
                      block)

    def test_refresh_helper_uses_original_title_key(self):
        block = self.js.split("async function refreshPosterCoverCard(title) {", 1)[1].split(
            "// ---------- 观影报告", 1)[0]
        # 取图与写回都用同一个原始 title，不再二次 trim
        self.assertIn("got[title]", block)
        self.assertIn("posterState.covers[title]", block)


class PosterCoverRefreshTests(unittest.TestCase):
    """设定封面后局部刷新单卡，不整墙重载（用户 2026-11-19 反馈「触发重复加载，很慢」）。

    旧做法 set-cover 成功后 invalidateCoverCache() 全清 + loadPosterWall() 整墙重载，
    会把全部封面的 data URI 重传、后端签名重算、整墙 DOM 重建，打断其它编辑。
    """

    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")

    def test_invalidate_cache_supports_single_title(self):
        block = self.js.split("function invalidateCoverCache(title) {", 1)[1].split("\n}", 1)[0]
        self.assertIn("coverUriCache.delete(key)", block)
        self.assertIn("coverUriCache.clear", block)  # 无参仍全清，兼容旧调用
        self.assertIn("if (title)", block)
        # key 必须与 coverUris 一致：原样标题（含首尾空格），不能再 trim
        self.assertIn("String(title == null ? '' : title)", block)
        self.assertIn("coverUriCache.delete(key.trim())", block)  # 兼容清理历史遗留

    def test_refresh_helper_fetches_only_one_cover(self):
        block = self.js.split("async function refreshPosterCoverCard(title) {", 1)[1].split(
            "// ---------- 观影报告", 1)[0]
        # 只重取这一张，不触发整墙接口
        self.assertIn("coverUris([title])", block)
        for heavy in ("loadPosterWall", "get_poster_wall", "get_work_meta"):
            self.assertNotIn(heavy, block)
        # 精确失效该张；按 data-title 定位卡片，只替换它自己的 .poster-art
        self.assertIn("invalidateCoverCache(title)", block)
        self.assertIn(".poster-card", block)
        self.assertIn("dataset.title === title", block)
        self.assertIn("querySelector('.poster-art')", block)

    def test_set_cover_case_uses_local_refresh(self):
        block = self.js.split("case 'set-cover': {", 1)[1].split("case 'tree-toggle':", 1)[0]
        self.assertIn("await refreshPosterCoverCard(name)", block)
        # 成功分支与清除分支都不再整墙重载
        self.assertNotIn("loadPosterWall()", block)
        # 成功分支不再全清封面缓存（精确失效下沉到 refresh helper）
        self.assertNotIn("invalidateCoverCache();", block)

    def test_card_carries_title_for_targeting(self):
        func = self.js.split("function posterCardHtml(card, cover) {", 1)[1].split(
            "function renderPosterCards()", 1)[0]
        block = func.split('return `<div class="poster-card"', 1)[1].split(
            "${art}${posterStateTag(card)}", 1)[0]
        self.assertIn('data-title="${esc(card.title)}"', block)

    def test_cover_dialog_clear_invalidates_only_one(self):
        block = self.js.split("if (e.target.closest('#coverClear')) {", 1)[1].split("}", 2)[1]
        self.assertIn("invalidateCoverCache(title)", block)
        self.assertNotIn("invalidateCoverCache();", block)


if __name__ == "__main__":
    unittest.main()
