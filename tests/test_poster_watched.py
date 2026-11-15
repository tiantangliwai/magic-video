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


if __name__ == "__main__":
    unittest.main()
