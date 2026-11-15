"""看过的列表 → 本地目录匹配 / 资源管理器跳转的回归契约。

铁律：
  * 前端只传 watched_list.id，绝不把路径传给后端；
  * 目录必须落在已登记的扫描根目录内，且不是系统目录；
  * 打开动作只读（os.startfile），测试一律 mock，绝不真的弹资源管理器。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import db, file_ops
from app.api import Api
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent


class SafeDirectoryTests(unittest.TestCase):
    """目录白名单校验：绝对路径 + 真实存在 + 非系统目录 + 在扫描根内。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="magicvideo-dir-")
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "Anime"
        self.folder = self.root / "[DBD-Raws] 葬送的芙莉莲"
        self.folder.mkdir(parents=True)
        (self.tmp / "Outside").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_accepts_directory_inside_scan_root(self):
        ok, why = file_ops.is_safe_directory(str(self.folder), [str(self.root)])
        self.assertTrue(ok, why)
        ok2, _ = file_ops.is_safe_directory(str(self.root), [str(self.root)])
        self.assertTrue(ok2)

    def test_rejects_relative_and_empty_paths(self):
        self.assertFalse(file_ops.is_safe_directory("Anime/xxx", [str(self.root)])[0])
        self.assertFalse(file_ops.is_safe_directory("", [str(self.root)])[0])

    def test_rejects_missing_directory(self):
        ok, why = file_ops.is_safe_directory(str(self.root / "不存在"), [str(self.root)])
        self.assertFalse(ok)
        self.assertIn("不存在", why)

    def test_rejects_system_directory(self):
        # 黑名单在 is_dir() 之前生效，路径不必真实存在
        ok, why = file_ops.is_safe_directory(str(self.tmp / "windows" / "system32"),
                                             [str(self.tmp)])
        self.assertFalse(ok)
        self.assertIn("系统目录", why)

    def test_rejects_directory_outside_scan_roots(self):
        ok, why = file_ops.is_safe_directory(str(self.tmp / "Outside"), [str(self.root)])
        self.assertFalse(ok)
        self.assertIn("扫描目录", why)


class WatchedFolderApiTests(IsolatedDbTestCase):
    """复用追剧列表的匹配索引，把作品名定位到发布目录。"""

    def setUp(self):
        super().setUp()
        self.api = Api()
        self.root = self.tmp / "Anime"
        self.folder = self.root / "[DBD-Raws] 葬送的芙莉莲"
        self.folder.mkdir(parents=True)
        (self.tmp / "Outside").mkdir()
        db.execute("INSERT INTO directories(path) VALUES (?)", (str(self.root),))
        self._media("Sousou no Frieren - 01.mkv", self.folder, "葬送的芙莉莲")
        self._media("Sousou no Frieren - 02.mkv", self.folder, "葬送的芙莉莲")
        self.matched_id = self._watched("葬送的芙莉莲", "葬送的芙莉莲")
        self.unmatched_id = self._watched("完全没下载过的作品", "")

    def _media(self, filename, folder, title_cn):
        return self.media(filename, path=str(folder / filename),
                          title_guess="Sousou no Frieren", title_cn=title_cn)

    def _watched(self, title, title_cn):
        return db.execute(
            "INSERT INTO watched_list(title, title_cn, source, status) VALUES (?, ?, 'manual', 'watched')",
            (title, title_cn or None),
        )

    def test_watched_folders_only_lists_matched_rows(self):
        result = self.api.watched_folders()
        self.assertTrue(result["ok"])
        self.assertEqual(result["folders"], {self.matched_id: str(self.folder)})
        self.assertNotIn(self.unmatched_id, result["folders"])

    def test_get_watched_list_structure_is_unchanged(self):
        """匹配目录不塞进列表返回体，旧契约（只按 id / 字段取值）不受影响。"""
        rows = self.api.get_watched_list(0)
        self.assertEqual(len(rows), 2)
        self.assertNotIn("folder", rows[0])

    def test_open_watched_folder_starts_explorer_on_the_directory(self):
        with mock.patch.object(os, "startfile", create=True) as start:
            result = self.api.open_watched_folder(self.matched_id)
        self.assertTrue(result["ok"], result.get("error"))
        self.assertEqual(result["path"], str(self.folder))
        start.assert_called_once_with(str(self.folder))
        # 只读打开：不能出现删除 / 写库副作用
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM media_files"), 2)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 2)

    def test_open_watched_folder_rejects_unmatched_and_bad_ids(self):
        with mock.patch.object(os, "startfile", create=True) as start:
            missing = self.api.open_watched_folder(self.unmatched_id)
            bad = self.api.open_watched_folder("not-a-number")
            gone = self.api.open_watched_folder(999999)
        self.assertFalse(missing["ok"])
        self.assertIn("没有匹配到", missing["error"])
        self.assertFalse(bad["ok"])
        self.assertFalse(gone["ok"])
        start.assert_not_called()

    def test_directory_outside_scan_roots_is_never_opened(self):
        """匹配到的文件若在未登记的目录里（例如外部播放记录），一律不跳转。"""
        self.media("Outside - 01.mkv", path=str(self.tmp / "Outside" / "Outside - 01.mkv"),
                   title_guess="Outside Show", title_cn="外部作品")
        outside_id = self._watched("外部作品", "外部作品")
        self.assertNotIn(outside_id, self.api.watched_folders()["folders"])
        with mock.patch.object(os, "startfile", create=True) as start:
            result = self.api.open_watched_folder(outside_id)
        self.assertFalse(result["ok"])
        start.assert_not_called()

    def test_files_directly_in_scan_root_are_opened_as_is(self):
        """文件直接放在扫描根里是常见情况，不能因为「不回退到根」而误杀。"""
        self.media("Standalone - 01.mkv", path=str(self.root / "Standalone - 01.mkv"),
                   title_guess="Standalone Show", title_cn="独立作品")
        item = self._watched("独立作品", "独立作品")
        self.assertEqual(self.api.watched_folders()["folders"].get(item), str(self.root))

    def test_stale_release_directory_falls_back_to_existing_parent(self):
        """发布子目录被改名/移走时，退到仍在扫描根内的作品目录，入口不消失。"""
        stale = self.folder / "[Old][WebRip 1080p]"
        self.media("Huanzao - 01.mkv", path=str(stale / "Huanzao - 01.mkv"),
                   title_guess="Huanzao", title_cn="幻造实验剧")
        item = self._watched("幻造实验剧", "幻造实验剧")
        self.assertFalse(stale.exists())
        self.assertEqual(self.api.watched_folders()["folders"].get(item), str(self.folder))
        with mock.patch.object(os, "startfile", create=True) as start:
            result = self.api.open_watched_folder(item)
        self.assertTrue(result["ok"], result.get("error"))
        start.assert_called_once_with(str(self.folder))

    def test_fallback_does_not_open_the_scan_root_itself(self):
        """回退最多到作品目录；上级就是扫描根时宁可不显示按钮。"""
        stale = self.root / "Gone"
        self.media("Lostdir - 01.mkv", path=str(stale / "Lostdir - 01.mkv"),
                   title_guess="Lostdir", title_cn="消失的目录")
        item = self._watched("消失的目录", "消失的目录")
        self.assertNotIn(item, self.api.watched_folders()["folders"])

    def test_fallback_never_escapes_scan_roots(self):
        """未登记目录里的失效路径也不能借回退绕过白名单。"""
        outside_stale = self.tmp / "Outside" / "Gone"
        self.media("Outgone - 01.mkv", path=str(outside_stale / "Outgone - 01.mkv"),
                   title_guess="Outgone", title_cn="外部失效作品")
        item = self._watched("外部失效作品", "外部失效作品")
        self.assertNotIn(item, self.api.watched_folders()["folders"])
        with mock.patch.object(os, "startfile", create=True) as start:
            self.assertFalse(self.api.open_watched_folder(item)["ok"])
        start.assert_not_called()

    def test_startfile_failure_is_reported_not_raised(self):
        with mock.patch.object(os, "startfile", create=True,
                               side_effect=OSError("资源管理器不可用")):
            result = self.api.open_watched_folder(self.matched_id)
        self.assertFalse(result["ok"])
        self.assertIn("资源管理器不可用", result["error"])


class WatchedFolderFrontendTests(unittest.TestCase):
    """静态契约：卡片墙 / 表格共用按钮，且只把 id 交给后端。"""

    def setUp(self):
        self.js = (ROOT / "web/app.js").read_text(encoding="utf-8")
        self.api = (ROOT / "app/api.py").read_text(encoding="utf-8")
        self.ops = (ROOT / "app/file_ops.py").read_text(encoding="utf-8")

    def test_button_action_and_api_are_wired(self):
        for token in ("watchedFolderButton", 'data-action="open-watched-folder"',
                      "case 'open-watched-folder'", "open_watched_folder",
                      "watched_folders", "watchedFolders", "打开文件夹"):
            self.assertIn(token, self.js)
        for token in ("def watched_folders", "def open_watched_folder",
                      "def _watched_folder_for"):
            self.assertIn(token, self.api)
        self.assertIn("def is_safe_directory", self.ops)

    def test_only_item_id_is_sent_to_backend(self):
        self.assertIn("call('open_watched_folder', Number(id))", self.js)
        self.assertNotIn("open_watched_folder', path", self.js)
        self.assertNotIn("os.startfile", self.js)

    def test_both_views_use_the_shared_button(self):
        # 定义 1 次 + 表格 1 次 + 卡片墙 1 次
        self.assertGreaterEqual(self.js.count("watchedFolderButton("), 3)
        self.assertIn("call('watched_folders')", self.js)

    def test_folder_api_failure_degrades_to_hidden_button(self):
        self.assertIn("call('watched_folders').catch", self.js)
