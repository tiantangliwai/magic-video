"""端到端冒烟测试：完整跑通「导入 → 扫描 → 播放 → 记录 → 续播 → 纠错」。

全程使用临时数据库与模拟的播放器状态，不触碰真实文件与真实 PotPlayer。
"""
from __future__ import annotations

from unittest import mock

from app import db, importer, potplayer, scanner
from app.api import Api
from app.watcher import Watcher
from tests.base import IsolatedDbTestCase


class EndToEndTests(IsolatedDbTestCase):
    def test_full_user_journey(self):
        # 1. 用户导入「看过的动漫列表」，中文名当场匹配完成
        preview = importer.preview("Attack on Titan S1 1-25\nSono Bisque Doll wa Koi wo Suru S2 01-12")
        self.assertEqual(preview["total"], 2)
        self.assertEqual(preview["rows"][0]["title_cn"], "进击的巨人")
        self.assertIn("第二季", preview["rows"][1]["title_cn"])
        self.assertEqual(importer.commit(preview["rows"], source="smoke")["inserted"], 2)
        self.assertIsNone(db.row("SELECT * FROM media_files"))  # 还没有扫描，不应凭空造文件

        # 2. 添加本地视频目录并扫描，外文名即时转中文
        root = self.tmp / "anime"
        root.mkdir()
        video = root / "[Group] Sono Bisque Doll wa Koi wo Suru - 03 [1080p].mkv"
        video.write_bytes(b"x" * 100)
        scanner.add_directory(str(root))
        scanned = scanner.scan()
        self.assertEqual(scanned["added"], 1)
        record = db.row("SELECT * FROM media_files")
        self.assertEqual(record["title_cn"], "更衣人偶坠入爱河")
        self.assertEqual(record["episode_no"], 3)

        # 3. 守护进程记录三段播放进度
        watcher = Watcher()
        for position in (120_000, 300_000, 900_000):
            fake = {
                "available": True, "media_name": record["filename"],
                "position_ms": position, "duration_ms": 1_420_000,
                "percent": round(position / 1_420_000 * 100, 2),
                "state": "playing", "state_code": 2, "healthy": True,
                "title": record["filename"] + " - PotPlayer",
            }
            with mock.patch.object(potplayer, "read_status", return_value=fake):
                watcher._tick()
        watcher.stop()
        progress = db.row("SELECT * FROM watch_progress WHERE file_id = ?", (record["id"],))
        self.assertEqual(progress["position_ms"], 900_000)
        self.assertGreater(progress["percent"], 60)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)

        # 4. 从断点续播
        api = Api()
        cont = api.get_continue_watching()
        self.assertEqual(len(cont), 1)
        self.assertEqual(cont[0]["display_title"], "更衣人偶坠入爱河")
        with mock.patch.object(potplayer, "play", return_value={"ok": True}) as play:
            result = api.play_file(record["id"])
        self.assertTrue(result["ok"])
        self.assertEqual(play.call_args[0][1], 900_000)

        # 5. 用户纠错：误标已看 → 标记未看 → 进度可重新出现，历史会话不丢
        api.mark_watched(record["id"])
        self.assertEqual(len(api.get_continue_watching()), 0)
        self.assertEqual(len(api.get_completed()), 1)
        api.mark_unwatched(record["id"])
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watch_sessions"), 1)
        self.assertEqual(db.scalar("SELECT COUNT(*) FROM watched_list"), 2)

        # 6. 历史时间线仍然完整可查
        history = api.get_history()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["display_title"], "更衣人偶坠入爱河")

    def test_real_database_is_untouched(self):
        """测试结束后真实数据库文件不应被本套件创建或改写（由隔离基类保证）。"""
        from app import config
        self.assertTrue(str(config.DB_PATH).startswith(str(self.tmp)))


if __name__ == "__main__":
    import unittest
    unittest.main()
