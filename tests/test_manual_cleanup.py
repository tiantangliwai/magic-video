"""人工清理：历史记录可删、整剧看完可删本地文件、观看记录绝不自动删。

所有涉及磁盘的用例都把 ``file_ops._shell_trash`` / ``file_ops.trash_file`` mock 掉，
测试绝不真的把任何文件移入回收站，更不会调用 os.remove / unlink。
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path
from unittest import mock

from app import config, db, file_ops, library_tree, metadata, scanner
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class FileOpsSafetyTests(unittest.TestCase):
    """纯逻辑校验：不碰数据库、不碰真实文件。"""

    def test_safe_path_accepts_absolute_video(self):
        ok, reason = file_ops.is_safe_video_path("D:/anime/Show - 01.mkv")
        self.assertTrue(ok, reason)

    def test_safe_path_rejects_relative_non_video_and_empty(self):
        for path in ("", "Show - 01.mkv", "D:/anime/readme.txt", "D:/anime/noext"):
            ok, _ = file_ops.is_safe_video_path(path)
            self.assertFalse(ok, path)

    def test_safe_path_rejects_system_directories(self):
        for path in (r"C:\Windows\System32\a.mkv", r"C:\Program Files\a.mp4",
                     r"D:\$RECYCLE.BIN\a.mkv"):
            ok, _ = file_ops.is_safe_video_path(path)
            self.assertFalse(ok, path)

    def test_source_never_contains_permanent_delete(self):
        """只看真实代码节点：文档字符串里提到 os.remove 是在解释「绝不这么干」。"""
        source = (ROOT / "app/file_ops.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        forbidden = {"remove", "unlink", "rmtree", "rmdir", "shutil"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = (node.func.attr if isinstance(node.func, ast.Attribute)
                        else node.func.id if isinstance(node.func, ast.Name) else "")
                self.assertNotIn(name, forbidden, f"出现永久删除调用：{name}")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name, forbidden)

    def test_trash_failure_never_falls_back_to_permanent_delete(self):
        with mock.patch.object(file_ops, "_shell_trash", return_value=(False, "boom")), \
             mock.patch.object(file_ops.Path, "is_file", return_value=True):
            result = file_ops.trash_file("D:/anime/Show - 01.mkv")
        self.assertFalse(result["ok"])
        self.assertIn("boom", result["error"])

    def test_trash_rejects_directory_link_and_missing_file(self):
        with mock.patch.object(file_ops.Path, "is_symlink", return_value=True):
            self.assertFalse(file_ops.trash_file("D:/anime/Show - 01.mkv")["ok"])
        with mock.patch.object(file_ops.Path, "is_symlink", return_value=False), \
             mock.patch.object(file_ops.Path, "is_file", return_value=False):
            result = file_ops.trash_file("D:/anime/Show - 01.mkv")
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["error"])


class ManualCleanupTests(IsolatedDbTestCase):
    def setUp(self):
        super().setUp()
        self.api = Api()
        # 全局防线：哪怕某个用例忘了 mock，也不会真的调用 Windows 回收站
        self._shell = mock.patch.object(file_ops, "_shell_trash", return_value=(True, ""))
        self.shell_mock = self._shell.start()
        db.execute("INSERT INTO watched_list(title, title_cn) VALUES ('测试作品', '测试作品')")

    def tearDown(self):
        self._shell.stop()
        super().tearDown()

    def video(self, name="Show - 01.mkv", *, completed=1, title_cn="测试作品", **extra):
        """插入一条真实的临时视频文件 + 进度 + 会话（文件只在临时目录里，绝不真删）。"""
        path = self.tmp / name
        path.write_bytes(b"fake video content")
        row = self.media(name, title_guess="Show", title_cn=title_cn, **extra)
        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, duration_ms, percent, completed, "
            "play_count, last_played_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
            (row["id"], 1000, 1000, 100, completed, db.now()),
        )
        db.execute(
            "INSERT INTO watch_sessions(file_id, started_at, ended_at, from_ms, to_ms, max_ms) "
            "VALUES (?, ?, ?, 0, 1000, 1000)",
            (row["id"], "2026-11-15 10:00:00", "2026-11-15 10:20:00"),
        )
        return row

    def group_id(self, label="测试作品"):
        for group in self.api._full_series_tree().get("groups", []):
            if group.get("label") == label:
                return group["id"]
        self.fail(f"找不到作品分组：{label}")

    # ---- 历史记录人工删除 ------------------------------------------------
    def test_delete_history_session_removes_only_that_row(self):
        self.video("Show - 01.mkv")
        second = db.execute(
            "INSERT INTO watch_sessions(file_id, started_at) VALUES (?, ?)",
            (db.scalar("SELECT id FROM media_files"), "2026-11-15 11:00:00"),
        )
        before = db.scalar("SELECT COUNT(*) FROM watch_progress")
        result = self.api.delete_history_session(second)
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], 1)
        self.assertTrue(result["kept_progress"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), before)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files"))

    def test_delete_history_session_rejects_invalid_or_missing_id(self):
        self.video()
        for bad in ("abc", None, 999999):
            result = self.api.delete_history_session(bad)
            self.assertFalse(result["ok"])
            self.assertEqual(result["deleted"], 0)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

    # ---- 整部剧「全部看完」判定 ------------------------------------------
    def test_series_node_marks_all_done_and_exposes_file_ids(self):
        a = self.video("Show - 01.mkv")
        b = self.video("Show - 02.mkv")
        group = self.api._full_series_tree()["groups"][0]
        self.assertTrue(group["all_done"])
        self.assertEqual(group["removable_count"], 2)
        self.assertEqual(set(group["file_ids"]), {a["id"], b["id"]})

    def test_series_node_is_not_all_done_when_one_episode_unwatched(self):
        self.video("Show - 01.mkv")
        self.video("Show - 02.mkv", completed=0)
        group = self.api._full_series_tree()["groups"][0]
        self.assertFalse(group["all_done"])
        self.assertEqual(group["file_ids"], [])

    def test_removable_count_excludes_already_removed_files(self):
        a = self.video("Show - 01.mkv")
        self.video("Show - 02.mkv")
        db.execute("UPDATE media_files SET deleted_at = ? WHERE id = ?", (db.now(), a["id"]))
        group = self.api._full_series_tree()["groups"][0]
        self.assertTrue(group["all_done"])          # 观看状态不受文件移除影响
        self.assertEqual(group["removable_count"], 1)

    def test_library_tree_marks_all_done_for_plain_rows(self):
        rows = [
            {"id": 1, "path": "D:/a/Show - 01.mkv", "filename": "Show - 01.mkv",
             "title_guess": "Show", "title_cn": "测试作品", "completed": 1},
            {"id": 2, "path": "D:/a/Show - 02.mkv", "filename": "Show - 02.mkv",
             "title_guess": "Show", "title_cn": "测试作品", "completed": 1},
        ]
        group = library_tree.series_tree(rows)["groups"][0]
        self.assertTrue(group["all_done"])
        self.assertEqual(group["file_ids"], [1, 2])

    # ---- 删除清单预览 ----------------------------------------------------
    def test_candidate_lists_completed_file_with_path(self):
        row = self.video()
        preview = self.api.get_delete_candidates([row["id"]])
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["deletable_count"], 1)
        self.assertEqual(preview["files"][0]["path"], str(self.tmp / "Show - 01.mkv"))
        self.assertTrue(preview["keeps_records"])

    def test_candidate_rejects_unwatched_episode(self):
        row = self.video(completed=0)
        preview = self.api.get_delete_candidates([row["id"]])
        self.assertEqual(preview["deletable_count"], 0)
        self.assertIn("还没看完", preview["files"][0]["reason"])

    def test_candidate_rejects_external_and_already_removed(self):
        external = self.video(status="external")
        removed = self.video("Show - 02.mkv")
        db.execute("UPDATE media_files SET deleted_at = ? WHERE id = ?", (db.now(), removed["id"]))
        preview = self.api.get_delete_candidates([external["id"], removed["id"]])
        self.assertEqual(preview["deletable_count"], 0)
        self.assertIn("外部打开", preview["files"][0]["reason"])
        self.assertIn("回收站", preview["files"][1]["reason"])

    def test_group_preview_requires_every_episode_completed(self):
        a = self.video("Show - 01.mkv")
        self.video("Show - 02.mkv", completed=0)
        gid = self.group_id()
        preview = self.api.get_delete_candidates([], gid)
        self.assertFalse(preview["ok"])
        self.assertIn("未看完", preview["error"])
        refused = self.api.delete_local_files([a["id"]], gid)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["deleted"], 0)
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?", (a["id"],)))

    def test_group_preview_lists_whole_series(self):
        self.video("Show - 01.mkv")
        self.video("Show - 02.mkv")
        preview = self.api.get_delete_candidates([], self.group_id())
        self.assertTrue(preview["ok"])
        self.assertEqual(len(preview["files"]), 2)
        self.assertEqual(preview["deletable_count"], 2)
        self.assertEqual(preview["title"], "测试作品")

    def test_group_preview_rejects_unknown_group(self):
        self.assertFalse(self.api.get_delete_candidates([], "series::不存在的作品")["ok"])
        self.assertFalse(self.api.delete_local_files([1], "series::不存在的作品")["ok"])

    # ---- 核心：删本地文件，但观看记录一律保留 -------------------------------
    def test_delete_local_file_keeps_every_watch_record(self):
        row = self.video()
        before = {
            "media_files": db.scalar("SELECT COUNT(*) FROM media_files"),
            "watch_progress": db.scalar("SELECT COUNT(*) FROM watch_progress"),
            "watch_sessions": db.scalar("SELECT COUNT(*) FROM watch_sessions"),
            "watched_list": db.scalar("SELECT COUNT(*) FROM watched_list"),
        }
        with mock.patch.object(file_ops, "trash_file", return_value={"ok": True, "error": ""}) as trash:
            result = self.api.delete_local_files([row["id"]])
        trash.assert_called_once_with(str(self.tmp / "Show - 01.mkv"))
        self.assertTrue(result["ok"])
        self.assertEqual(result["deleted"], 1)
        self.assertTrue(result["kept_records"])
        for table, count in before.items():
            self.assertEqual(db.scalar(f"SELECT COUNT(*) FROM {table}"), count, table)
        self.assertIsNotNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?", (row["id"],)))
        self.assertEqual(db.scalar("SELECT completed FROM watch_progress WHERE file_id = ?", (row["id"],)), 1)
        self.assertEqual(db.scalar("SELECT MAX(max_ms) FROM watch_sessions WHERE file_id = ?", (row["id"],)), 1000)

    def test_delete_whole_series_keeps_all_records(self):
        rows = [self.video("Show - 01.mkv"), self.video("Show - 02.mkv")]
        gid = self.group_id()
        with mock.patch.object(file_ops, "trash_file", return_value={"ok": True, "error": ""}):
            result = self.api.delete_local_files([r["id"] for r in rows], gid)
        self.assertEqual(result["deleted"], 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files WHERE deleted_at IS NOT NULL"), 2)

    def test_history_and_progress_still_readable_after_local_file_removed(self):
        row = self.video()
        with mock.patch.object(file_ops, "trash_file", return_value={"ok": True, "error": ""}):
            self.api.delete_local_files([row["id"]])
        history = self.api.get_history(10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["file_id"], row["id"])
        self.assertEqual(len(self.api.get_completed(10)), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 1)

    def test_play_after_local_file_removed_explains_recycle_bin(self):
        row = self.video()
        db.execute("UPDATE media_files SET deleted_at = ? WHERE id = ?", (db.now(), row["id"]))
        (self.tmp / "Show - 01.mkv").unlink()      # 只删自己建的临时文件
        result = self.api.play_file(row["id"])
        self.assertFalse(result["ok"])
        self.assertIn("回收站", result["error"])
        self.assertIn("观看记录已保留", result["error"])

    def test_delete_failure_never_marks_file_or_records(self):
        row = self.video()
        with mock.patch.object(file_ops, "trash_file",
                               return_value={"ok": False, "error": "回收站不可用"}):
            result = self.api.delete_local_files([row["id"]])
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["deleted"], 0)
        self.assertIn("回收站不可用", result["errors"][0])
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?", (row["id"],)))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

    def test_second_delete_is_skipped_not_repeated(self):
        row = self.video()
        with mock.patch.object(file_ops, "trash_file", return_value={"ok": True, "error": ""}) as trash:
            self.api.delete_local_files([row["id"]])
            again = self.api.delete_local_files([row["id"]])
        self.assertEqual(again["deleted"], 0)
        self.assertEqual(again["skipped"], 1)
        self.assertEqual(trash.call_count, 1)

    def test_unwatched_and_external_files_are_skipped(self):
        done = self.video("Show - 01.mkv")
        pending = self.video("Show - 02.mkv", completed=0)
        external = self.video("Show - 03.mkv", status="external")
        with mock.patch.object(file_ops, "trash_file", return_value={"ok": True, "error": ""}) as trash:
            result = self.api.delete_local_files([done["id"], pending["id"], external["id"]])
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["skipped"], 2)
        self.assertEqual(trash.call_count, 1)

    def test_group_delete_refuses_foreign_file_id(self):
        """前端传来的 id 必须落在后端重建出的分组内，防止伪造删到别的作品。"""
        a = self.video("Show - 01.mkv")
        other = self.video("Other - 01.mkv", title_cn="别的作品")
        group = {"id": "series::测试作品", "label": "测试作品",
                 "all_done": True, "file_ids": [a["id"]]}
        with mock.patch.object(Api, "_find_series_group", return_value=group), \
             mock.patch.object(file_ops, "trash_file", return_value={"ok": True, "error": ""}) as trash:
            result = self.api.delete_local_files([a["id"], other["id"]], "series::测试作品")
        self.assertFalse(result["ok"])
        self.assertIn("不属于该作品", result["error"])
        trash.assert_not_called()
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files"))

    def test_empty_and_oversized_requests_are_rejected(self):
        self.assertFalse(self.api.delete_local_files([])["ok"])
        self.assertFalse(self.api.delete_local_files(None)["ok"])
        result = self.api.delete_local_files(list(range(1, 500)))
        self.assertFalse(result["ok"])
        self.assertIn("分批", result["error"])

    def test_normalize_ids_dedups_and_drops_invalid(self):
        self.assertEqual(Api._normalize_ids([3, "4", 3, 0, -1, "x", None]), [3, 4])
        self.assertEqual(Api._normalize_ids("7"), [7])
        self.assertEqual(Api._normalize_ids(None), [])

    # ---- 扫描：文件回来清标记、文件消失保留记录 -----------------------------
    def _scan(self):
        with mock.patch.object(metadata, "auto_match_media",
                               return_value={"matched": 0, "total": 0, "online_available": False}):
            return scanner.scan()

    def test_scan_clears_marker_when_file_reappears(self):
        row = self.video()
        db.execute("INSERT INTO directories(path, enabled) VALUES (?, 1)", (str(self.tmp),))
        db.execute("UPDATE media_files SET dir_id = ?, deleted_at = ? WHERE id = ?",
                   (db.scalar("SELECT id FROM directories"), db.now(), row["id"]))
        self._scan()
        self.assertIsNone(db.scalar("SELECT deleted_at FROM media_files WHERE id = ?", (row["id"],)))
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

    def test_scan_never_deletes_rows_when_file_is_missing(self):
        row = self.video()
        db.execute("INSERT INTO directories(path, enabled) VALUES (?, 1)", (str(self.tmp),))
        db.execute("UPDATE media_files SET dir_id = ? WHERE id = ?",
                   (db.scalar("SELECT id FROM directories"), row["id"]))
        (self.tmp / "Show - 01.mkv").unlink()      # 只删自己建的临时文件
        self._scan()
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_progress"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)


class FrontendContractTests(unittest.TestCase):
    """静态契约：人工入口存在，且没有任何自动删除/定时清理调用点。"""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "web/style.css").read_text(encoding="utf-8")
        cls.api_src = (ROOT / "app/api.py").read_text(encoding="utf-8")

    def test_manual_delete_entries_exist(self):
        for token in ("confirmDialog(", "confirmAndDeleteLocalFiles(",
                      "data-action=\"delete-history\"", "data-action=\"delete-local-file\"",
                      "data-action=\"delete-series-files\"",
                      "call('delete_history_session'", "call('get_delete_candidates'",
                      "call('delete_local_files'", "get_history", "all_done"):
            self.assertIn(token, self.js, token)

    def test_confirmation_lists_paths_and_warns(self):
        self.assertIn("⚠️ 此操作非常危险，可能导致不可逆的数据丢失！", self.js)
        self.assertIn("items: usable.map(f => f.path)", self.js)
        self.assertIn("观看进度、历史记录、看过的列表和媒体库记录会全部保留", self.js)

    def test_modal_is_styled_for_both_themes(self):
        self.assertIn(".modal-layer", self.css)
        self.assertIn(".modal-list", self.css)
        self.assertIn(".btn-danger", self.css)

    def test_history_delete_is_only_explicit_api(self):
        # 整个后端只有 delete_history_session 里能删 watch_sessions，且只能由前端调用
        sources = {
            p.name: p.read_text(encoding="utf-8")
            for p in (ROOT / "app").glob("*.py")
        }
        total = sum(text.count("DELETE FROM watch_sessions") for text in sources.values())
        self.assertEqual(total, 1)
        self.assertEqual(self.api_src.count("DELETE FROM watch_sessions"), 1)
        self.assertNotIn("DELETE FROM watch_sessions", sources["watcher.py"])
        self.assertNotIn("DELETE FROM watch_sessions", sources["scanner.py"])
        self.assertNotIn("DELETE FROM watch_sessions", sources["importer.py"])

    def test_no_automatic_local_file_deletion(self):
        sources = "\n".join(
            p.read_text(encoding="utf-8") for p in (ROOT / "app").glob("*.py")
        )
        # 只有方法定义本身，没有任何 Python 侧自动调用（没有定时/扫描/会话关闭时触发）
        self.assertEqual(sources.count("def delete_local_files("), 1)
        self.assertNotIn("delete_local_files(", sources.replace("def delete_local_files(", ""))
        self.assertNotIn("delete_history_session(", sources.replace("def delete_history_session(", ""))

    def test_delete_api_never_deletes_media_rows(self):
        self.assertNotIn("DELETE FROM media_files", self.api_src)
        self.assertIn("UPDATE media_files SET deleted_at", self.api_src)


if __name__ == "__main__":
    unittest.main()
