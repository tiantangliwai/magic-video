"""文件名解析、忽略规则与增量扫描测试。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from app import config, db, scanner
from tests.base import IsolatedDbTestCase


class FilenameTests(IsolatedDbTestCase):
    def test_fansub_release_name(self):
        name = ("[Nekomoe kissaten&LoliHouse] Sono Bisque Doll wa Koi wo Suru - 02 "
                "[WebRip 1080p HEVC-10bit AAC ASSx2].mkv")
        info = scanner.parse_filename(name)
        self.assertEqual(info["title_guess"], "Sono Bisque Doll wa Koi wo Suru")
        self.assertEqual(info["episode_no"], 2)

    def test_standard_sxxeyy(self):
        info = scanner.parse_filename("Spy.x.Family.S01E02.1080p.WEB-DL.mkv")
        self.assertEqual((info["season_no"], info["episode_no"]), (1, 2))
        self.assertNotIn("s01e02", info["title_guess"].lower())

    def test_year_and_quality_tags_are_removed_from_title(self):
        info = scanner.parse_filename("Your.Name.(2016).[1080p].mkv")
        self.assertEqual(info["year"], 2016)
        self.assertNotIn("2016", info["title_guess"])

    def test_chinese_episode_marker(self):
        info = scanner.parse_filename("[Group] 葬送的芙莉莲 第03话 [1080p].mkv")
        self.assertEqual(info["episode_no"], 3)

    def test_ep_marker(self):
        info = scanner.parse_filename("Frieren - EP12.mkv")
        self.assertEqual(info["episode_no"], 12)

    def test_season_two_marker(self):
        info = scanner.parse_filename("[Group] Sono Bisque Doll wa Koi wo Suru Season 2 - 05.mkv")
        self.assertEqual(info["season_no"], 2)
        self.assertEqual(info["episode_no"], 5)

    def test_movie_without_episode(self):
        info = scanner.parse_filename("Violet Evergarden (2020) [BDRip].mkv")
        self.assertIsNone(info["episode_no"])
        self.assertEqual(info["year"], 2020)


class IdTagTests(IsolatedDbTestCase):
    """P1-4：文件名里的作品 ID 标签（{tmdb-123} / [tvdbid-456]）。"""

    def test_tmdb_tag_is_extracted_and_removed_from_title(self):
        info = scanner.parse_filename("[Group] Frieren S01E03 [1080p]{tmdb-209867}.mkv")
        self.assertEqual(info["tmdb_id"], 209867)
        self.assertIsNone(info["tvdb_id"])
        self.assertEqual((info["season_no"], info["episode_no"]), (1, 3))
        self.assertEqual(info["title_guess"], "Frieren", "ID 标签不能污染标题")
        self.assertNotIn("tmdb", info["title_guess"].lower())

    def test_tvdbid_tag_variant(self):
        info = scanner.parse_filename("Some.Show.S02E05.[tvdbid-345678].mkv")
        self.assertEqual(info["tvdb_id"], 345678)
        self.assertNotIn("tvdbid", info["title_guess"].lower())

    def test_tag_without_number_is_stripped_but_yields_no_id(self):
        """`{tmdb-}` 这种残缺标签要剥掉，但不能凭它认作品。"""
        info = scanner.parse_filename("Show.Name.{tmdb-}.S01E01.mkv")
        self.assertIsNone(info["tmdb_id"])
        self.assertNotIn("tmdb", info["title_guess"].lower())

    def test_normal_names_carry_no_ids(self):
        info = scanner.parse_filename("Frieren - 01.mkv")
        self.assertIsNone(info["tmdb_id"])
        self.assertIsNone(info["tvdb_id"])

    def test_scan_persists_ids_on_media_rows(self):
        folder = self.tmp / "idscan"
        folder.mkdir()
        (folder / "Frieren S01E01 {tmdb-209867}.mkv").write_bytes(b"x" * 64)
        scanner.add_directory(str(folder))
        scanner.scan()
        row = db.row("SELECT tmdb_id, title_guess FROM media_files WHERE filename LIKE '%tmdb%'")
        self.assertEqual(row["tmdb_id"], 209867)
        self.assertEqual(row["title_guess"], "Frieren")


class IgnoreRuleTests(IsolatedDbTestCase):
    def test_subtitles_and_unknown_extensions_are_ignored(self):
        self.assertTrue(scanner.should_ignore(Path("D:/Anime/show.srt")))
        self.assertTrue(scanner.should_ignore(Path("D:/Anime/readme.txt")))

    def test_sample_and_trailer_are_ignored(self):
        self.assertTrue(scanner.should_ignore(Path("D:/Anime/[Group] Show - 01 Sample.mkv")))
        self.assertTrue(scanner.should_ignore(Path("D:/Anime/Show.Trailer.mp4")))

    def test_sp_token_must_not_blacklist_regular_titles(self):
        """「SP」是特典标记，但不能因为 'Spy' / 'Special' 含 sp 就误杀正片。"""
        self.assertFalse(scanner.should_ignore(Path("D:/Anime/Spy.x.Family.S01E02.mkv")))
        self.assertFalse(scanner.should_ignore(Path("D:/Anime/Show - Special Edition.mkv")))

    def test_ignored_system_directories(self):
        self.assertTrue(scanner.should_ignore(Path("D:/$RECYCLE.BIN/movie.mkv")))
        self.assertTrue(scanner.should_ignore(Path("D:/Anime/.git/movie.mkv")))

    def test_normal_video_is_kept(self):
        self.assertFalse(scanner.should_ignore(Path("D:/Anime/[Group] Show - 01 [1080p].mkv")))


class ScanFlowTests(IsolatedDbTestCase):
    def _make_library(self):
        root = self.tmp / "videos"
        root.mkdir()
        (root / "Attack on Titan - 01.mkv").write_bytes(b"x" * 10)
        (root / "Attack on Titan - 02.mkv").write_bytes(b"y" * 20)
        (root / "notes.srt").write_text("subtitle", encoding="utf-8")
        return root

    def test_add_directory_rejects_missing_path(self):
        result = scanner.add_directory(str(self.tmp / "does-not-exist"))
        self.assertFalse(result["ok"])
        self.assertIn("目录不存在", result["error"])

    def test_add_directory_is_unique(self):
        root = self._make_library()
        first = scanner.add_directory(str(root))
        second = scanner.add_directory(str(root))
        self.assertTrue(first["ok"] and second["ok"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM directories"), 1)

    def test_scan_is_incremental_and_matches_chinese(self):
        root = self._make_library()
        scanner.add_directory(str(root))
        first = scanner.scan()
        self.assertEqual(first["added"], 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2)
        second = scanner.scan()
        self.assertEqual(second["added"], 0)
        # 扫描当场完成中文名匹配，不留待办队列
        titles = {r["title_cn"] for r in db.rows("SELECT title_cn FROM media_files")}
        self.assertIn("进击的巨人", titles)

    def test_moved_file_is_relinked_and_keeps_progress(self):
        """文件移动/改名后按指纹重关联：id 与播放进度都不丢，不新增重复行。"""
        root = self.tmp / "videos"
        root.mkdir()
        src = root / "Show - 01.mkv"
        src.write_bytes(b"content-A" * 200)
        scanner.add_directory(str(root))
        scanner.scan()
        original = db.row("SELECT * FROM media_files WHERE filename = 'Show - 01.mkv'")
        fid = original["id"]
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent) VALUES (?, ?, ?)",
                   (fid, 123_000, 42.0))
        dest_dir = root / "Sub"
        dest_dir.mkdir()
        dest = dest_dir / "Show - 01 Renamed.mkv"
        src.rename(dest)  # rename 保留 mtime
        result = scanner.scan()
        rows = db.rows("SELECT * FROM media_files")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], fid)
        self.assertEqual(rows[0]["filename"], "Show - 01 Renamed.mkv")
        self.assertEqual(
            db.scalar("SELECT position_ms FROM watch_progress WHERE file_id = ?", (fid,)), 123_000)
        self.assertEqual(result["added"], 0)

    def test_copy_with_same_metadata_is_not_relinked(self):
        """原文件还在时，同 size/mtime 的另一份是副本，应各自入库而不是合并。"""
        import shutil
        root = self.tmp / "videos"
        root.mkdir()
        original = root / "Show - 01.mkv"
        original.write_bytes(b"content-B" * 200)
        scanner.add_directory(str(root))
        scanner.scan()
        first = db.row("SELECT * FROM media_files WHERE filename = 'Show - 01.mkv'")
        copy = root / "Show - 01 copy.mkv"
        shutil.copy2(original, copy)  # 保留 mtime、内容相同
        scanner.scan()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2)
        duplicated = db.row("SELECT * FROM media_files WHERE filename = 'Show - 01 copy.mkv'")
        self.assertIsNotNone(duplicated)
        self.assertNotEqual(duplicated["id"], first["id"])

    def test_scan_honours_disabled_directory(self):
        root = self._make_library()
        added = scanner.add_directory(str(root))
        scanner.set_directory_enabled(added["id"], False)
        self.assertEqual(scanner.scan()["added"], 0)

    def test_scan_survives_unreadable_file(self):
        root = self._make_library()
        scanner.add_directory(str(root))
        real_stat = Path.stat

        def flaky_stat(self, *args, **kwargs):
            if self.suffix.lower() in config.VIDEO_EXTS:
                raise OSError("locked")
            return real_stat(self, *args, **kwargs)

        with mock.patch.object(Path, "stat", flaky_stat):
            result = scanner.scan()
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 0)
        self.assertGreaterEqual(result["skipped"], 1)


if __name__ == "__main__":
    unittest.main()
