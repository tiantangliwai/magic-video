"""媒体库树状视图：作品 → 季 → 集 / 目录树 的行为测试。

所有用例都通过 js_api 的公开接口验证，不直接断言内部 SQL 实现。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import db, potplayer
from app.scanner import parse_filename
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class MediaTreeTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def _dir(self, name: str = "Anime") -> int:
        path = str(self.tmp / name)
        db.execute("INSERT INTO directories(path) VALUES (?)", (path,))
        return int(db.scalar("SELECT id FROM directories WHERE path = ?", (path,)))

    def _file(self, name: str, *, directory: str = "Anime", sub: str = "",
              **extra) -> dict:
        values = {"path": str(self.tmp / directory / sub / name), **extra}
        return self.media(name, **values)

    def _seed_series(self):
        dir_id = self._dir()
        return [
            self._file("Frieren - 01.mkv", sub="Frieren/Season 1",
                       title_guess="Frieren", title_cn="葬送的芙莉莲",
                       season_no=1, episode_no=1, dir_id=dir_id),
            self._file("Frieren - 02.mkv", sub="Frieren/Season 1",
                       title_guess="Frieren", title_cn="葬送的芙莉莲",
                       season_no=1, episode_no=2, dir_id=dir_id),
            # 季数只写在目录名里，扫描时 season_no 为空，树里也必须归到「第 2 季」
            self._file("Frieren - 03.mkv", sub="Frieren/Season 2",
                       title_guess="Frieren", title_cn="葬送的芙莉莲",
                       episode_no=3, dir_id=dir_id),
            # 剧场版 / 未分集文件直接挂在作品组下，不造「未标注季」，也不能丢
            self._file("Frieren Movie.mkv", sub="Frieren",
                       title_guess="Frieren", title_cn="葬送的芙莉莲", dir_id=dir_id),
            self._file("Attack on Titan - 01.mkv", sub="AOT",
                       title_guess="Attack on Titan", episode_no=1, dir_id=dir_id),
        ]

    def _group(self, tree, label):
        return next(g for g in tree["groups"] if g["label"] == label)

    def test_empty_library_returns_empty_tree(self):
        tree = self.api.get_media_tree({})
        self.assertEqual(tree["mode"], "series")
        self.assertEqual(tree["total"], 0)
        self.assertEqual(tree["groups"], [])
        self.assertEqual(tree["group_count"], 0)

    def test_series_tree_groups_seasons_and_sorts_episodes(self):
        self._seed_series()
        tree = self.api.get_media_tree({})
        self.assertEqual(tree["mode"], "series")
        self.assertEqual(tree["total"], 5)
        self.assertEqual(tree["group_count"], 2)

        frieren = self._group(tree, "葬送的芙莉莲")
        self.assertEqual(frieren["file_count"], 4)
        self.assertIn("Frieren", frieren["sub"])
        self.assertEqual(
            [s["label"] for s in frieren["children"]],
            ["第 1 季", "第 2 季"],
        )
        s1 = frieren["children"][0]
        self.assertEqual(s1["file_count"], 2)
        self.assertEqual([f["episode_no"] for f in s1["files"]], [1, 2])
        # 没有季标记的文件直接挂在作品组上，绝不造「未标注季」这一层
        self.assertEqual([f["filename"] for f in frieren["files"]], ["Frieren Movie.mkv"])
        aot = self._group(tree, "Attack on Titan")
        self.assertEqual(aot["file_count"], 1)
        self.assertEqual(aot["children"], [])
        self.assertEqual([f["filename"] for f in aot["files"]], ["Attack on Titan - 01.mkv"])
        self.assertFalse(any("未标注季" in s["label"]
                             for g in tree["groups"] for s in g["children"]))

    def test_chinese_title_merges_rows_without_cn_and_season_from_path(self):
        dir_id = self._dir()
        self._file("Frieren - 01.mkv", sub="Season 1", title_guess="Frieren",
                   title_cn="葬送的芙莉莲", season_no=1, episode_no=1, dir_id=dir_id)
        self._file("Frieren - 25.mkv", sub="Season 2", title_guess="Frieren",
                   episode_no=25, dir_id=dir_id)
        tree = self.api.get_media_tree({})
        self.assertEqual(tree["group_count"], 1)
        group = tree["groups"][0]
        self.assertEqual(group["label"], "葬送的芙莉莲")
        self.assertEqual(group["file_count"], 2)
        self.assertEqual([s["label"] for s in group["children"]], ["第 1 季", "第 2 季"])

    def test_tree_has_no_hard_limit_and_honors_filters(self):
        dir_id = self._dir()
        rows = []
        for i in range(1, 451):
            name = f"Bleach - {i:03d}.mkv"
            rows.append((str(self.tmp / "Anime" / name), name, name, "Bleach", "new"))
        db.executemany(
            "INSERT INTO media_files(path, filename, title_raw, title_guess, status) "
            "VALUES (?, ?, ?, ?, ?)", rows,
        )
        db.execute("UPDATE media_files SET dir_id = ?, season_no = 1", (dir_id,))
        self._file("Frieren - 01.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                   episode_no=1, dir_id=dir_id)

        everything = self.api.get_media_tree({})
        self.assertEqual(everything["total"], 451)
        bleach = self._group(everything, "Bleach")
        self.assertEqual(bleach["file_count"], 450)
        self.assertEqual(len(bleach["children"][0]["files"]), 450)

        hit = self.api.get_media_tree({"keyword": "芙莉莲"})
        self.assertEqual(hit["total"], 1)
        self.assertEqual(hit["group_count"], 1)
        self.assertEqual(hit["groups"][0]["label"], "葬送的芙莉莲")

        other = self.api.get_media_tree({"directory_id": 99999})
        self.assertEqual(other["total"], 0)
        matched = self.api.get_media_tree({"match_state": "matched"})
        self.assertEqual(matched["total"], 1)

    def test_unsafe_mode_is_ignored_and_leaf_file_opens_player(self):
        video = self.tmp / "Frieren - 01.mkv"
        video.write_bytes(b"")
        self._file("Frieren - 01.mkv", title_guess="Frieren", title_cn="葬送的芙莉莲",
                   episode_no=1, path=str(video))
        tree = self.api.get_media_tree({"mode": "series; DROP TABLE media_files"})
        self.assertEqual(tree["mode"], "series")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 1)
        leaf = tree["groups"][0]["files"][0]
        with mock.patch.object(potplayer, "play", return_value={"ok": True}) as play:
            result = self.api.play_file(leaf["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(play.call_args[0][0], str(video))

    def test_watched_counts_and_next_episode(self):
        rows = self._seed_series()
        f1, f2 = rows[0], rows[1]
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, completed, last_played_at) "
                   "VALUES (?, ?, ?, ?, ?)", (f1["id"], 60000, 25, 0, "2026-11-15 09:00:00"))
        db.execute("INSERT INTO watch_progress(file_id, percent, completed) VALUES (?, ?, ?)",
                   (f2["id"], 100, 1))
        group = self._group(self.api.get_media_tree({}), "葬送的芙莉莲")
        self.assertEqual(group["watched_count"], 1)
        self.assertEqual(group["playing_count"], 1)
        # 优先回到看了一半的那一集，而不是已经看完的集数
        self.assertEqual(group["next_file_id"], f1["id"])

    def test_release_folder_merges_bracketed_episode_filenames(self):
        """字幕组命名的真实场景：文件名每集都不同，靠发布目录聚成一部番。"""
        dir_id = self._dir()
        folder = "[DBD-Raws][家里蹲吸血姬的苦闷][01-12TV全集][1080P][BDRip][HEVC-10bit][FLAC][MKV]"
        for i in (1, 2, 3):
            name = f"[DBD-Raws][家里蹲吸血姬的苦闷][0{i}][1080P][BDRip][HEVC-10bit][FLAC].mkv"
            self.media(name, path=str(self.tmp / "Anime" / folder / name),
                       title_guess=name, title_cn=name, match_by="native", dir_id=dir_id)
        tree = self.api.get_media_tree({})
        self.assertEqual(tree["group_count"], 1)
        group = tree["groups"][0]
        self.assertEqual(group["label"], "家里蹲吸血姬的苦闷")
        self.assertEqual(group["file_count"], 3)
        self.assertEqual([f["tree_episode"] for f in group["files"]], [1, 2, 3])

    def test_chinese_numeral_season_and_single_file_movie_keeps_own_group(self):
        dir_id = self._dir()
        show = "Re：从零开始的异世界生活"
        for season, episode in (("第二季", 1), ("第三季", 2)):
            name = f"Re Zero - {episode:02d}.mkv"
            self.media(name, path=str(self.tmp / "Anime" / show / season / name),
                       title_guess="Re Zero", dir_id=dir_id)
        self.media("Movie.mkv", path=str(self.tmp / "Anime" / "Movie.mkv"),
                   title_guess="Movie", dir_id=dir_id)
        tree = self.api.get_media_tree({})
        self.assertEqual(tree["group_count"], 2)
        show_node = next(g for g in tree["groups"] if g["file_count"] == 2)
        self.assertEqual([s["label"] for s in show_node["children"]], ["第 2 季", "第 3 季"])

    def test_directory_tree_nests_folders_and_keeps_loose_files(self):
        root = self.tmp / "Anime"
        dir_id = self._dir()
        a = self._file("Frieren - 01.mkv", sub="Frieren/Season 1", dir_id=dir_id,
                       title_guess="Frieren")
        b = self._file("Frieren - 02.mkv", sub="Frieren/Season 1", dir_id=dir_id,
                       title_guess="Frieren")
        c = self._file("Movie.mkv", sub="Movies", dir_id=dir_id, title_guess="Movie")
        self.media("Loose.mkv", path=r"Z:\Loose\Loose.mkv", title_guess="Loose")

        tree = self.api.get_media_tree({"mode": "folder"})
        self.assertEqual(tree["mode"], "folder")
        self.assertEqual(tree["total"], 4)

        anime = self._group(tree, str(root))
        self.assertEqual(anime["file_count"], 3)
        frieren = next(n for n in anime["children"] if n["label"] == "Frieren")
        self.assertEqual(frieren["file_count"], 2)
        season1 = frieren["children"][0]
        self.assertEqual(season1["label"], "Season 1")
        self.assertEqual({f["id"] for f in season1["files"]}, {a["id"], b["id"]})
        movies = next(n for n in anime["children"] if n["label"] == "Movies")
        self.assertEqual([f["id"] for f in movies["files"]], [c["id"]])
        # 不在扫描根目录里的文件也要有归属，不能凭空消失
        self.assertEqual(sum(g["file_count"] for g in tree["groups"]), 4)

    def test_anna_e_prefixed_episodes_merge_by_release_folder(self):
        """真实脏目录：8 集 E01–E08 同目录，靠目录归并成一部剧而不是散成 8 组。"""
        dir_id = self._dir()
        folder = ("【高清剧集网 www.BTHDTV.com】安娜[全6集][简繁英字幕]"
                  ".Anna.Directors.Cut.2022.WEB-DL.1080p")
        for i in range(1, 9):
            name = f"Anna.Directors.Cut.E{i:02d}.2022.Amazon.WEB-DL.1080p.H264.mkv"
            self.media(name, path=str(self.tmp / "Anime" / folder / name),
                       title_guess=f"Anna Directors Cut E{i:02d} Amazon", dir_id=dir_id)
        self.assertEqual(parse_filename("Anna.Directors.Cut.E01.2022.mkv")["episode_no"], 1)
        tree = self.api.get_media_tree({})
        groups = [g for g in tree["groups"] if g["label"] == "安娜"]
        self.assertEqual(len(groups), 1)
        group = groups[0]
        self.assertEqual(group["file_count"], 8)
        self.assertEqual([f["tree_episode"] for f in group["files"]], list(range(1, 9)))
        self.assertEqual(group["children"], [])       # 无季标记，直接挂作品组

    def test_size_token_folder_shows_real_chinese_title(self):
        """WWW.17BT.CN@爱的躯壳[…][1.36G] 必须显示片名，不能把体积当作品名。"""
        dir_id = self._dir()
        folder = "WWW.17BT.CN@爱的躯壳[韩语中字幕WAF][1.36G]"
        for i in (1, 2):
            name = f"[爱的躯壳].La.Belle.2000.XviD.AC3.CD{i}-WAF.avi"
            self.media(name, path=str(self.tmp / "Anime" / folder / name),
                       title_guess=f"La Belle XviD AC3 CD{i}-WAF", dir_id=dir_id)
        labels = [g["label"] for g in self.api.get_media_tree({})["groups"]]
        self.assertIn("爱的躯壳", labels)
        self.assertNotIn("1.36G", labels)
        card = next(c for c in self.api.get_poster_wall({})["cards"]
                    if c["title"] == "爱的躯壳")
        self.assertEqual(card["search_keyword"], "爱的躯壳")

    def test_rename_work_persists_and_can_be_reset(self):
        """改名只改展示标签：树 / 剧集墙同步，media_files 与播放记录分毫不动。"""
        dir_id = self._dir()
        self._file("Show - 01.mkv", title_guess="Show", title_cn="原剧名", dir_id=dir_id)
        before = self.api.get_media_tree({})
        gid = before["groups"][0]["id"]
        self.assertEqual(before["groups"][0]["label"], "原剧名")
        self.assertTrue(self.api.rename_work(gid, "我改的名字")["ok"])
        after = self.api.get_media_tree({})
        self.assertEqual(after["groups"][0]["label"], "我改的名字")
        self.assertEqual(after["groups"][0]["id"], gid)
        self.assertEqual(db.scalar("SELECT title_cn FROM media_files"), "原剧名")
        self.assertEqual(self.api.get_poster_wall({})["cards"][0]["title"], "我改的名字")
        self.assertTrue(self.api.rename_work(gid, "", reset=True)["ok"])
        self.assertEqual(self.api.get_media_tree({})["groups"][0]["label"], "原剧名")
        self.assertFalse(self.api.rename_work("not-a-group", "x")["ok"])


class FrontendTreeTests(unittest.TestCase):
    """前端契约：三种视图可切换，树节点可折叠，叶子仍可点击播放。"""

    def setUp(self):
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.css = (ROOT / "web/style.css").read_text(encoding="utf-8")

    def test_view_mode_switch_and_tree_api_are_wired(self):
        for token in ("get_media_tree", 'id="fileViewMode"', "fileViewModeHandler",
                      "view_mode", "tree-toggle", "treeNodeHtml", "fileListRows("):
            self.assertIn(token, self.js)

    def test_tree_styles_exist(self):
        for token in (".tree-node", ".tree-head", ".tree-children", ".tree-leaf"):
            self.assertIn(token, self.css)
