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


class EpisodeConfidenceTests(IsolatedDbTestCase):
    """集数解析的来源与可信度（OPTIMIZATION #6 尾巴）。

    低置信度只留给**语义本身有歧义**的写法（方括号数字、区间），
    规范的 `SxxEyy` / `1x01` / `第03话` 一律自动识别，不打扰用户。
    """

    def test_chinese_episode_is_not_mistaken_for_season(self):
        """`第03话` 以前会被季正则同时读成「第 3 季」，一部 12 集的番拆成 12 季。"""
        info = scanner.parse_filename("[Group] 葬送的芙莉莲 第03话 [1080p].mkv")
        self.assertEqual(info["episode_no"], 3)
        self.assertIsNone(info["season_no"], "第 N 话 不是季数")
        self.assertEqual(info["episode_confidence"], "high")

    def test_chinese_season_with_suffix_still_works(self):
        info = scanner.parse_filename("某某番 第2季 - 05.mkv")
        self.assertEqual((info["season_no"], info["episode_no"]), (2, 5))
        self.assertEqual(info["episode_confidence"], "medium")

    def test_x_format_gives_season_and_episode(self):
        info = scanner.parse_filename("Show.1x01.1080p.mkv")
        self.assertEqual((info["season_no"], info["episode_no"]), (1, 1))
        self.assertEqual(info["episode_source"], "xformat")
        self.assertEqual(info["episode_confidence"], "medium")

    def test_bracket_episode_is_low_confidence(self):
        info = scanner.parse_filename("Show.[01].mkv")
        self.assertEqual(info["episode_no"], 1)
        self.assertEqual(info["episode_source"], "bracket")
        self.assertEqual(info["episode_confidence"], "low")

    def test_episode_range_is_low_confidence(self):
        info = scanner.parse_filename("Show.01-02.mkv")
        self.assertEqual(info["episode_confidence"], "low")
        self.assertEqual(info["episode_source"], "trailing")

    def test_season_plus_episode_dash_is_not_a_range(self):
        """`Season 2 - 05` 里的「2 - 05」是季 + 集，不是集数区间。"""
        info = scanner.parse_filename("Show Season 2 - 05.mkv")
        self.assertEqual((info["season_no"], info["episode_no"]), (2, 5))
        self.assertEqual(info["episode_confidence"], "medium")

    def test_sxxeyy_is_high_and_never_flagged(self):
        info = scanner.parse_filename("Show.S01E02.mkv")
        self.assertEqual(info["episode_source"], "sxxeyy")
        self.assertEqual(info["episode_confidence"], "high")

    def test_year_and_quality_tags_do_not_produce_episodes(self):
        info = scanner.parse_filename("Your.Name.(2016).[1080p].mkv")
        self.assertIsNone(info["episode_no"])
        self.assertEqual(info["episode_confidence"], "")

    def test_scan_persists_source_and_confidence(self):
        root = self.tmp / "conf"
        root.mkdir()
        (root / "Show.[01].mkv").write_bytes(b"z" * 10)
        scanner.add_directory(str(root))
        scanner.scan()
        row = db.row("SELECT season_no, episode_no, episode_source, episode_confidence "
                     "FROM media_files WHERE filename = 'Show.[01].mkv'")
        self.assertEqual(row["episode_source"], "bracket")
        self.assertEqual(row["episode_confidence"], "low")

    def test_user_confirmation_survives_rescan(self):
        root = self.tmp / "conf2"
        root.mkdir()
        (root / "Show.[01].mkv").write_bytes(b"z" * 10)
        scanner.add_directory(str(root))
        scanner.scan()
        file_id = int(db.scalar("SELECT id FROM media_files WHERE filename = 'Show.[01].mkv'"))
        # 用户手工指定成第 2 季第 7 集
        db.execute("UPDATE media_files SET season_no = 2, episode_no = 7, "
                   "episode_source = 'user', episode_confidence = 'high' WHERE id = ?", (file_id,))
        scanner.scan()
        row = db.row("SELECT season_no, episode_no, episode_source FROM media_files WHERE id = ?",
                     (file_id,))
        self.assertEqual((row["season_no"], row["episode_no"]), (2, 7))
        self.assertEqual(row["episode_source"], "user", "重新扫描不能覆盖用户确认过的季集")


class MissingAndRelocateTests(IsolatedDbTestCase):
    """P1-1：目录不可达只标缺失；路径重定位只改路径，永不删记录与进度。"""

    def _media_folder(self, folder: Path, names=("Show - 01.mkv", "Show - 02.mkv")) -> Path:
        folder.mkdir()
        for name in names:
            (folder / name).write_bytes(b"x" * 64)
        scanner.add_directory(str(folder))
        scanner.scan()
        return folder

    def test_unreachable_directory_marks_missing_without_deleting(self):
        folder = self._media_folder(self.tmp / "gone")
        record = db.row("SELECT * FROM media_files ORDER BY id LIMIT 1")
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent) VALUES (?, ?, ?)",
                   (record["id"], 600_000, 50))

        # 模拟 U 盘被拔：整个目录消失
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()

        result = scanner.scan()
        self.assertEqual(result["missing_dirs"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2, "绝不能删行")
        self.assertIsNotNone(db.scalar("SELECT missing_since FROM media_files WHERE id = ?",
                                      (record["id"],)))
        self.assertEqual(db.scalar("SELECT position_ms FROM watch_progress"), 600_000,
                         "播放进度必须原样保留")
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?",
                                    (record["id"],)), "标记缺失不等于软删")

    def test_directory_coming_back_clears_missing_flag(self):
        folder = self._media_folder(self.tmp / "back")
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()
        scanner.scan()
        self.assertIsNotNone(db.scalar("SELECT missing_since FROM media_files LIMIT 1"))

        # 硬盘插回来：同样的文件重新出现
        folder.mkdir()
        (folder / "Show - 01.mkv").write_bytes(b"x" * 64)
        (folder / "Show - 02.mkv").write_bytes(b"x" * 64)
        scanner.scan()
        self.assertIsNone(db.scalar("SELECT missing_since FROM media_files LIMIT 1"))

    def test_diagnose_reports_reachability_and_missing_counts(self):
        folder = self._media_folder(self.tmp / "diag")
        for item in folder.iterdir():
            item.unlink()
        folder.rmdir()
        scanner.scan()

        data = scanner.diagnose_library()
        self.assertTrue(data["ok"])
        self.assertEqual(data["total_missing"], 2)
        self.assertEqual(len(data["missing_ids"]), 2)
        entry = data["directories"][0]
        self.assertFalse(entry["reachable"])
        self.assertEqual(entry["files"], 2)
        self.assertEqual(entry["missing"], 2)

    def test_relocate_preview_then_apply_keeps_progress(self):
        old_root = self._media_folder(self.tmp / "old")
        record = db.row("SELECT * FROM media_files ORDER BY id LIMIT 1")
        db.execute("INSERT INTO watch_progress(file_id, position_ms, percent) VALUES (?, ?, ?)",
                   (record["id"], 600_000, 50))

        # 整盘换符：文件挪到新根目录
        new_root = self.tmp / "new"
        new_root.mkdir()
        for item in sorted(old_root.iterdir()):
            item.rename(new_root / item.name)
        old_root.rmdir()

        preview = scanner.preview_relocate(str(old_root), str(new_root))
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["affected"], 2)
        self.assertEqual(preview["restorable"], 2)
        self.assertEqual(preview["conflicts"], 0)
        self.assertTrue(preview["new_prefix_exists"])

        result = scanner.apply_relocate(str(old_root), str(new_root))
        self.assertEqual(result["moved"], 2)
        self.assertEqual(result["conflicts"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2, "只改路径不删行")
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files WHERE missing_since IS NOT NULL"), 0)
        self.assertEqual(db.scalar("SELECT position_ms FROM watch_progress"), 600_000)
        for row in db.rows("SELECT path FROM media_files"):
            self.assertIn(str(new_root), row["path"])

    def test_relocate_skips_conflicting_targets(self):
        old_root = self._media_folder(self.tmp / "src", names=("Same.mkv",))
        new_root = self.tmp / "dst"
        new_root.mkdir()
        # 目标路径已经挂在另一条记录上：不能覆盖，只能跳过
        db.execute(
            "INSERT INTO media_files(path, filename, title_raw, title_guess, size, mtime) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(new_root / "Same.mkv"), "Same.mkv", "Same", "Same", 1, 1.0),
        )

        preview = scanner.preview_relocate(str(old_root), str(new_root))
        self.assertEqual(preview["conflicts"], 1)
        result = scanner.apply_relocate(str(old_root), str(new_root))
        self.assertEqual(result["moved"], 0)
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2, "冲突时谁都不能被删")

    def test_relocate_rejects_empty_or_identical_prefixes(self):
        self.assertFalse(scanner.preview_relocate("", "D:/x")["ok"])
        self.assertFalse(scanner.preview_relocate("D:/x", "D:/x/")["ok"])

    def test_dismiss_missing_only_soft_deletes_missing_rows(self):
        kept_folder = self._media_folder(self.tmp / "keep", names=("Keep.mkv",))
        gone_folder = self.tmp / "gone"
        gone_folder.mkdir()
        (gone_folder / "Gone.mkv").write_bytes(b"x" * 64)
        scanner.add_directory(str(gone_folder))
        scanner.scan()
        for item in gone_folder.iterdir():
            item.unlink()
        gone_folder.rmdir()
        scanner.scan()

        missing = db.row("SELECT * FROM media_files WHERE filename = 'Gone.mkv'")
        alive = db.row("SELECT * FROM media_files WHERE filename = 'Keep.mkv'")
        db.execute("INSERT INTO watch_progress(file_id, position_ms) VALUES (?, 120000)", (missing["id"],))

        out = scanner.dismiss_missing([missing["id"], alive["id"]])
        self.assertTrue(out["ok"])
        self.assertEqual(out["dismissed"], 1, "只处理标记为缺失的行")
        self.assertIsNotNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?",
                                      (missing["id"],)))
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?",
                                    (alive["id"],)))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2, "软删不是真删")
        self.assertEqual(db.scalar("SELECT position_ms FROM watch_progress"), 120000)


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
