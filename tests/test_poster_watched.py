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

    def test_watched_only_card_carries_row_ids_and_work_key(self):
        """合成卡要给出「改名用的行 id 组」与「评分用的 work_key」。

        用户 2026-11-19：本地没文件的卡片也要能改名 / 评分 / 设定封面。
        同一作品可能有多季多条记录（S1 一行、S2 一行），改名必须整组一起改，
        否则改完一张卡会裂成「新名字 + 旧名字」两张；评分 key 用卡片展示名，
        与卡片自己的标题同源。
        """
        self._add_watched("多季作品")
        self._add_watched("多季作品")
        cards = self.client.get_poster_wall({"source": "watched"})["cards"]
        card = next(c for c in cards if c["title"] == "多季作品")
        self.assertTrue(card["readonly"])
        self.assertGreaterEqual(len(card["watched_ids"]), 2)
        self.assertEqual(card["work_key"], "watched::多季作品")

    def test_watched_only_card_settings_cover_shows_on_wall(self):
        """合成卡「设定封面」要真的生效：设完剧集墙就能取到这张图。

        这类卡没有本地文件，封面只能从 subjects(source='manual') 来；
        之前只读卡连按钮都没有，用户没法给它们换掉自动抓错的图。
        """
        import base64
        self._add_watched("纯记录作品B")
        self.assertEqual(self.client.get_poster_wall({"source": "watched"})["covers"], {})
        uri = "data:image/jpeg;base64," + base64.b64encode(
            b"\xff\xd8\xff" + b"x" * 1024).decode("ascii")
        r = self.client.set_cover("纯记录作品B", uri, "datauri")
        self.assertTrue(r["ok"], r)
        wall = self.client.get_poster_wall({"source": "watched"})
        self.assertIn("纯记录作品B", wall["covers"])
        out = self.client.cover_data_uris(["纯记录作品B"])
        self.assertTrue(out["纯记录作品B"].startswith("data:image/jpeg;base64,"))

    def test_watched_only_card_rating_key_is_the_card_title(self):
        """给合成卡打分后，用卡片展示名能立刻取回这条标注。"""
        self._add_watched("纯记录作品C")
        card = self.client.get_poster_wall({"source": "watched"})["cards"][0]
        r = self.client.save_work_meta(card["work_key"], "8", "神作")
        self.assertTrue(r.get("ok", True), r)
        got = self.client.get_work_meta([card["work_key"]])
        self.assertEqual(str(got["items"].get(card["work_key"], {}).get("rating")), "8")

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

    def test_readonly_card_has_only_local_record_actions(self):
        """本地没文件的合成卡：改名 / 评分 / 设定封面要能用，「查看分集 / 清理」不能。

        用户 2026-11-19 反馈：「看过 · 本地暂无文件 这些还是不能改名、评分、设定封面」。
        这三件事只依赖本地记录（watched_list + subjects + work_meta），与有没有下载无关，
        所以必须在编辑模式下给出来；而「查看分集 / 清理本地文件」确实无对象，不能给。
        """
        block = self.js.split("if (card.readonly) {", 1)[1].split("const seasons =", 1)[0]
        self.assertIn("is-readonly", block)
        self.assertIn("本地暂无文件", block)
        # 能用的三个动作：改名在本块内，评分 / 设定封面由共用的 metaButtons 提供
        self.assertIn('data-action="rename-watched-group"', block)
        self.assertIn("data-sync-cn", block)          # 合成卡展示名是 title_cn，要一起改
        self.assertIn("${metaButtons}", block)
        meta_block = self.js.split("const metaButtons = editMode ?", 1)[1].split(
            "if (card.readonly) {", 1)[0]
        self.assertIn('data-action="rate-work"', meta_block)
        self.assertIn('data-action="set-cover"', meta_block)
        # 不能用的两个动作
        for action in ("rename-work", "delete-series-files", 'data-action="goto"'):
            self.assertNotIn(action, block)
        # 和别的卡片一样收在编辑模式里，纯查看模式不显示
        self.assertIn("editMode && ids.length", block)

    def test_readonly_card_rename_uses_watched_row_ids(self):
        """改名要带上同作品的全部 watched_list 行 id，否则一张卡会裂成两张。"""
        block = self.js.split("if (card.readonly) {", 1)[1].split("const seasons =", 1)[0]
        self.assertIn("card.watched_ids", block)
        self.assertIn("data-ids=", block)


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
        block = self.js.split("const editButtons = editMode ?", 1)[1].split(
            "return `<div class=\"poster-card\"", 1)[0]
        self.assertIn("rename-work", block)
        self.assertIn("${cleanup}", block)
        # 评分 / 设定封面抽成 metaButtons（本地卡与「本地暂无文件」卡共用一份），
        # 它自己也在 editMode 三元里，纯查看模式同样不出现。
        meta_block = self.js.split("const metaButtons = editMode ?", 1)[1].split(
            "if (card.readonly) {", 1)[0]
        for action in ("rate-work", "set-cover"):
            self.assertIn(action, meta_block)
        self.assertIn("${metaButtons}", block)
        # 清理按钮本身也带 delete-series-files，且只在 editMode 分支被拼进来
        cleanup_block = self.js.split("const cleanup = card.all_done", 1)[1].split(
            "const removedTag =", 1)[0]
        self.assertIn("delete-series-files", cleanup_block)
        # 编辑按钮全在 editMode 分支里；返回模板只拼 editButtons / metaButtons，没有裸按钮
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
        func = self.js.split(
            "function posterCardHtml(card, cover, editMode = posterState.editMode) {", 1)[1].split(
            "function renderPosterCards()", 1)[0]
        block = func.split('return `<div class="poster-card"', 1)[1].split(
            "${art}${posterStateTag(card)}", 1)[0]
        self.assertIn('data-title="${esc(card.title)}"', block)

    def test_cover_dialog_clear_invalidates_only_one(self):
        block = self.js.split("if (e.target.closest('#coverClear')) {", 1)[1].split("}", 2)[1]
        self.assertIn("invalidateCoverCache(title)", block)
        self.assertNotIn("invalidateCoverCache();", block)


class WatchedCardActionsFrontendTests(unittest.TestCase):
    """看过的列表：除「查看分集」外，剧集墙卡片上的编辑按钮都要有。

    用户反馈：「看过的列表为什么不能设定封面？除了查看分集，其他的按钮都应该有」。
    这些是纯前端契约：卡片墙 / 表格两张皮都必须提供改名、评分、设定封面，
    并且评分与设定封面要能就地刷新（不整页重载、避免重演「设封面卡一下」）。
    """

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def _cards(self):
        return self.js.split("function watchedCardsHtml(groups) {", 1)[1].split(
            "// 只重写列表容器", 1)[0]

    def _row(self):
        return self.js.split("function watchedRowHtml(r) {", 1)[1].split(
            "// 看过的列表里「一部作品」", 1)[0]

    def test_card_wall_has_edit_buttons(self):
        block = self._cards()
        for action in ("rename-watched-group", "rate-work", "set-cover", "watched-view-mode"):
            self.assertIn(f'data-action="{action}"', block)
        # 「查看分集」不进来：看过的作品很可能没有本地文件，跳过去只会是空列表
        self.assertNotIn('data-view="library"', block)

    def test_card_wall_has_title_for_cover_refresh(self):
        self.assertIn('<div class="poster-card" data-title="${esc(g.key)}">', self._cards())

    def test_table_row_has_edit_buttons(self):
        block = self._row()
        for action in ("edit-watched", "rate-work", "set-cover", "delete-watched"):
            self.assertIn(f'data-action="{action}"', block)

    def test_rename_group_case_updates_every_row(self):
        block = self.js.split("case 'rename-watched-group': {", 1)[1].split("case '", 1)[0]
        self.assertIn("call('update_watched'", block)
        self.assertIn("for (const id of ids)", block)
        self.assertIn("await renderWatched()", block)

    def test_rate_work_refreshes_watched_list(self):
        block = self.js.split("case 'rate-work': {", 1)[1].split("case '", 1)[0]
        self.assertIn("currentView === 'watched'", block)
        self.assertIn("watchedMetas[key] = r", block)
        self.assertIn("renderWatchedRows()", block)

    def test_set_cover_uses_partial_refresh_for_watched(self):
        block = self.js.split("case 'set-cover': {", 1)[1].split("case '", 1)[0]
        self.assertIn("currentView === 'watched'", block)
        # 设定 / 清除都只刷新这一张，不再整页 renderWatched()
        self.assertNotIn("await renderWatched()", block)

    def test_cover_refresh_covers_watched_grid(self):
        block = self.js.split("async function refreshPosterCoverCard(title) {", 1)[1].split(
            "// ---------- 观影报告", 1)[0]
        self.assertIn("watchedCovers[title] = cover", block)
        self.assertIn("getElementById('watchedCards')", block)


if __name__ == "__main__":
    unittest.main()
