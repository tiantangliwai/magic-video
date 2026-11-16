"""「看过的动漫列表」解析、中文名即时匹配与入库测试。"""
from __future__ import annotations

import unittest

from app import db, importer, metadata
from tests.base import IsolatedDbTestCase


class ParseLineTests(unittest.TestCase):
    def test_plain_title(self):
        r = importer.parse_line("进击的巨人")
        self.assertEqual(r["title"], "进击的巨人")
        self.assertIsNone(r["season_no"])
        self.assertIsNone(r["episode_from"])

    def test_comments_and_blank_lines_are_skipped(self):
        for line in ("", "   ", "# 注释", "// 注释"):
            self.assertIsNone(importer.parse_line(line))

    def test_season_and_episode_range(self):
        r = importer.parse_line("更衣人偶坠入爱河 S2 01-12")
        self.assertEqual(r["title"], "更衣人偶坠入爱河")
        self.assertEqual((r["season_no"], r["episode_from"], r["episode_to"]), (2, 1, 12))

    def test_chinese_season_and_episode(self):
        r = importer.parse_line("Attack on Titan 第1季 1-25集")
        self.assertEqual(r["title"], "Attack on Titan")
        self.assertEqual((r["season_no"], r["episode_from"], r["episode_to"]), (1, 1, 25))

    def test_chinese_course_marker(self):
        """「第2期」与「第2季」应等价，不能把期数留在标题里。"""
        r = importer.parse_line("葬送のフリーレン 第2期")
        self.assertEqual(r["season_no"], 2)
        self.assertNotIn("第2期", r["title"])

    def test_csv_three_two_one_numbers(self):
        a = importer.parse_line("葬送的芙莉莲,1,1,28")
        self.assertEqual((a["season_no"], a["episode_from"], a["episode_to"]), (1, 1, 28))
        b = importer.parse_line("Frieren,1,28")
        self.assertEqual((b["episode_from"], b["episode_to"]), (1, 28))
        c = importer.parse_line("SPY x FAMILY,2")
        self.assertEqual(c["season_no"], 2)

    def test_tab_separated(self):
        r = importer.parse_line("Bocchi the Rock\t1\t1\t12")
        self.assertEqual((r["season_no"], r["episode_from"], r["episode_to"]), (1, 1, 12))

    def test_year_is_not_an_episode(self):
        r = importer.parse_line("Totally Unknown Show XYZ 2099")
        self.assertEqual(r["title"], "Totally Unknown Show XYZ")
        self.assertIsNone(r["episode_from"])

    def test_parenthesized_year_removed(self):
        r = importer.parse_line("进击的巨人 (2013) 1-25")
        self.assertEqual(r["title"], "进击的巨人")
        self.assertEqual((r["episode_from"], r["episode_to"]), (1, 25))

    def test_reversed_range_is_normalized(self):
        r = importer.parse_line("某作品 12-01")
        self.assertEqual((r["episode_from"], r["episode_to"]), (1, 12))

    def test_single_episode(self):
        r = importer.parse_line("孤独摇滚 第08话")
        self.assertEqual(r["title"], "孤独摇滚")
        self.assertEqual((r["episode_from"], r["episode_to"]), (8, 8))

    def test_parse_text_deduplicates(self):
        rows = importer.parse_text("进击的巨人\n进击的巨人\n# 注释\n孤独摇滚")
        self.assertEqual(len(rows), 2)

    def test_normalize_title(self):
        self.assertEqual(importer.normalize_title("  A  B （2020） "), "A B")


class ImportFlowTests(IsolatedDbTestCase):
    def test_preview_matches_chinese_immediately(self):
        result = importer.preview("Attack on Titan\nFrieren\n进击的巨人\n")
        self.assertEqual(result["total"], 3)
        by_title = {r["title"]: r for r in result["rows"]}
        self.assertEqual(by_title["Attack on Titan"]["title_cn"], "进击的巨人")
        self.assertEqual(by_title["Frieren"]["title_cn"], "葬送的芙莉莲")
        self.assertEqual(by_title["进击的巨人"]["match_by"], "native")
        self.assertFalse(result["match"]["by_online"])

    def test_preview_season_two_uses_season_title(self):
        result = importer.preview("Sono Bisque Doll wa Koi wo Suru S2 01-12")
        row = result["rows"][0]
        self.assertEqual(row["season_no"], 2)
        self.assertIn("第二季", row["title_cn"])

    def test_preview_flags_unmatched_foreign_title(self):
        result = importer.preview("Totally Unknown Show XYZ 2099")
        self.assertEqual(result["unmatched_cn"], 1)
        self.assertEqual(result["rows"][0]["match_by"], "none")

    def test_commit_deduplicates_and_counts(self):
        preview = importer.preview("进击的巨人\n孤独摇滚！")
        first = importer.commit(preview["rows"], source="test")
        self.assertEqual(first["inserted"], 2)
        second = importer.commit(preview["rows"], source="test")
        self.assertEqual((second["inserted"], second["skipped"]), (0, 2))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 2)

    def test_user_edited_chinese_name_is_remembered(self):
        rows = importer.preview("Mystery Foreign Title")["rows"]
        rows[0]["title_cn"] = "神秘外语作品"
        rows[0]["cn_edited"] = True
        result = importer.commit(rows, source="test")
        self.assertEqual(result["learned"], 1)
        hit = metadata.lookup_local("Mystery Foreign Title")
        self.assertEqual(hit["title_cn"], "神秘外语作品")
        # 重新预览时应直接命中，不再需要用户再改一次
        again = importer.preview("Mystery Foreign Title")
        self.assertEqual(again["rows"][0]["title_cn"], "神秘外语作品")

    def test_commit_keeps_raw_text_and_source(self):
        rows = importer.preview("Attack on Titan S2 01-12")["rows"]
        importer.commit(rows, source="unit-test")
        saved = db.row("SELECT * FROM watched_list")
        self.assertEqual(saved["source"], "unit-test")
        self.assertIn("Attack on Titan", saved["raw_text"])
        self.assertEqual(saved["season_no"], 2)


class PendingReviewTests(IsolatedDbTestCase):
    """P1-6：未匹配行进入待确认区，不静默丢行，也不冒充正常记录。"""

    def test_preview_lists_unmatched_rows_as_pending(self):
        result = importer.preview("Attack on Titan\nTotally Unknown Show XYZ")
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["unmatched_cn"], result["pending_count"])
        item = result["pending"][0]
        self.assertEqual(item["title"], "Totally Unknown Show XYZ")
        # index 指回 rows 里的同一条，前端才能按行勾选「暂不导入」
        self.assertEqual(result["rows"][item["index"]]["title"], item["title"])

    def test_matched_rows_are_watched_and_unmatched_are_pending(self):
        rows = importer.preview("Attack on Titan\nTotally Unknown Show XYZ")["rows"]
        out = importer.commit(rows, source="test")
        self.assertEqual(out["inserted"], 2)
        self.assertEqual(out["pending"], 1)
        statuses = {r["title"]: r["status"]
                    for r in db.rows("SELECT title, status FROM watched_list")}
        self.assertEqual(statuses["Attack on Titan"], "watched")
        self.assertEqual(statuses["Totally Unknown Show XYZ"], "pending_review")

    def test_skip_unmatched_drops_them_only_when_asked(self):
        rows = importer.preview("Attack on Titan\nTotally Unknown Show XYZ")["rows"]
        out = importer.commit(rows, source="test", skip_unmatched=True)
        self.assertEqual(out["inserted"], 1)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(out["pending"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_reimport_is_idempotent_including_pending_rows(self):
        rows = importer.preview("Totally Unknown Show XYZ")["rows"]
        importer.commit(rows, source="test")
        again = importer.commit(rows, source="test")
        self.assertEqual((again["inserted"], again["skipped"]), (0, 1))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_naming_a_pending_row_turns_it_into_a_normal_record(self):
        rows = importer.preview("Totally Unknown Show XYZ")["rows"]
        rows[0]["title_cn"] = "神秘作品"
        rows[0]["cn_edited"] = True
        out = importer.commit(rows, source="test")
        self.assertEqual(out["pending"], 0)
        self.assertEqual(out["learned"], 1)
        self.assertEqual(db.scalar("SELECT status FROM watched_list"), "watched")


if __name__ == "__main__":
    unittest.main()
