"""看过的列表：存量脏数据清洗、播放记录同步、解析防再犯与前端契约。

全部使用临时 SQLite（IsolatedDbTestCase）+ 禁网；静态契约只读取源码文本，
绝不启动窗口、播放器或守护进程，也不碰真实库。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from app import covers, db, importer
from app.api import Api
from app.watcher import Watcher
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
DIRTY_TITLE = "\u200c \u200c：《零之使魔～双月的骑士～》（ 年， ）。 - 完"
DIRTY_RAW = "\u200c第二季\u200c：《零之使魔～双月的骑士～》（2007 年，12 集）。 - 完"


class ParseRepairTests(unittest.TestCase):
    """新导入不得再产生旧版那种空壳括号与「- 完」垃圾行。"""

    def test_no_context_season_line_keeps_book_title(self):
        rows = importer.parse_text(DIRTY_RAW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "零之使魔～双月的骑士～")
        self.assertEqual(row["season_no"], 2)
        self.assertEqual(row["episode_from"], 12)
        self.assertEqual(row["episode_to"], 12)
        self.assertEqual(row["raw_text"], DIRTY_RAW)

    def test_standalone_completion_markers_are_dropped(self):
        rows = importer.parse_text("S1 - 完\nS2 - 完\nS3 - 完\nS4 - 完")
        self.assertEqual(rows, [])
        self.assertFalse([r for r in rows if r["title"] == "- 完"])

    def test_empty_year_paren_and_trailing_completion_are_stripped(self):
        row = importer.parse_line("《零之使魔～双月的骑士～》（ 年， ）。 - 完")
        self.assertIsNotNone(row)
        self.assertEqual(row["title"], "零之使魔～双月的骑士～")
        for noise in ("《", "（", "年，", "。", "- 完"):
            self.assertNotIn(noise, row["title"])

    def test_halfwidth_comma_in_aside_does_not_truncate_title(self):
        row = importer.parse_line("《某作品》（2007 年, 12 集）")
        self.assertEqual(row["title"], "某作品")
        self.assertIsNone(row["episode_from"])

    def test_legal_markers_are_unchanged(self):
        self.assertEqual(
            importer.parse_line("《某科学的超电磁炮》第一季（24集）：独立日常篇，完善角色设定，建议先看。（完）")["title"],
            "某科学的超电磁炮")
        r = importer.parse_line("更衣人偶坠入爱河 S2 01-12")
        self.assertEqual((r["season_no"], r["episode_from"], r["episode_to"]), (2, 1, 12))
        r = importer.parse_line("进击的巨人 (2013) 1-25")
        self.assertEqual((r["title"], r["episode_from"], r["episode_to"]), ("进击的巨人", 1, 25))
        self.assertIsNone(importer.parse_line("魔王2099")["episode_from"])
        rows = importer.parse_text("怪兽8号\nS1 （完）\nS2 - （完）\n剧场版 - 侦查任务- （完）")
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[3]["note"], "剧场版 - 侦查任务")

    def test_clean_watched_title_preserves_wave_dash(self):
        self.assertEqual(importer.clean_watched_title("零之使魔～双月的骑士～"),
                         "零之使魔～双月的骑士～")
        self.assertEqual(importer.clean_watched_title("进击的巨人"), "进击的巨人")
        self.assertEqual(importer.clean_watched_title("《A》(2013)"), "A")
        self.assertEqual(importer.clean_watched_title("- 完"), "")

    def test_clean_watched_title_handles_colon_by_position(self):
        # 书名号闭合后的冒号 = 观感说明，整段删
        self.assertEqual(
            importer.clean_watched_title("\u200c《某科学的超电磁炮》 \u200c（ ）：独立日常篇，完善角色设定，建议先看。（完）"),
            "某科学的超电磁炮")
        # 书名号内部的冒号 = 作品副标题，必须保留
        self.assertEqual(
            importer.clean_watched_title("\u200c《魔法禁书目录 剧场版：恩底弥翁的奇迹》\u200c（可选）：独立故事，不影响主线。（完）"),
            "魔法禁书目录 剧场版：恩底弥翁的奇迹")
        # 前置说明 + 书名号 + 冒号说明
        self.assertEqual(
            importer.clean_watched_title("\u200cTV动画《兔女郎学姐》\u200c：改编自小说第 卷，是系列的开篇之作 。（完）"),
            "TV动画兔女郎学姐")
        # 冒号在书名号之前：只删行首冒号，标题留在冒号后
        self.assertEqual(importer.clean_watched_title(DIRTY_TITLE), "零之使魔～双月的骑士～")
        for junk in ("（完）", "- 完", "- （完）"):
            self.assertEqual(importer.clean_watched_title(junk), "")
        self.assertEqual(importer.clean_watched_title("After Story （完）"), "After Story")


class CleanupDbTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def _watched(self, title, *, title_cn=None, raw_text=None, match_by="native",
                 season_no=None, episode_from=None, episode_to=None, note=None,
                 source="manual") -> dict:
        item_id = db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, match_by, season_no, "
            "episode_from, episode_to, source, note) VALUES (?,?,?,?,?,?,?,?,?)",
            (raw_text if raw_text is not None else title, title, title_cn, match_by,
             season_no, episode_from, episode_to, source, note),
        )
        return db.row("SELECT * FROM watched_list WHERE id = ?", (item_id,))

    def test_commit_guard_rejects_dirty_title(self):
        rec = importer.parse_line("干净作品")
        rec["title"] = DIRTY_TITLE
        rec["title_cn"] = DIRTY_TITLE
        result = importer.commit([rec], source="test")
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(db.row("SELECT title FROM watched_list")["title"], "零之使魔～双月的骑士～")

    def test_clean_watched_rows_fixes_dirty_rows_and_is_idempotent(self):
        dirty = self._watched(DIRTY_TITLE, title_cn=DIRTY_TITLE, raw_text=DIRTY_RAW,
                              season_no=2, episode_from=12, episode_to=12)
        junk = self._watched("- 完", raw_text="S2 - 完", season_no=2)
        clean = self._watched("进击的巨人")
        first = importer.clean_watched_rows()
        self.assertEqual(first["scanned"], 3)
        self.assertEqual((first["updated"], first["deleted"], first["unchanged"]), (1, 1, 1))
        after = db.row("SELECT * FROM watched_list WHERE id = ?", (dirty["id"],))
        self.assertEqual(after["title"], "零之使魔～双月的骑士～")
        self.assertEqual(after["title_cn"], "零之使魔～双月的骑士～")
        self.assertEqual(after["raw_text"], DIRTY_RAW)
        self.assertEqual((after["season_no"], after["episode_from"], after["episode_to"]), (2, 12, 12))
        self.assertIsNone(db.row("SELECT * FROM watched_list WHERE id = ?", (junk["id"],)))
        self.assertEqual(db.row("SELECT * FROM watched_list WHERE id = ?", (clean["id"],))["title"],
                         "进击的巨人")
        second = importer.clean_watched_rows()
        self.assertEqual((second["updated"], second["deleted"]), (0, 0))

    def test_clean_watched_rows_preserves_user_renamed_rows(self):
        self._watched("《脏标题》（ 年， ）。 - 完", title_cn="用户改的中文名", match_by="user")
        importer.clean_watched_rows()
        row = db.row("SELECT * FROM watched_list")
        self.assertEqual(row["title"], "脏标题")
        self.assertEqual(row["title_cn"], "用户改的中文名")
        self.assertEqual(row["match_by"], "user")

    def test_clean_watched_rows_dry_run_does_not_write(self):
        self._watched(DIRTY_TITLE, title_cn=DIRTY_TITLE)
        self._watched("- 完")
        preview = importer.clean_watched_rows(dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertEqual((preview["updated"], preview["deleted"]), (1, 1))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 2)

    def test_clean_watched_rows_keeps_note_and_source(self):
        row = self._watched(DIRTY_TITLE, title_cn=DIRTY_TITLE, note="剧场版", source="manual")
        importer.clean_watched_rows()
        after = db.row("SELECT * FROM watched_list WHERE id = ?", (row["id"],))
        self.assertEqual(after["note"], "剧场版")
        self.assertEqual(after["source"], "manual")

    def test_api_clean_watched_list_bridge(self):
        self._watched("- 完")
        self.assertEqual(self.api.clean_watched_list(True)["deleted"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)
        self.assertEqual(self.api.clean_watched_list(False)["deleted"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)


class SyncFromProgressTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def _watched(self, title, *, title_cn=None, season_no=None,
                 episode_from=None, episode_to=None, source="manual") -> int:
        return db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, match_by, season_no, "
            "episode_from, episode_to, source) VALUES (?,?,?,?,?,?,?,?)",
            (title, title, title_cn, "native", season_no, episode_from, episode_to, source),
        )

    def _completed(self, *, filename="Frieren - 12.mkv", title_guess="Frieren",
                    title_cn=None, season_no=1, episode_no=12, completed=1):
        media = self.media(filename, title_guess=title_guess, title_cn=title_cn,
                           season_no=season_no, episode_no=episode_no)
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent, completed) "
            "VALUES (?, ?, ?, ?, ?)",
            (media["id"], 1_140_000, 1_200_000, 95.0, completed),
        )
        return media

    def test_completed_progress_creates_playback_row(self):
        self._completed()
        result = importer.sync_watched_from_progress()
        self.assertEqual((result["scanned"], result["added"]), (1, 1))
        row = db.row("SELECT * FROM watched_list")
        self.assertEqual(row["source"], "playback")
        self.assertEqual(row["title"], "Frieren")
        self.assertEqual((row["season_no"], row["episode_from"], row["episode_to"]), (1, 12, 12))

    def test_imported_episode_range_covers_played_episode(self):
        self._watched("Frieren", season_no=1, episode_from=1, episode_to=25)
        self._completed(episode_no=12)
        result = importer.sync_watched_from_progress()
        self.assertEqual(result["added"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_imported_null_range_covers_any_episode(self):
        self._watched("Frieren", season_no=1)
        self._completed(episode_no=7)
        result = importer.sync_watched_from_progress()
        self.assertEqual(result["added"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_chinese_title_matches_imported_row(self):
        self._watched("葬送的芙莉莲", title_cn="葬送的芙莉莲", season_no=1,
                      episode_from=1, episode_to=28)
        self._completed(title_cn="葬送的芙莉莲", episode_no=12)
        self.assertEqual(importer.sync_watched_from_progress()["added"], 0)

    def test_sync_is_idempotent(self):
        self._completed()
        importer.sync_watched_from_progress()
        importer.sync_watched_from_progress()
        third = importer.sync_watched_from_progress()
        self.assertEqual(third["added"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_incomplete_progress_does_not_sync(self):
        self._completed(completed=0)
        result = importer.sync_watched_from_progress()
        self.assertEqual((result["scanned"], result["added"]), (0, 0))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 0)

    def test_manual_rows_are_not_rewritten(self):
        self._watched("Frieren", season_no=2, episode_from=1, episode_to=1)
        self._completed(episode_no=12)
        importer.sync_watched_from_progress()
        manual = db.row("SELECT * FROM watched_list WHERE source = 'manual'")
        self.assertEqual(manual["episode_from"], 1)
        self.assertEqual(manual["season_no"], 2)
        playback = db.row("SELECT * FROM watched_list WHERE source = 'playback'")
        self.assertIsNotNone(playback)
        self.assertEqual(playback["episode_from"], 12)

    def test_api_sync_watched_from_history_bridge(self):
        self._completed()
        result = self.api.sync_watched_from_history()
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 1)
        self.assertEqual(len(self.api.get_watched_list(0)), 1)


class WatcherCloseFallbackTests(IsolatedDbTestCase):
    """关闭 PotPlayer 时窗口已消失，不得把断点丢成 0。"""

    def setUp(self):
        super().setUp()
        self.file = self.media("Show - 01.mkv", title_guess="Show")

    def _open_current(self, max_ms, last_written_ms):
        session_id = db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, from_ms, to_ms, max_ms) "
            "VALUES (?, ?, 0, 0, 0)", (self.file["id"], db.now()),
        )
        db.execute("INSERT OR IGNORE INTO watch_progress(file_id, position_ms) VALUES (?, 0)",
                   (self.file["id"],))
        watcher = Watcher()
        watcher.current = {
            "file_id": self.file["id"], "session_id": session_id,
            "started_at": db.now(), "max_ms": max_ms,
            "last_written_ms": last_written_ms, "last_state": "playing",
        }
        return watcher, session_id

    def test_close_session_falls_back_to_max_position(self):
        watcher, sid = self._open_current(180_000, None)
        watcher._close_session()
        self.assertEqual(
            db.scalar("SELECT position_ms FROM watch_progress WHERE file_id = ?", (self.file["id"],)),
            180_000,
        )
        self.assertEqual(db.scalar("SELECT to_ms FROM watch_sessions WHERE id = ?", (sid,)), 180_000)

    def test_close_session_writes_zero_instead_of_null(self):
        watcher, sid = self._open_current(0, None)
        watcher._close_session()
        self.assertEqual(db.scalar("SELECT to_ms FROM watch_sessions WHERE id = ?", (sid,)), 0)
        self.assertEqual(
            db.scalar("SELECT position_ms FROM watch_progress WHERE file_id = ?", (self.file["id"],)), 0)

    def test_close_session_does_not_lower_existing_position(self):
        watcher, sid = self._open_current(60_000, 300_000)
        db.execute("UPDATE watch_progress SET position_ms = 300_000 WHERE file_id = ?",
                   (self.file["id"],))
        watcher._close_session()
        self.assertEqual(
            db.scalar("SELECT position_ms FROM watch_progress WHERE file_id = ?", (self.file["id"],)),
            300_000,
        )


class FrontendContractTests(unittest.TestCase):
    """静态契约：本轮 11 项界面改动不能被引擎或后续重构悄悄抹掉。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web/style.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "web/index.html").read_text(encoding="utf-8")

    def test_window_title_is_software_name_and_stays_in_sync(self):
        self.assertIn("<title>Magic Video</title>", self.html)
        self.assertNotIn("播放历史与续播", self.html)
        main = (ROOT / "app/main.py").read_text(encoding="utf-8")
        single = (ROOT / "app/single_instance.py").read_text(encoding="utf-8")
        self.assertIn('"Magic Video",', main)
        self.assertIn('WINDOW_TITLE = "Magic Video"', single)
        self.assertIn("document.title = 'Magic Video · ' + title", self.js)

    def test_series_view_replaces_directory_tree_option(self):
        self.assertIn("剧集展示（作品 → 季 → 集）", self.js)
        self.assertNotIn("option('folder'", self.js)
        self.assertIn("if (s.view_mode === 'folder') s.view_mode = 'series';", self.js)

    def test_double_click_and_focusout_autosave(self):
        for token in ('data-edit-watched="', "addEventListener('dblclick'",
                     "addEventListener('focusout'", "saveWatchedEdit", "watchedSaving"):
            self.assertIn(token, self.js)

    def test_watched_card_fills_viewport_and_header_is_sticky(self):
        for token in ("classList.toggle('content-fill'", ".content.content-fill",
                      ".watched-card > .table-wrap", "position: sticky"):
            self.assertIn(token, self.js if token.startswith("classList") else self.css)
        self.assertIn('class="card watched-card"', self.js)
        self.assertIn('<div class="section-head"><h3>看过的列表</h3>', self.js)
        self.assertIn('id="watchedCount"', self.js)
        self.assertNotIn('<span class="hint" id="watchedCount"></span>', self.js)

    def test_topbar_repaints_immediately_on_theme_switch(self):
        self.assertIn('html[data-theme="light"] .topbar', self.css)
        self.assertIn('html[data-theme="dark"]  .topbar', self.css)

    def test_new_buttons_and_cover_progress_are_wired(self):
        for token in ("sync-watched-history", "clean-watched", "sync_watched_from_history",
                     "clean_watched_list", "coverProgress", "bar.value = num(s.done)",
                     "s.current", "window.addEventListener('focus'"):
            self.assertIn(token, self.js)

    def test_default_data_dir_is_d_drive(self):
        config_text = (ROOT / "app/config.py").read_text(encoding="utf-8")
        self.assertIn('DEFAULT_DATA_DIR = Path("D:/magicvideo")', config_text)
        start_text = (ROOT / "start").read_text(encoding="utf-8")
        self.assertIn("D:\\magicvideo", start_text)

    def test_cover_status_exposes_current(self):
        self.assertIn("current", covers.status())


if __name__ == "__main__":
    unittest.main()
