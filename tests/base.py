"""测试隔离基类：临时数据库 + 禁止真实网络请求。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config, db, metadata

_PATCHED_KEYS = ("DATA_DIR", "DB_PATH", "COVER_DIR", "LOG_DIR", "ALIAS_IMPORT_FILE")


def reset_module_state() -> None:
    """清理进程内缓存，让每个用例都从干净状态开始。"""
    db._INITIALIZED = False
    metadata._BUILTIN_READY = False
    metadata._INDEX_DIRTY = True
    metadata._INDEX.clear()
    metadata._ONLINE_BLOCKED = False
    metadata._ONLINE_ERROR = ""
    metadata._CFG_CACHE = None
    metadata._CFG_AT = 0.0
    # 新增的进程内缓存（媒体库结果 / 网页搜索 / 黑名单词表）也要清，
    # 否则上一个用例的临时库数据会漏到下一个用例里。
    from app import api, blacklist, websearch
    api._MEDIA_CACHE.clear()
    websearch.clear_cache()
    blacklist.invalidate()


class IsolatedDbTestCase(unittest.TestCase):
    """所有涉及数据库的用例都继承本类。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="magicvideo-test-")
        tmp = Path(self._tmp.name)
        self.tmp = tmp
        self._orig = {k: getattr(config, k) for k in _PATCHED_KEYS}
        config.DATA_DIR = tmp
        config.DB_PATH = tmp / "library.db"
        config.COVER_DIR = tmp / "covers"
        config.LOG_DIR = tmp / "logs"
        config.ALIAS_IMPORT_FILE = tmp / "alias_import.json"
        reset_module_state()
        # 测试永远不发真实网络请求；需要验证在线分支时用例内自行替换该 mock
        self.online_mock = mock.patch.object(metadata, "search_online", return_value=None)
        self.mock_search = self.online_mock.start()
        db.init_db()
        metadata.ensure_builtin_loaded()

    def tearDown(self) -> None:
        self.online_mock.stop()
        for key, value in self._orig.items():
            setattr(config, key, value)
        reset_module_state()
        self._tmp.cleanup()

    def media(self, filename: str = "Test Show - 01.mkv", *, title_guess="Test Show",
              status="scanned", **extra) -> dict:
        """快速插入一条媒体文件记录并返回。"""
        values = {
            "path": str(self.tmp / filename),
            "filename": filename,
            "title_raw": Path(filename).stem,
            "title_guess": title_guess,
            "status": status,
            "size": 1024,
            "mtime": 1.0,
            **extra,
        }
        cols = ",".join(values)
        marks = ",".join("?" for _ in values)
        file_id = db.execute(f"INSERT INTO media_files({cols}) VALUES ({marks})", tuple(values.values()))
        return db.row("SELECT * FROM media_files WHERE id = ?", (file_id,))
