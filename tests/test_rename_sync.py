"""看过的列表改名 → 媒体库/历史记录/剧集墙 跟随。

历史记录的标题读 ``media_files``（``title_cn`` > ``title_guess``），而改名只写
``watched_list``；两张表没有外键，不主动传播就会出现「看过的列表是新名字、
历史记录还是老名字」。这组用例锁住传播口径与四条不变量：

* 只改**同一季**的文件（第 1 季 / 第 2 季常常共用同一个中文名）；
* 无季标记（NULL）与「第 1 季」视为同一季；
* 发布组 / 压制参数串（``... KKTV AAC2 0 -BlackTV``）不能当中文名写库、更不能进词典；
* 写回的行带 ``match_by='user'``，后续刮削（含强制重刮）不得覆盖。
"""
from __future__ import annotations

import unittest

from app import db, metadata
from app.api import Api
from tests.base import IsolatedDbTestCase


class RenameSyncTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()
        metadata.reset_online(force=True)

    def _watched(self, title: str, *, title_cn=None, season_no=None,
                 match_by="none") -> dict:
        item_id = db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, season_no, match_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, title, title_cn, season_no, match_by),
        )
        return db.row("SELECT * FROM watched_list WHERE id = ?", (item_id,))

    def _media(self, name: str, *, season=2, title_guess=None, title_cn=None,
               episode=1) -> dict:
        return self.media(
            f"{name}.S{season:02d}E{episode:02d}.mkv",
            title_guess=title_guess or name,
            season_no=season,
            episode_no=episode,
            title_cn=title_cn,
        )

    def _session(self, file_id: int, started: str = "2026-11-16 20:00:00") -> int:
        return db.execute(
            "INSERT INTO watch_sessions(file_id, started_at) VALUES (?, ?)",
            (file_id, started),
        )

    # ---------- 主链路 ----------

    def test_rename_propagates_to_same_season_files(self):
        """改名后媒体库同季的行全部换成新名，历史记录立刻跟着变。"""
        for ep in range(1, 4):
            self._media("My.DressUp.Darling", episode=ep,
                        title_guess="My DressUp Darling KKTV AAC2 0 -BlackTV")
        item = self._watched("更衣人偶坠入爱河", season_no=2,
                             title_cn="My DressUp Darling KKTV AAC2 0 -BlackTV",
                             match_by="user")
        session = self._session(db.scalar("SELECT id FROM media_files WHERE episode_no = 3"))

        before = [h for h in self.api.get_history(50) if h["id"] == session]
        self.assertEqual(before[0]["display_title"], "My DressUp Darling KKTV AAC2 0 -BlackTV")

        result = self.api.update_watched(item["id"], "更衣人偶坠入爱河 第二季",
                                         "更衣人偶坠入爱河 第二季")
        self.assertTrue(result["ok"])
        self.assertEqual(result["media_updated"], 3)

        rows = db.rows("SELECT title_cn, match_by FROM media_files ORDER BY episode_no")
        self.assertEqual([r["title_cn"] for r in rows], ["更衣人偶坠入爱河 第二季"] * 3)
        self.assertEqual({r["match_by"] for r in rows}, {"user"})

        after = [h for h in self.api.get_history(50) if h["id"] == session]
        self.assertEqual(after[0]["display_title"], "更衣人偶坠入爱河 第二季")

    def test_other_season_is_never_touched(self):
        """第 1 季与第 2 季同名时，改第 2 季不能把第 1 季一起改掉。"""
        self.media("Season1 - 01.mkv", season_no=None, episode_no=1,
                   title_guess="Sono Bisque Doll", title_cn="更衣人偶坠入爱河")
        self._media("Sono.Bisque.Doll", season=2, episode=1,
                    title_guess="Sono Bisque Doll", title_cn="更衣人偶坠入爱河")
        item = self._watched("更衣人偶坠入爱河", season_no=2,
                             title_cn="My DressUp Darling KKTV", match_by="user")
        # 让 watched 行能定位到第 2 季：用媒体行的 display_title 精确匹配
        db.execute("UPDATE watched_list SET title_cn = ? WHERE id = ?",
                   ("更衣人偶坠入爱河", item["id"]))

        result = self.api.update_watched(item["id"], "更衣人偶坠入爱河 第二季",
                                         "更衣人偶坠入爱河 第二季")
        self.assertTrue(result["ok"])
        self.assertEqual(result["media_updated"], 1)
        first = db.row("SELECT title_cn FROM media_files WHERE season_no IS NULL")
        self.assertEqual(first["title_cn"], "更衣人偶坠入爱河")
        second = db.row("SELECT title_cn FROM media_files WHERE season_no = 2")
        self.assertEqual(second["title_cn"], "更衣人偶坠入爱河 第二季")

    def test_seasonless_row_matches_season_one_files(self):
        """watched 行没写季号时按「第 1 季」对齐，不会去改第 2 季。"""
        self.media("Ep01.mkv", season_no=None, episode_no=1,
                   title_guess="Sono Bisque Doll", title_cn="更衣人偶坠入爱河")
        self._media("Ep01.s2.mkv", season=2, episode=1,
                    title_guess="Sono Bisque Doll", title_cn="更衣人偶坠入爱河")
        item = self._watched("更衣人偶坠入爱河", season_no=None,
                             title_cn="更衣人偶坠入爱河", match_by="builtin")

        result = self.api.update_watched(item["id"], "更衣人偶坠入爱河 第一季",
                                         "更衣人偶坠入爱河 第一季")
        self.assertTrue(result["ok"])
        self.assertEqual(result["media_updated"], 1)
        self.assertEqual(
            db.row("SELECT title_cn FROM media_files WHERE season_no IS NULL")["title_cn"],
            "更衣人偶坠入爱河 第一季")
        self.assertEqual(
            db.row("SELECT title_cn FROM media_files WHERE season_no = 2")["title_cn"],
            "更衣人偶坠入爱河")

    # ---------- 脏名防护 ----------

    def test_release_junk_is_not_stored_as_chinese_title(self):
        """发布组串既不能写进 title_cn，也不能被词典记住。"""
        item = self._watched("更衣人偶坠入爱河", season_no=2,
                             title_cn="My DressUp Darling KKTV AAC2 0 -BlackTV",
                             match_by="user")
        self.api.update_watched(item["id"], "更衣人偶坠入爱河 第二季", None)
        row = db.row("SELECT title_cn, match_by FROM watched_list WHERE id = ?",
                     (item["id"],))
        self.assertIsNone(row["title_cn"])
        self.assertEqual(row["match_by"], "none")
        self.assertIsNone(db.row(
            "SELECT * FROM name_alias WHERE raw_title = ? AND source = 'user'",
            ("更衣人偶坠入爱河 第二季",)))

    def test_clean_chinese_title_still_learned(self):
        """正常的用户改名照旧进词典，别把脏名防护做成「一律不记」。"""
        item = self._watched("Sono Bisque Doll S2", season_no=2)
        result = self.api.update_watched(item["id"], "Sono Bisque Doll S2",
                                         "更衣人偶坠入爱河 第二季")
        self.assertTrue(result["ok"])
        self.assertTrue(result["learned"])
        alias = db.row("SELECT * FROM name_alias WHERE raw_title = ?",
                       ("Sono Bisque Doll S2",))
        self.assertEqual(alias["title_cn"], "更衣人偶坠入爱河 第二季")

    # ---------- 边界 ----------

    def test_rename_without_local_files_still_succeeds(self):
        """媒体库没有对应文件时只改 watched_list，不能报错。"""
        item = self._watched("只有清单没有文件", season_no=1)
        result = self.api.update_watched(item["id"], "新名字", "新中文名")
        self.assertTrue(result["ok"])
        self.assertEqual(result["media_updated"], 0)
        self.assertEqual(db.row("SELECT title FROM watched_list WHERE id = ?",
                                (item["id"],))["title"], "新名字")

    def test_no_change_keeps_the_existing_chinese_title(self):
        """原样保存不能把中文名冲掉；重复确认一律按「用户手改」记住。"""
        item = self._watched("Frieren", title_cn="葬送的芙莉莲", match_by="builtin")
        result = self.api.update_watched(item["id"], "Frieren", "葬送的芙莉莲")
        self.assertTrue(result["ok"])
        row = db.row("SELECT title_cn, match_by FROM watched_list WHERE id = ?",
                     (item["id"],))
        self.assertEqual(row["title_cn"], "葬送的芙莉莲")
        self.assertEqual(row["match_by"], "user")

    def test_scrape_does_not_overwrite_user_rename(self):
        """写回的行带 match_by='user'：强制重刮也不能覆盖。"""
        for ep in range(1, 3):
            self._media("Show", episode=ep, title_guess="Show KKTV WEB-DL")
        item = self._watched("剧", season_no=2, title_cn="Show KKTV WEB-DL",
                             match_by="user")
        self.api.update_watched(item["id"], "我的名字", "我的名字")
        changed = metadata.apply_to_media(
            {"Show KKTV WEB-DL": {"matched": True, "title_cn": "被覆盖了", "by": "bangumi"}},
            force=True,
        )
        self.assertEqual(changed, 0)
        self.assertEqual(
            {r["title_cn"] for r in db.rows("SELECT title_cn FROM media_files")},
            {"我的名字"})

    def test_chinese_title_equal_to_name_is_still_protected(self):
        """中文名与作品名相同时也要标 user —— 否则自动补名会把用户改名冲掉。

        ``apply_to_watched`` 的自动条件包含 ``title_cn = title``，正是这种行；
        不改标记的话，用户辛苦改的名字活不过一次「外文补中文」。
        """
        item = self._watched("我的名字", season_no=1)
        self.api.update_watched(item["id"], "我的名字", "我的名字")
        row = db.row("SELECT match_by FROM watched_list WHERE id = ?", (item["id"],))
        self.assertEqual(row["match_by"], "user")
        changed = metadata.apply_to_watched(
            {"我的名字": {"matched": True, "title_cn": "别的名字", "by": "bangumi"}},
            force=True,
        )
        self.assertEqual(changed, 0)
        self.assertEqual(
            db.row("SELECT title_cn FROM watched_list WHERE id = ?",
                   (item["id"],))["title_cn"],
            "我的名字")


if __name__ == "__main__":
    unittest.main()
