"""配置与数据库层测试。"""
from __future__ import annotations

import sqlite3
import unittest

from app import config, db
from tests.base import IsolatedDbTestCase

EXPECTED_TABLES = {
    "settings", "directories", "subjects", "media_files", "watched_list",
    "watch_progress", "watch_sessions", "name_alias", "work_meta", "scrape_cache",
}


class ConfigTests(unittest.TestCase):
    def test_defaults_cover_runtime_needs(self):
        for key in ("potplayer_path", "poll_interval_sec", "complete_percent",
                    "recording_enabled", "metadata_source", "bangumi_base"):
            self.assertIn(key, config.DEFAULTS)
        self.assertEqual(config.DEFAULTS["complete_percent"], 90)
        self.assertEqual(config.DEFAULTS["poll_interval_sec"], 3)

    def test_video_extensions_exclude_subtitles(self):
        self.assertIn(".mkv", config.VIDEO_EXTS)
        self.assertIn(".mp4", config.VIDEO_EXTS)
        for ext in (".srt", ".ass", ".nfo", ".txt"):
            self.assertNotIn(ext, config.VIDEO_EXTS)

    def test_builtin_dictionary_ships_with_source(self):
        self.assertTrue(config.BUILTIN_ALIAS_FILE.exists(), "内置词典文件必须随代码一起提供")


class DbTests(IsolatedDbTestCase):
    def test_init_creates_all_tables(self):
        tables = {r["name"] for r in db.rows("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue(EXPECTED_TABLES.issubset(tables))

    def test_init_is_idempotent(self):
        db.init_db()
        db.init_db()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM settings"), len(config.DEFAULTS))

    def test_journal_mode_is_wal(self):
        with db.session() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(str(mode).lower(), "wal")

    def test_settings_roundtrip_and_int_coercion(self):
        db.set_setting("poll_interval_sec", 7)
        self.assertEqual(db.get_setting("poll_interval_sec"), "7")
        self.assertEqual(db.get_int("poll_interval_sec"), 7)
        db.set_setting("poll_interval_sec", "not-a-number")
        self.assertEqual(db.get_int("poll_interval_sec", 3), 3)
        self.assertIsNone(db.get_setting("nonexistent_key"))
        self.assertEqual(db.get_setting("nonexistent_key", 42), "42")

    def test_all_settings_merges_defaults(self):
        merged = db.all_settings()
        for key in config.DEFAULTS:
            self.assertIn(key, merged)
        db.set_setting("complete_percent", 95)
        self.assertEqual(db.all_settings()["complete_percent"], "95")

    def test_migration_adds_columns_without_losing_rows(self):
        """模拟 0.1.0 的旧库：缺 title_cn / match_by，且已有真实观看记录。"""
        with db.session() as conn:
            conn.executescript("""
                DROP TABLE media_files;
                CREATE TABLE media_files (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE,
                    filename TEXT NOT NULL, title_raw TEXT, title_guess TEXT,
                    season_no INTEGER, episode_no INTEGER, status TEXT NOT NULL DEFAULT 'new'
                );
                DROP TABLE watched_list;
                CREATE TABLE watched_list (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
                    raw_text TEXT, season_no INTEGER, status TEXT NOT NULL DEFAULT 'watched'
                );
            """)
            conn.execute("INSERT INTO media_files(path, filename, title_guess) VALUES (?, ?, ?)",
                         ("D:/old/anime.mkv", "anime.mkv", "An Old Show"))
            conn.execute("INSERT INTO watched_list(title) VALUES (?)", ("旧作品",))
            db._migrate(conn)
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(media_files)")}
            self.assertIn("title_cn", cols)
            self.assertIn("match_by", cols)
            watched_cols = {r["name"] for r in conn.execute("PRAGMA table_info(watched_list)")}
            self.assertIn("match_by", watched_cols)
            self.assertEqual(conn.execute("SELECT title_guess FROM media_files").fetchone()[0],
                             "An Old Show")
            self.assertEqual(conn.execute("SELECT title FROM watched_list").fetchone()[0], "旧作品")

    def test_foreign_key_cascade_removes_progress(self):
        record = self.media()
        db.execute("INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent) "
                   "VALUES (?, 1000, 2000, 50)", (record["id"],))
        db.execute("DELETE FROM media_files WHERE id = ?", (record["id"],))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), 0)


if __name__ == "__main__":
    unittest.main()
