"""封面积累导出器的纯逻辑契约（不碰真库、不写 vault）。

需求（2026-11-19）：把「看过的列表 / 未看过的列表」与全部封面积累成 Obsidian
md 节点，再额外打一个带时间戳的 tar.gz 快照；md 后续可继续手写积累。
导出器在 scripts/ 下，测试只验证可复用的纯函数，文件写入由 --dry-run 之外的
人工执行负责。
"""
from __future__ import annotations

import unittest

from app import snapshot as exporter


class SnapshotDataTests(unittest.TestCase):
    def test_group_watched_merges_seasons_and_episodes(self):
        rows = [
            {"id": 1, "title": "某番", "title_cn": "某番中文", "season_no": 1,
             "episode_from": 1, "episode_to": 12, "note": "", "source": "manual"},
            {"id": 2, "title": "某番", "title_cn": "某番中文", "season_no": 2,
             "episode_from": 1, "episode_to": 11, "note": "好看", "source": "user"},
            {"id": 3, "title": "另一部", "title_cn": "", "season_no": None,
             "episode_from": None, "episode_to": None, "note": "", "source": "manual"},
        ]
        groups = exporter.group_watched(rows)
        self.assertEqual([g["title"] for g in groups], ["某番", "另一部"])
        first = groups[0]
        self.assertEqual(first["ids"], [1, 2])
        self.assertEqual(first["seasons"], ["1", "2"])
        self.assertEqual(first["episodes"], ["1–12", "1–11"])
        self.assertIn("好看", first["notes"])
        self.assertEqual(first["count"], 2)

    def test_display_name_prefers_title_like_watched_page(self):
        self.assertEqual(exporter.display_name({"title": "A", "title_cn": "B"}), "A")
        self.assertEqual(exporter.display_name({"title": "", "title_cn": "B"}), "B")
        self.assertEqual(exporter.display_name({}), "(未命名)")

    def test_cover_for_tries_cn_and_original_titles(self):
        covers = {"中文名": "abc.jpg"}
        self.assertEqual(exporter.cover_for(["原名", "中文名"], covers), "abc.jpg")
        self.assertEqual(exporter.cover_for(["无名"], covers), "")

    def test_rating_for_matches_watched_and_series_keys(self):
        meta = {"watched::作品A": {"rating": 8, "tags": "神作"},
                "series::guess::作品B": {"rating": 9, "tags": "追完"}}
        self.assertEqual(exporter.rating_for(["作品A"], meta)["rating"], 8)
        self.assertEqual(exporter.rating_for(["作品B"], meta)["tags"], "追完")
        self.assertEqual(exporter.rating_for(["无名"], meta)["rating"], None)

    def test_unseen_cards_exclude_readonly_synthetic_cards(self):
        cards = [
            {"title": "本地未看", "watch_state": "none", "readonly": False, "file_count": 2},
            {"title": "只看过记录", "watch_state": "listed", "readonly": True, "file_count": 0},
            {"title": "本地看过", "watch_state": "partial", "readonly": False, "file_count": 3},
        ]
        self.assertEqual([c["title"] for c in exporter.unseen_cards(cards)], ["本地未看"])


class SnapshotMarkdownTests(unittest.TestCase):
    def test_list_note_has_yaml_managed_marker_and_image_links(self):
        md = exporter.render_list_note("看过清单", "magic-video", [
            {"title": "作品A", "cover": "abcd.jpg", "seasons": ["1"],
             "episodes": [], "file_count": 2, "rating": 8, "tags": "神作", "note": ""},
        ], "2026-11-19 23:55:00", 295)
        self.assertTrue(md.startswith("---\ntitle: 看过清单"))
        self.assertIn("managed: true", md)
        self.assertIn(exporter.MANAGED, md)
        self.assertIn('<img src="封面图/abcd.jpg"', md)
        self.assertIn("★ 8", md)
        self.assertIn("本地 2 集", md)
        self.assertIn("图库总数：**295**", md)

    def test_merge_note_keeps_handwritten_annotations(self):
        generated = exporter.render_list_note("看过清单", "magic-video", [
            {"title": "作品A", "cover": "", "note": ""}], "now", 1)
        existing = generated.replace(
            exporter.AUTO_END + "\n", exporter.AUTO_END + "\n\n## 我的批注\n别动我\n")
        merged = exporter.merge_note(existing, generated.replace("作品A", "作品B"))
        self.assertIn("## 我的批注\n别动我", merged)
        self.assertIn("作品B", merged)
        self.assertNotIn("| **作品A** |", merged)

    def test_append_snapshot_keeps_history(self):
        first = exporter.append_snapshot("", "旧节点", "2026-11-18 10:00:00")
        second = exporter.append_snapshot(first, "新节点", "2026-11-19 10:00:00")
        self.assertIn("旧节点", second)
        self.assertIn("新节点", second)
        self.assertEqual(second.count(exporter.SNAP_BEGIN), 1)
        self.assertIn("[[看过清单]]", first)
        self.assertIn("[[未看过清单]]", first)

    def test_is_managed(self):
        self.assertTrue(exporter.is_managed("前言\n" + exporter.MANAGED + "\n正文"))
        self.assertFalse(exporter.is_managed("# 我自己的笔记"))


if __name__ == "__main__":
    unittest.main()
