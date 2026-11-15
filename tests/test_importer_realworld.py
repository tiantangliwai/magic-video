"""真实「看过的清单」解析：多季同行、纯季行依附、完结标记、括号说明、数字误判。

用户粘贴的清单不是整齐的「一行一作品」，而是混了作品名、S1/S2、剧场版、
（完）、括号说明、系列分组标题。这里用等价结构的中性样本固定解析契约。
"""
from __future__ import annotations

import unittest

from app import importer
from tests.base import IsolatedDbTestCase


class RealWorldParseTests(IsolatedDbTestCase):

    def test_multiple_seasons_on_one_line_expand(self):
        rows = importer.parse_text("关于我转生变成史莱姆这档事 S1 S2 S3")
        self.assertEqual([r["title"] for r in rows], ["关于我转生变成史莱姆这档事"] * 3)
        self.assertEqual([r["season_no"] for r in rows], [1, 2, 3])

    def test_multiple_seasons_no_space_between_cn_and_s(self):
        rows = importer.parse_text("我推的孩子S1 S2 S3")
        self.assertEqual([r["season_no"] for r in rows], [1, 2, 3])
        self.assertTrue(all(r["title"] == "我推的孩子" for r in rows))

    def test_five_seasons(self):
        rows = importer.parse_text("地下城邂逅是否搞错了什么 S1 S2 S3 S4 S5")
        self.assertEqual([r["season_no"] for r in rows], [1, 2, 3, 4, 5])

    def test_pure_season_lines_attach_to_previous_title(self):
        text = "\n".join([
            "怪兽8号", "S1 （完）", "S2 - （完）", "剧场版 - 侦查任务- （完）",
        ])
        rows = importer.parse_text(text)
        self.assertTrue(all(r["title"] == "怪兽8号" for r in rows))
        self.assertEqual([r["season_no"] for r in rows], [None, 1, 2, None])
        self.assertEqual(rows[3]["note"], "剧场版 - 侦查任务")

    def test_season_line_with_alias_attaches_to_previous(self):
        rows = importer.parse_text("出包王女\nS4 Trouble Darkness 2nd - 完")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["title"] == "出包王女" for r in rows))
        self.assertEqual(rows[1]["season_no"], 4)

    def test_chinese_season_inline(self):
        rows = importer.parse_text("月光下的异世界之旅 第二季")
        self.assertEqual(rows[0]["season_no"], 2)
        self.assertEqual(rows[0]["title"], "月光下的异世界之旅")

    def test_chinese_combined_seasons(self):
        rows = importer.parse_text("我独自升级一二季")
        self.assertEqual([r["season_no"] for r in rows], [1, 2])

    def test_paren_asides_and_completion_marks_are_stripped(self):
        r = importer.parse_line("《某科学的超电磁炮》第一季（24集）：独立日常篇，完善角色设定，建议先看。（完）")
        self.assertEqual(r["title"], "某科学的超电磁炮")
        self.assertEqual(r["season_no"], 1)
        self.assertIsNone(r["episode_from"])

    def test_book_quote_title_with_aside(self):
        r = importer.parse_line("《某科学的一方通行》（可选）：外传性质，非核心主线，随时可看。")
        self.assertEqual(r["title"], "某科学的一方通行")

    def test_numeric_titles_are_not_split_into_episodes(self):
        for line, expected in (
            ("魔王2099", "魔王2099"),
            ("斯巴达300勇士", "斯巴达300勇士"),
            ("29岁单身冒险者的日常", "29岁单身冒险者的日常"),
        ):
            r = importer.parse_line(line)
            self.assertEqual(r["title"], expected, line)
            self.assertIsNone(r["episode_from"], line)

    def test_standalone_year_still_removed(self):
        r = importer.parse_line("Totally Unknown Show XYZ 2099")
        self.assertEqual(r["title"], "Totally Unknown Show XYZ")
        self.assertIsNone(r["episode_from"])

    def test_movie_lines_attach_to_previous_work(self):
        rows = importer.parse_text("CLANNAD\nS1 （完）\nAfter Story （完）\n剧场版   （完）")
        self.assertTrue(all(r["title"] == "CLANNAD" for r in rows))
        self.assertEqual([r["season_no"] for r in rows], [None, 1, None, None])

    def test_series_heading_and_conversational_lines_are_skipped(self):
        text = "\n".join([
            "最后可以再拜托您一件事吗 S1", "大学生篇有哪些新作",
            "EVA四部曲", "序 - 完", "破 - 完", "Q - 完", "终- 完",
        ])
        rows = importer.parse_text(text)
        titles = [r["title"] for r in rows]
        self.assertFalse(any("拜托" in t or "有哪些新作" in t for t in titles))
        # 四部曲作为系列标题保留，四部剧场版依附它
        self.assertIn("EVA四部曲", titles)
        self.assertEqual(len([t for t in titles if t == "EVA四部曲"]), 5)

    def test_leading_chinese_season_line_attaches(self):
        rows = importer.parse_text("零之魔使\n第一季：《零之使魔》（2006 年，13 集）。 - 完")
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["title"] == "零之魔使" for r in rows))
        self.assertEqual(rows[1]["season_no"], 1)

    def test_duplicate_titles_are_deduplicated(self):
        rows = importer.parse_text("间谍过家家\n间谍过家家 第二季\n间谍过家家")
        self.assertEqual(len(rows), 2)

    def test_classic_formats_still_work(self):
        r = importer.parse_line("更衣人偶坠入爱河 S2 01-12")
        self.assertEqual(r["title"], "更衣人偶坠入爱河")
        self.assertEqual((r["season_no"], r["episode_from"], r["episode_to"]), (2, 1, 12))


class WatchedGroupingTests(IsolatedDbTestCase):
    """看过的列表按作品分组展示，避免多季条目散成一片。"""

    def test_group_watched_rows(self):
        rows = [
            {"title": "白色相簿", "title_cn": "白色相簿", "season_no": 1},
            {"title": "白色相簿", "title_cn": "白色相簿", "season_no": 2},
            {"title": "电锯人", "title_cn": "电锯人", "season_no": None},
        ]
        groups = importer.group_watched(rows)
        self.assertEqual([g["title"] for g in groups], ["白色相簿", "电锯人"])
        self.assertEqual(groups[0]["seasons"], [1, 2])
        self.assertEqual(groups[0]["count"], 2)


if __name__ == "__main__":
    unittest.main()
