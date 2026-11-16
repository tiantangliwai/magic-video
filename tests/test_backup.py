"""app/backup.py 的 JSON 导出 / 恢复测试。

全部在 ``IsolatedDbTestCase`` 的临时库上跑，不碰真实 ``D:\\magicvideo``。
"""
from __future__ import annotations

import json
from pathlib import Path

from app import backup, db
from tests.base import IsolatedDbTestCase


class BackupTableTest(IsolatedDbTestCase):
    def test_tables_are_ordered_by_foreign_key(self):
        self.assertEqual(
            backup.TABLES,
            (
                "settings",
                "directories",
                "subjects",
                "media_files",
                "watched_list",
                "watchlist",
                "watch_progress",
                "watch_sessions",
                "name_alias",
            ),
        )
        self.assertEqual(len(backup.TABLES), 9)


class BackupExportTest(IsolatedDbTestCase):
    def test_export_empty_db_covers_all_tables(self):
        result = backup.export_to_json()

        self.assertTrue(result["ok"], result)
        self.assertEqual(set(result["counts"]), set(backup.TABLES))
        self.assertEqual(result["total"], sum(result["counts"].values()))
        self.assertEqual(result["counts"]["media_files"], 0)
        self.assertEqual(result["counts"]["watch_progress"], 0)

        path = Path(result["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.tmp / "backups")
        self.assertTrue(path.name.startswith("magicvideo-backup-"))

        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["app"], "magic-video")
        self.assertEqual(payload["format_version"], 1)
        self.assertTrue(payload["exported_at"])
        for table in backup.TABLES:
            self.assertIn(table, payload["tables"])
            self.assertEqual(payload["counts"][table], len(payload["tables"][table]))

    def test_export_to_explicit_path(self):
        target = self.tmp / "nested" / "my-backup.json"
        result = backup.export_to_json(target)

        self.assertTrue(result["ok"], result)
        self.assertEqual(Path(result["path"]), target.resolve())
        self.assertTrue(target.is_file())

    def test_list_backups_empty_without_directory(self):
        self.assertEqual(backup.list_backups(), [])

    def test_list_backups_reports_metadata(self):
        first = backup.export_to_json()
        second = backup.export_to_json()
        self.assertTrue(first["ok"] and second["ok"])

        items = backup.list_backups()
        self.assertEqual(len(items), 2)
        names = {item["name"] for item in items}
        self.assertIn(Path(first["path"]).name, names)
        self.assertIn(Path(second["path"]).name, names)
        for item in items:
            self.assertTrue(Path(item["path"]).is_file())
            self.assertGreater(item["size"], 0)
            self.assertEqual(len(item["mtime"]), 19)  # YYYY-MM-DD HH:MM:SS


class BackupRestoreTest(IsolatedDbTestCase):
    def _seed(self) -> dict:
        """造一条完整链路的数据：媒体 + 进度 + 看过 + 追剧 + 别名 + 设置。"""
        media = self.media("Seed Show - 01.mkv")
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent, play_count)"
            " VALUES (?, ?, ?, ?, ?)",
            (media["id"], 60_000, 1_440_000, 4.1, 2),
        )
        db.execute(
            "INSERT INTO watch_sessions(file_id, from_ms, to_ms, max_ms) VALUES (?, ?, ?, ?)",
            (media["id"], 0, 60_000, 60_000),
        )
        db.execute(
            "INSERT INTO watched_list(title, title_cn, episode_from, episode_to)"
            " VALUES (?, ?, ?, ?)",
            ("Seed Show", "种子剧", 1, 1),
        )
        db.execute(
            "INSERT INTO watchlist(title, downloaded, sort_order) VALUES (?, ?, ?)",
            ("想看的剧", 0, 1),
        )
        db.execute(
            "INSERT INTO name_alias(raw_title, title_cn) VALUES (?, ?)",
            ("Seed Raw", "别名剧"),
        )
        db.set_setting("theme", "dark")
        return media

    def _clear_all(self) -> None:
        for table in reversed(backup.TABLES):
            db.execute(f"DELETE FROM {table}")

    def test_overwrite_restore_roundtrip(self):
        self._seed()
        snapshot = backup.export_to_json(self.tmp / "snapshot.json")
        self.assertTrue(snapshot["ok"], snapshot)
        expected = json.loads(Path(snapshot["path"]).read_text(encoding="utf-8"))["tables"]

        self._clear_all()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM settings"), 0)

        result = backup.restore_from_json(snapshot["path"], mode="overwrite")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["inserted"]["media_files"], 1)
        self.assertEqual(result["inserted"]["watch_progress"], 1)
        self.assertEqual(result["inserted"]["watch_sessions"], 1)
        self.assertEqual(result["inserted"]["watched_list"], 1)
        self.assertEqual(result["inserted"]["watchlist"], 1)
        # name_alias 在 setUp 里被内置别名表填充过，按导出时行数断言
        self.assertGreater(result["inserted"]["name_alias"], 0)
        for table in backup.TABLES:
            self.assertEqual(result["inserted"][table], snapshot["counts"][table])
        self.assertEqual(sum(result["inserted"].values()), snapshot["total"])

        again = backup.export_to_json(self.tmp / "after.json")
        self.assertTrue(again["ok"], again)
        actual = json.loads(Path(again["path"]).read_text(encoding="utf-8"))["tables"]
        self.assertEqual(actual, expected)
        self.assertEqual(db.get_setting("theme"), "dark")

    def test_restore_creates_auto_backup_first(self):
        self._seed()
        snapshot = backup.export_to_json(self.tmp / "snapshot.json")
        self.assertTrue(snapshot["ok"], snapshot)

        result = backup.restore_from_json(snapshot["path"], mode="merge")
        self.assertTrue(result["ok"], result)

        auto = Path(result["auto_backup"])
        self.assertTrue(auto.is_file())
        self.assertEqual(auto.parent, self.tmp / "backups")
        self.assertTrue(auto.name.startswith("magicvideo-backup-"))
        self.assertTrue(any(item["path"] == str(auto) for item in backup.list_backups()))

    def test_merge_twice_does_not_duplicate(self):
        self._seed()
        snapshot = backup.export_to_json(self.tmp / "snapshot.json")
        before = snapshot["counts"]

        first = backup.restore_from_json(snapshot["path"], mode="merge")
        second = backup.restore_from_json(snapshot["path"], mode="merge")
        self.assertTrue(first["ok"], first)
        self.assertTrue(second["ok"], second)

        self.assertEqual(sum(second["inserted"].values()), 0)
        self.assertGreater(sum(second["skipped"].values()), 0)
        for table in backup.TABLES:
            self.assertEqual(db.scalar(f"SELECT COUNT(*) FROM {table}"), before[table])

    def test_merge_adds_only_missing_rows(self):
        media = self.media("Old Show - 01.mkv")
        snapshot = backup.export_to_json(self.tmp / "snapshot.json")
        self.assertTrue(snapshot["ok"], snapshot)

        # 备份之后新插的一条不能被 merge 冲掉
        fresh = self.media("Fresh Show - 01.mkv")
        result = backup.restore_from_json(snapshot["path"], mode="merge")
        self.assertTrue(result["ok"], result)

        self.assertEqual(result["inserted"]["media_files"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2)
        self.assertIsNotNone(db.row("SELECT id FROM media_files WHERE id = ?", (media["id"],)))
        self.assertIsNotNone(db.row("SELECT id FROM media_files WHERE id = ?", (fresh["id"],)))

    def test_overwrite_preserves_explicit_id(self):
        db.execute(
            "INSERT INTO media_files(id, path, filename, status) VALUES (?, ?, ?, ?)",
            (100, str(self.tmp / "hundred.mkv"), "hundred.mkv", "scanned"),
        )
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, duration_ms) VALUES (?, ?, ?)",
            (100, 5_000, 10_000),
        )
        snapshot = backup.export_to_json(self.tmp / "snapshot.json")
        self.assertTrue(snapshot["ok"], snapshot)

        self._clear_all()
        result = backup.restore_from_json(snapshot["path"], mode="overwrite")
        self.assertTrue(result["ok"], result)

        media = db.row("SELECT * FROM media_files WHERE id = 100")
        self.assertIsNotNone(media)
        self.assertEqual(media["filename"], "hundred.mkv")
        progress = db.row("SELECT * FROM watch_progress WHERE file_id = 100")
        self.assertIsNotNone(progress)
        self.assertEqual(progress["position_ms"], 5_000)

    def test_invalid_mode_rejected(self):
        snapshot = backup.export_to_json(self.tmp / "snapshot.json")
        result = backup.restore_from_json(snapshot["path"], mode="replace")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertFalse((self.tmp / "backups").exists())

    def test_missing_file_returns_error(self):
        result = backup.restore_from_json(self.tmp / "nope.json")

        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_invalid_json_returns_error(self):
        bad = self.tmp / "bad.json"
        bad.write_text("{ this is not json", encoding="utf-8")

        result = backup.restore_from_json(bad)
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_missing_tables_key_returns_error(self):
        payload = self.tmp / "no-tables.json"
        payload.write_text(
            json.dumps({"app": "magic-video", "format_version": 1}),
            encoding="utf-8",
        )

        result = backup.restore_from_json(payload)
        self.assertFalse(result["ok"])
        self.assertIn("tables", result["error"])


class TableExportTest(IsolatedDbTestCase):
    """CSV / NFO 导出（OPTIMIZATION #5 的下半句）。"""

    def _seed(self):
        db.execute(
            "INSERT INTO media_files(path, filename, title_guess, title_cn, season_no, "
            "episode_no, year, size, duration_ms) VALUES (?, ?, ?, ?, 1, 1, 2023, ?, ?)",
            (str(self.tmp / "a.mkv"), "a.mkv", "Frieren", "葬送的芙莉莲", 104857600, 1440000),
        )
        file_id = int(db.scalar("SELECT MAX(id) FROM media_files"))
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, percent, completed, "
            "total_watched_ms, last_played_at) VALUES (?, 0, 100, 1, 720000, ?)",
            (file_id, db.now()),
        )
        db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, ended_at, watched_ms) "
            "VALUES (?, ?, ?, 600000)",
            (file_id, "2026-11-16 20:00:00", "2026-11-16 20:15:00"),
        )
        db.execute(
            "INSERT INTO watched_list(title, title_cn, season_no, episode_from, episode_to, "
            "status, source) VALUES ('Frieren', '葬送的芙莉莲', 1, 1, 12, 'watched', 'manual')"
        )
        return file_id

    def test_csv_export_writes_bom_and_header(self):
        self._seed()
        result = backup.export_csv("history")

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["rows"], 1)
        path = Path(result["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.parent, self.tmp / "exports")
        raw = path.read_bytes()
        # Excel 认 BOM 才不会把中文读成乱码
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        text = raw.decode("utf-8-sig")
        self.assertIn("开始时间", text)
        self.assertIn("葬送的芙莉莲", text)

    def test_each_kind_exports_its_own_columns(self):
        self._seed()
        history = backup.export_csv("history")
        watched = backup.export_csv("watched")
        media = backup.export_csv("media")
        for r in (history, watched, media):
            self.assertTrue(r["ok"], r)
            self.assertEqual(r["rows"], 1)
        self.assertIn("原名", Path(watched["path"]).read_text(encoding="utf-8-sig"))
        self.assertIn("累计观看分钟", Path(media["path"]).read_text(encoding="utf-8-sig"))

    def test_unknown_kind_is_rejected(self):
        result = backup.export_csv("drop-table")
        self.assertFalse(result["ok"])
        self.assertIn("不支持", result["error"])

    def test_export_never_writes_into_video_directory(self):
        """铁律：导出只落数据目录的 exports，绝不写进媒体所在目录。"""
        self._seed()
        self.assertTrue(backup.export_csv("media")["ok"])
        self.assertTrue(backup.export_nfo()["ok"])
        self.assertEqual(list(self.tmp.glob("*.csv")), [])
        self.assertEqual(list(self.tmp.glob("*.nfo")), [])

    def test_nfo_export_builds_one_tvshow_per_work(self):
        self._seed()
        result = backup.export_nfo()

        self.assertTrue(result["ok"], result)
        self.assertEqual(result["works"], 1)
        root = Path(result["path"])
        self.assertEqual(root, self.tmp / "exports" / "nfo")
        nfo = root / "葬送的芙莉莲" / "tvshow.nfo"
        self.assertTrue(nfo.is_file(), "应按作品建子目录并写 tvshow.nfo")
        text = nfo.read_text(encoding="utf-8")
        self.assertIn("<title>葬送的芙莉莲</title>", text)
        self.assertIn("<year>2023</year>", text)

    def test_nfo_folder_name_is_windows_safe(self):
        self.assertEqual(backup._safe_folder('A/B:C*D?"E"'), "A_B_C_D__E_")
        self.assertEqual(backup._safe_folder("   "), "未命名")


if __name__ == "__main__":  # pragma: no cover
    import unittest

    unittest.main()