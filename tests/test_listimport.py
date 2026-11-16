"""外部清单导入（P1-2）：MAL XML / Bangumi Takeout JSON 解析、差异报告、合并规则。"""
from __future__ import annotations

import json
import unittest

from app import db, importer, listimport
from tests.base import IsolatedDbTestCase

MAL_XML = """<?xml version="1.0" encoding="UTF-8"?>
<myanimelist>
  <myinfo><user_name>someone</user_name></myinfo>
  <anime>
    <series_animedb_id>21</series_animedb_id>
    <series_title>One Piece</series_title>
    <series_type>TV</series_type>
    <series_episodes>1000</series_episodes>
    <my_watched_episodes>500</my_watched_episodes>
    <my_score>9</my_score>
    <my_status>Watching</my_status>
  </anime>
  <anime>
    <series_animedb_id>52991</series_animedb_id>
    <series_title>Sousou no Frieren</series_title>
    <series_episodes>28</series_episodes>
    <my_watched_episodes>0</my_watched_episodes>
    <my_status>Plan to Watch</my_status>
  </anime>
</myanimelist>
"""

BGM_JSON = json.dumps([
    {"subject": {"id": 12, "name": "Sousou no Frieren", "name_cn": "葬送的芙莉莲", "eps": 28},
     "status": 3, "ep_status": 28, "rating": 9},
    {"subject": {"id": 253, "name": "Cowboy Bebop", "name_cn": "星际牛仔", "eps": 26},
     "status": 2, "ep_status": 5},
], ensure_ascii=False)


class DetectFormatTests(IsolatedDbTestCase):
    def test_detects_by_extension_and_content(self):
        self.assertEqual(listimport.detect_format("animelist.xml", ""), "mal_xml")
        self.assertEqual(listimport.detect_format("", MAL_XML), "mal_xml")
        self.assertEqual(listimport.detect_format("takeout.json", ""), "bangumi_json")
        self.assertEqual(listimport.detect_format("", BGM_JSON), "bangumi_json")
        self.assertEqual(listimport.detect_format("list.txt", "进击的巨人"), "text")


class MalXmlTests(IsolatedDbTestCase):
    def test_parses_titles_and_watched_episodes(self):
        parsed = listimport.parse_mal_xml(MAL_XML)
        self.assertTrue(parsed["ok"])
        self.assertEqual(len(parsed["records"]), 2)
        one_piece = parsed["records"][0]
        self.assertEqual(one_piece["title"], "One Piece")
        self.assertEqual((one_piece["episode_from"], one_piece["episode_to"]), (1, 500))
        self.assertEqual(one_piece["ext_total"], 1000)
        self.assertEqual(one_piece["ext_status"], "在看")
        self.assertEqual(one_piece["fmt"], "mal_xml")

    def test_unwatched_entry_has_no_episode_range(self):
        parsed = listimport.parse_mal_xml(MAL_XML)
        frieren = parsed["records"][1]
        self.assertIsNone(frieren["episode_from"])
        self.assertIsNone(frieren["episode_to"])
        self.assertIn("想看", frieren["note"])

    def test_invalid_xml_returns_error_not_exception(self):
        parsed = listimport.parse_mal_xml("<myanimelist><anime>")
        self.assertFalse(parsed["ok"])
        self.assertTrue(parsed["warnings"])

    def test_xml_without_anime_nodes_is_rejected(self):
        parsed = listimport.parse_mal_xml("<root><other/></root>")
        self.assertFalse(parsed["ok"])


class BangumiJsonTests(IsolatedDbTestCase):
    def test_parses_array_with_name_cn_and_progress(self):
        parsed = listimport.parse_bangumi_json(BGM_JSON)
        self.assertTrue(parsed["ok"])
        first = parsed["records"][0]
        self.assertEqual(first["title"], "Sousou no Frieren")
        self.assertEqual(first["title_cn"], "葬送的芙莉莲")
        self.assertEqual((first["episode_from"], first["episode_to"]), (1, 28))
        self.assertEqual(first["ext_status"], "看过")
        self.assertEqual(first["ext_score"], 9)

    def test_parses_jsonl(self):
        lines = "\n".join([
            json.dumps({"subject": {"id": 1, "name": "Show A", "name_cn": "作品A", "eps": 12},
                        "status": 2, "ep_status": 3}, ensure_ascii=False),
            json.dumps({"subject": {"id": 2, "name": "Show B", "eps": 24},
                        "status": 2, "ep_status": 10}, ensure_ascii=False),
        ])
        parsed = listimport.parse_bangumi_json(lines)
        self.assertTrue(parsed["ok"])
        self.assertEqual([r["title"] for r in parsed["records"]], ["Show A", "Show B"])

    def test_invalid_json_returns_error(self):
        parsed = listimport.parse_bangumi_json("not json at all")
        self.assertFalse(parsed["ok"])

    def test_parse_any_delegates_plain_text_unchanged(self):
        parsed = listimport.parse_any("list.txt", "进击的巨人\nFrieren")
        self.assertEqual(parsed["format"], "text")
        self.assertEqual(len(parsed["records"]), 2)


class DiffTests(IsolatedDbTestCase):
    def _watch(self, title: str, ep_to: int, *, title_cn: str = "", match_by: str = "builtin") -> None:
        db.execute(
            "INSERT INTO watched_list(title, title_cn, match_by, episode_from, episode_to) "
            "VALUES (?, ?, ?, 1, ?)",
            (title, title_cn or None, match_by, ep_to),
        )

    def test_reports_progress_gap_and_only_imported(self):
        self._watch("Cowboy Bebop", 3)
        records = listimport.parse_bangumi_json(BGM_JSON)["records"]
        d = listimport.diff(records)
        self.assertEqual(d["summary"]["incoming"], 2)
        self.assertEqual(d["summary"]["only_imported"], 1, "芙莉莲本地没有记录")
        self.assertEqual(d["summary"]["progress_gap"], 1, "星际牛仔本地 3 / 清单 5")
        self.assertEqual(d["progress_gap"][0]["direction"], "import_ahead")

    def test_local_ahead_is_marked_not_downgraded(self):
        self._watch("Cowboy Bebop", 20)
        records = listimport.parse_bangumi_json(BGM_JSON)["records"]
        d = listimport.diff(records)
        gap = [g for g in d["progress_gap"] if g["title"] == "Cowboy Bebop"][0]
        self.assertEqual(gap["direction"], "local_ahead")

    def test_title_conflict_keeps_local(self):
        self._watch("Cowboy Bebop", 5, title_cn="本地译名", match_by="user")
        records = listimport.parse_bangumi_json(BGM_JSON)["records"]
        d = listimport.diff(records)
        self.assertEqual(d["summary"]["title_conflict"], 1)
        conflict = d["title_conflict"][0]
        self.assertEqual(conflict["kept"], "本地译名")
        self.assertTrue(conflict["user_edited"])

    def test_file_alignment_requires_title_verification(self):
        """短英文关键词会命中无关文件，必须靠标题校验挡掉。"""
        self.media("Zero no Tsukaima - 01.mkv", title_guess="Zero no Tsukaima")
        self.media("Zero Dark Thirty.mkv", title_guess="Zero Dark Thirty")
        records = [importer._make_record("Zero no Tsukaima", "Zero no Tsukaima", None, 1, 12)]
        d = listimport.diff(records)
        ready = d["file_ready"][0]
        self.assertEqual(ready["files"], 1, "只应命中真正同一部作品的文件")
        self.assertEqual(ready["samples"][0]["filename"], "Zero no Tsukaima - 01.mkv")


class MergeTests(IsolatedDbTestCase):
    def _watch(self, title: str, ep_to: int, *, title_cn: str = "", match_by: str = "builtin") -> int:
        return db.execute(
            "INSERT INTO watched_list(title, title_cn, match_by, episode_from, episode_to) "
            "VALUES (?, ?, ?, 1, ?)",
            (title, title_cn or None, match_by, ep_to),
        )

    def _records(self, title: str, ep_to: int, *, title_cn: str = "") -> list[dict]:
        return [listimport._record(title, title_cn=title_cn, ep_from=1, ep_to=ep_to)]

    def test_raises_progress_when_import_is_ahead(self):
        row_id = self._watch("Cowboy Bebop", 3)
        out = listimport.commit(self._records("Cowboy Bebop", 12))
        self.assertEqual(out["updated"], 1)
        self.assertEqual(db.scalar("SELECT episode_to FROM watched_list WHERE id = ?", (row_id,)), 12)

    def test_never_lowers_local_progress(self):
        row_id = self._watch("Cowboy Bebop", 20)
        out = listimport.commit(self._records("Cowboy Bebop", 5))
        self.assertEqual(out["updated"], 0)
        self.assertEqual(out["skipped"], 1)
        self.assertEqual(db.scalar("SELECT episode_to FROM watched_list WHERE id = ?", (row_id,)), 20)

    def test_inserts_records_that_do_not_exist_locally(self):
        out = listimport.commit(self._records("Brand New Show", 6))
        self.assertEqual(out["inserted"], 1)
        self.assertEqual(db.scalar("SELECT episode_to FROM watched_list"), 6)

    def test_user_edited_title_cn_is_never_overwritten(self):
        row_id = self._watch("Cowboy Bebop", 5, title_cn="我改的名字", match_by="user")
        listimport.commit(self._records("Cowboy Bebop", 12, title_cn="清单译名"))
        self.assertEqual(
            db.scalar("SELECT title_cn FROM watched_list WHERE id = ?", (row_id,)), "我改的名字")

    def test_empty_local_title_cn_gets_filled(self):
        row_id = self._watch("Cowboy Bebop", 5)
        listimport.commit(self._records("Cowboy Bebop", 5, title_cn="星际牛仔"))
        self.assertEqual(
            db.scalar("SELECT title_cn FROM watched_list WHERE id = ?", (row_id,)), "星际牛仔")

    def test_merge_is_idempotent(self):
        self._watch("Cowboy Bebop", 5)
        records = self._records("Cowboy Bebop", 12)
        first = listimport.commit(records)
        second = listimport.commit(records)
        self.assertEqual(first["updated"], 1)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["skipped"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_preview_file_returns_diff_and_pending(self):
        result = listimport.preview_file("takeout.json", BGM_JSON)
        self.assertTrue(result["ok"])
        self.assertEqual(result["format"], "bangumi_json")
        self.assertEqual(result["format_label"], "Bangumi Takeout JSON")
        self.assertIn("diff", result)
        self.assertIn("pending", result)

    def test_preview_file_rejects_garbage(self):
        result = listimport.preview_file("x.json", "{{{")
        self.assertFalse(result["ok"])
        self.assertTrue(result["error"])


if __name__ == "__main__":
    unittest.main()
