"""pywebview js_api 桥接层测试。"""
from __future__ import annotations

from unittest import mock

from app import __version__, db, potplayer
from app.api import Api
from tests.base import IsolatedDbTestCase


class ApiTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()

    def test_app_info_reports_version_and_paths(self):
        info = self.api.get_app_info()
        self.assertEqual(info["version"], __version__)
        self.assertIn("library.db", info["db_path"])
        self.assertTrue(info["python"])

    def test_stats_are_zero_on_fresh_database(self):
        stats = self.api.get_stats()
        self.assertEqual(stats["files"], 0)
        self.assertEqual(stats["sessions"], 0)
        self.assertEqual(stats["in_progress"], 0)

    def test_import_roundtrip_through_bridge(self):
        preview = self.api.preview_import("Attack on Titan\nFrieren")
        self.assertEqual(preview["total"], 2)
        result = self.api.commit_import(preview["rows"])
        self.assertEqual(result["inserted"], 2)
        self.assertEqual(len(self.api.get_watched_list()), 2)
        self.api.delete_watched(self.api.get_watched_list()[0]["id"])
        self.assertEqual(len(self.api.get_watched_list()), 1)

    def test_save_settings_only_accepts_whitelisted_keys(self):
        self.api.save_settings({"poll_interval_sec": 9, "injected_key": "evil",
                                "complete_percent": 88})
        self.assertEqual(db.get_int("poll_interval_sec"), 9)
        self.assertEqual(db.get_int("complete_percent"), 88)
        self.assertIsNone(db.get_setting("injected_key"))

    def test_settings_change_resets_online_probe(self):
        with mock.patch("app.metadata.reset_online") as reset:
            self.api.save_settings({"http_proxy": "http://127.0.0.1:7890"})
        reset.assert_called_once()

    def test_get_settings_includes_dictionary_and_autostart(self):
        settings = self.api.get_settings()
        self.assertIn("dictionary", settings)
        self.assertIn("autostart_enabled", settings)
        self.assertIn("potplayer_exists", settings)
        self.assertGreater(settings["dictionary"]["builtin_terms"], 50)

    def test_play_file_rejects_missing_path(self):
        record = self.media("missing.mkv")
        result = self.api.play_file(record["id"])
        self.assertFalse(result["ok"])
        self.assertIn("已被移动", result["error"])

    def test_play_file_uses_saved_position(self):
        video = self.tmp / "Show.mkv"
        video.write_bytes(b"")
        record = self.media("Show.mkv", path=str(video))
        db.execute("INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent) "
                   "VALUES (?, ?, ?, ?)", (record["id"], 300_000, 1_200_000, 25))
        with mock.patch.object(potplayer, "play", return_value={"ok": True}) as play:
            result = self.api.play_file(record["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(play.call_args[0][1], 300_000)
        # 从头播放时不传进度
        with mock.patch.object(potplayer, "play", return_value={"ok": True}) as play:
            self.api.play_file(record["id"], from_start=True)
        self.assertEqual(play.call_args[0][1], 0)

    def test_continue_watching_excludes_completed(self):
        a = self.media("Show - 01.mkv")
        b = self.media("Show - 02.mkv")
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, completed) "
                   "VALUES (?, ?, ?, ?)", (a["id"], 1000, 10, 0))
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent, completed) "
                   "VALUES (?, ?, ?, ?)", (b["id"], 1000, 95, 1))
        cont = self.api.get_continue_watching()
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont[0]["file_id"], a["id"])
        done = self.api.get_completed()
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["file_id"], b["id"])

    def test_mark_watched_and_unwatched(self):
        record = self.media()
        db.execute("INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent, completed) "
                   "VALUES (?, ?, ?, ?, ?)", (record["id"], 500_000, 1_000_000, 50, 0))
        self.assertTrue(self.api.mark_watched(record["id"])["ok"])
        row = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (record["id"],))
        self.assertEqual(row["completed"], 1)
        self.assertEqual(row["position_ms"], 0)
        self.assertTrue(self.api.mark_unwatched(record["id"])["ok"])
        row = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (record["id"],))
        self.assertEqual(row["completed"], 0)
        # 纠错操作不能删掉已有的观看会话
        db.execute("INSERT INTO watch_sessions(file_id, started_at, from_ms, to_ms, max_ms) "
                   "VALUES (?, ?, 0, 100, 100)", (record["id"], db.now()))
        self.api.mark_unwatched(record["id"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

    def test_reset_progress_keeps_history(self):
        record = self.media()
        db.execute("INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent) "
                   "VALUES (?, ?, ?, ?)", (record["id"], 600_000, 1_000_000, 60))
        db.execute("INSERT INTO watch_sessions(file_id, started_at, from_ms, to_ms, max_ms) "
                   "VALUES (?, ?, 0, 600000, 600000)", (record["id"], db.now()))
        self.assertTrue(self.api.reset_progress(record["id"])["ok"])
        row = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (record["id"],))
        self.assertEqual((row["position_ms"], row["percent"], row["completed"]), (0, 0, 0))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

    def test_mark_state_rejects_unknown_file(self):
        self.assertFalse(self.api.mark_watched(999999)["ok"])
        self.assertFalse(self.api.reset_progress(999999)["ok"])

    def test_directories_and_scan_bridge(self):
        root = self.tmp / "lib"
        root.mkdir()
        (root / "Frieren - 01.mkv").write_bytes(b"x")
        self.assertTrue(self.api.add_directory(str(root))["ok"])
        # add_directory 触发后台线程，等待其完成
        import time
        for _ in range(40):
            if self.api.get_last_scan() is not None:
                break
            time.sleep(0.1)
        self.assertEqual(len(self.api.get_directories()), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 1)
        self.assertEqual(db.scalar("SELECT title_cn FROM media_files"), "葬送的芙莉莲")

    def test_daemon_running_returns_bool_dict(self):
        result = self.api.daemon_running()
        self.assertIn("running", result)
        self.assertIsInstance(result["running"], bool)

    def test_rematch_and_dictionary_bridge(self):
        self.api.dictionary_status()
        out = self.api.rematch_all()
        self.assertIn("matched", out)
        self.assertNotIn("results", out)


if __name__ == "__main__":
    import unittest
    unittest.main()
