"""缓存键口径的守护测试（补强方案 C-2）。

铁律（AGENTS.md §2 / docs/IMPLEMENTATION_NOTES.md）：媒体库、剧集墙、看过的列表
这类「整份结果缓存」的键必须是**数据指纹**，**禁止用数据库文件或视频文件的 mtime**。

原因不是洁癖：守护进程每 3 秒写一次播放进度，SQLite 的 WAL 文件 mtime 一直在变，
一旦把 mtime 放进缓存键，缓存就永远命中不了，界面会退回到「每次切视图重算 200ms」。

这条以前只写在文档里，靠人看；现在用测试锁住，防止哪天有人「顺手」加回去。
新增缓存时请调用 `api._data_fingerprint()` 或 `api._db_signature()`，不要自己拼键。
"""
from __future__ import annotations

import inspect
import re
import unittest

from app import api, db
from tests.base import IsolatedDbTestCase

# 缓存键构造函数：它们的源码里不允许出现 mtime 相关字样
KEY_BUILDERS = ("_data_fingerprint", "_db_signature", "_cache_get", "_cache_put")

_FORBIDDEN = re.compile(r"mtime|st_ctime|getmtime", re.I)


class CacheKeyGuardTests(unittest.TestCase):

    def test_cache_key_builders_never_reference_mtime(self):
        for name in KEY_BUILDERS:
            fn = getattr(api, name, None)
            self.assertIsNotNone(fn, f"api.{name} 不存在，缓存键口径的守护需要同步更新")
            code = inspect.getsource(fn)
            # docstring 里会解释「为什么不用 mtime」，所以只看代码行
            body = _strip_docstring(code)
            self.assertIsNone(
                _FORBIDDEN.search(body),
                f"api.{name} 的代码里出现了 mtime —— 缓存键必须按数据指纹，"
                f"否则守护每 3 秒写进度就会让缓存永远失效",
            )

    def test_signature_has_no_file_stat_based_component(self):
        """签名只由「库路径 + 写库次数 + 数据统计」组成，不含任何文件 stat。"""
        sig = api._db_signature()
        self.assertIsInstance(sig, tuple)
        # 五维数据统计 + 库路径 + 写库次数
        self.assertGreaterEqual(len(sig), 6)
        for part in sig:
            self.assertIsInstance(part, (int, str), f"签名里出现了意外类型：{type(part)}")


class CacheInvalidationTests(IsolatedDbTestCase):

    def _insert_file(self, name: str) -> int:
        db.execute(
            "INSERT INTO media_files(path, filename, title_guess, season_no, episode_no) "
            "VALUES (?, ?, ?, 1, 1)",
            (f"D:\\lib\\{name}", name, "测试作品"),
        )
        return int(db.scalar("SELECT MAX(id) FROM media_files") or 0)

    def test_fingerprint_ignores_progress_writes_but_signature_tracks_them(self):
        """守护写进度不该让「分组类缓存」失效，但签名要变（覆盖改设置这类写）。"""
        file_id = self._insert_file("Show - 01.mkv")
        before_fp = api._data_fingerprint()
        before_sig = api._db_signature()

        db.execute(
            "INSERT INTO watch_progress(file_id, position_ms, percent, updated_at) "
            "VALUES (?, 12345, 3.0, ?)",
            (file_id, db.now()),
        )

        self.assertEqual(before_fp, api._data_fingerprint(),
                         "写播放进度不应改变分组指纹（否则守护每 3 秒写一次就废掉缓存）")
        self.assertNotEqual(before_sig, api._db_signature(),
                            "写库之后签名必须变，否则改设置后界面会读到旧结果")

    def test_fingerprint_changes_when_grouping_inputs_change(self):
        before = api._data_fingerprint()
        self._insert_file("Another Show - 02.mkv")
        self.assertNotEqual(before, api._data_fingerprint(),
                            "新增媒体文件必须让指纹变化")

    def test_fingerprint_changes_when_watched_list_changes(self):
        before = api._data_fingerprint()
        db.execute("INSERT INTO watched_list(title, source) VALUES ('某部番', 'manual')")
        self.assertNotEqual(before, api._data_fingerprint(),
                            "看过的列表变化必须让指纹变化（剧集墙的「看过」标记依赖它）")

    def test_fingerprint_is_stable_without_writes(self):
        self._insert_file("Stable Show - 01.mkv")
        self.assertEqual(api._data_fingerprint(), api._data_fingerprint())

    def test_cache_get_returns_none_after_ttl(self):
        api._MEDIA_CACHE.clear()
        key = ("probe",)
        api._cache_put(key, {"ok": True})
        self.assertEqual(api._cache_get(key), {"ok": True})
        # 手工把时间戳推到 TTL 之外，验证过期判定本身
        stamp, payload = api._MEDIA_CACHE[key]
        api._MEDIA_CACHE[key] = (stamp - api._MEDIA_CACHE_TTL - 1, payload)
        self.assertIsNone(api._cache_get(key))
        api._MEDIA_CACHE.clear()


def _strip_docstring(source: str) -> str:
    """去掉函数自身的 docstring，只留可执行代码。"""
    lines = source.splitlines()
    out, inside, quote = [], False, ""
    for line in lines:
        stripped = line.strip()
        if not inside and stripped.startswith(('"""', "'''")):
            quote = stripped[:3]
            if stripped.count(quote) >= 2 and len(stripped) > 3:
                continue
            inside = True
            continue
        if inside:
            if quote in stripped:
                inside = False
            continue
        out.append(line)
    return "\n".join(out)


if __name__ == "__main__":
    unittest.main()
