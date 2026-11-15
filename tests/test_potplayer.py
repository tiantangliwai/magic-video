"""PotPlayer 桥接层测试：进度换算、窗口标题解析、断点参数、非 Windows 安全降级。"""
from __future__ import annotations

from unittest import mock

from app import db, potplayer
from tests.base import IsolatedDbTestCase


class PureFunctionTests(IsolatedDbTestCase):
    def test_format_seek(self):
        cases = {
            0: "00:00:00",
            999: "00:00:00",
            1000: "00:00:01",
            61_000: "00:01:01",
            3_661_000: "01:01:01",
            17 * 60_000 + 16_000: "00:17:16",
            -5000: "00:00:00",
        }
        for ms, expected in cases.items():
            self.assertEqual(potplayer.format_seek(ms), expected, ms)

    def test_parse_media_name(self):
        self.assertEqual(potplayer.parse_media_name("Show - 01.mkv - PotPlayer"), "Show - 01.mkv")
        self.assertEqual(potplayer.parse_media_name("Show.mkv - PotPlayer 1.7"), "Show.mkv")
        self.assertEqual(potplayer.parse_media_name("Show.mkv"), "Show.mkv")
        self.assertEqual(potplayer.parse_media_name(""), "")

    def test_state_labels(self):
        self.assertEqual(potplayer.STATE_LABEL.get(2), "playing")
        self.assertEqual(potplayer.STATE_LABEL.get(1), "paused")
        self.assertEqual(potplayer.STATE_LABEL.get(-1), "stopped")


class ResolveMediaTests(IsolatedDbTestCase):
    def test_exact_filename(self):
        record = self.media("Frieren - 01.mkv")
        self.assertEqual(potplayer.resolve_media("Frieren - 01.mkv")["id"], record["id"])

    def test_absolute_path(self):
        record = self.media("Frieren - 01.mkv")
        self.assertEqual(potplayer.resolve_media(record["path"])["id"], record["id"])

    def test_stem_fallback(self):
        record = self.media("[Group] Frieren - 01 [1080p].mkv")
        hit = potplayer.resolve_media("Frieren - 01")
        self.assertEqual(hit["id"], record["id"])

    def test_unknown_returns_none(self):
        self.assertIsNone(potplayer.resolve_media("Completely Unknown.mkv"))
        self.assertIsNone(potplayer.resolve_media(""))


class ReadStatusTests(IsolatedDbTestCase):
    def _fake_status(self, *, total=1_420_000, position=1_036_000, state=2, volume=50):
        def send(hwnd, code, lparam=0):
            return {
                potplayer.POT_GET_TOTAL_TIME: total,
                potplayer.POT_GET_CURRENT_TIME: position,
                potplayer.POT_GET_PLAY_STATUS: state,
                potplayer.POT_GET_VOLUME: volume,
            }[code]
        return send

    def test_read_status_computes_percent_and_clamps(self):
        with mock.patch.object(potplayer, "find_window", return_value=123), \
             mock.patch.object(potplayer, "window_title", return_value="Show.mkv - PotPlayer"), \
             mock.patch.object(potplayer, "send", side_effect=self._fake_status()), \
             mock.patch.object(potplayer, "self_check", return_value=True):
            status = potplayer.read_status()
        self.assertTrue(status["available"])
        self.assertEqual(status["media_name"], "Show.mkv")
        self.assertEqual(status["duration_ms"], 1_420_000)
        self.assertEqual(status["position_ms"], 1_036_000)
        self.assertAlmostEqual(status["percent"], 72.96, places=1)
        self.assertEqual(status["state"], "playing")
        self.assertTrue(status["healthy"])

    def test_position_never_exceeds_duration(self):
        with mock.patch.object(potplayer, "find_window", return_value=123), \
             mock.patch.object(potplayer, "window_title", return_value="x.mkv"), \
             mock.patch.object(potplayer, "send",
                               side_effect=self._fake_status(total=1000, position=5000)), \
             mock.patch.object(potplayer, "self_check", return_value=True):
            status = potplayer.read_status()
        self.assertEqual(status["position_ms"], 1000)
        self.assertEqual(status["percent"], 100.0)

    def test_no_window_reports_unavailable(self):
        with mock.patch.object(potplayer, "find_window", return_value=0):
            status = potplayer.read_status()
        self.assertFalse(status["available"])
        self.assertIn("未运行", status["reason"])

    def test_no_user32_is_safe(self):
        """非 Windows 环境导入本模块也不应崩溃，且安全地报告不可用。"""
        with mock.patch.object(potplayer, "_user32", None):
            self.assertEqual(potplayer.find_window(), 0)
            self.assertEqual(potplayer.window_title(1), "")
            self.assertEqual(potplayer.send(1, potplayer.POT_GET_VOLUME), 0)
            self.assertFalse(potplayer.read_status()["available"])


class PlayTests(IsolatedDbTestCase):
    def _prepare(self):
        exe = self.tmp / "PotPlayerMini64.exe"
        video = self.tmp / "Show.mkv"
        exe.write_bytes(b"")
        video.write_bytes(b"")
        db.set_setting("potplayer_path", str(exe))
        return exe, video

    def test_play_builds_seek_and_current_args(self):
        exe, video = self._prepare()
        with mock.patch.object(potplayer.subprocess, "Popen") as popen:
            result = potplayer.play(str(video), 17 * 60_000 + 16_000)
        self.assertTrue(result["ok"])
        args = popen.call_args[0][0]
        self.assertEqual(args[0], str(exe))
        self.assertEqual(args[1], str(video))
        self.assertIn("/seek=00:17:16", args)
        self.assertIn("/current", args)
        self.assertIn("/autoplay", args)

    def test_play_from_start_has_no_seek(self):
        _, video = self._prepare()
        with mock.patch.object(potplayer.subprocess, "Popen") as popen:
            result = potplayer.play(str(video), 0)
        self.assertTrue(result["ok"])
        self.assertFalse(any(a.startswith("/seek=") for a in popen.call_args[0][0]))

    def test_play_reports_missing_exe_and_file(self):
        db.set_setting("potplayer_path", str(self.tmp / "missing.exe"))
        video = self.tmp / "Show.mkv"
        video.write_bytes(b"")
        self.assertIn("找不到 PotPlayer", potplayer.play(str(video))["error"])
        exe = self.tmp / "player.exe"
        exe.write_bytes(b"")
        db.set_setting("potplayer_path", str(exe))
        self.assertIn("视频文件不存在", potplayer.play(str(self.tmp / "missing.mkv"))["error"])

    def test_seek_current_without_window_is_false(self):
        with mock.patch.object(potplayer, "find_window", return_value=0):
            self.assertFalse(potplayer.seek_current(1000))


class EmbeddedWindowLookupTests(IsolatedDbTestCase):
    """内嵌后 PotPlayer 变成子窗口，FindWindowW 找不到；必须能从 settings 回查 HWND。"""

    def setUp(self):
        super().setUp()
        potplayer.set_embedded_hwnd(0)

    def tearDown(self):
        potplayer.set_embedded_hwnd(0)
        super().tearDown()

    @staticmethod
    def _user32(*, window_alive=1, class_name="PotPlayer64"):
        u = mock.MagicMock()
        u.FindWindowW.return_value = 0        # 顶层窗口里没有播放器
        u.IsWindow.return_value = window_alive

        def class_of(hwnd, buf, size):
            buf.value = class_name
            return 1
        u.GetClassNameW.side_effect = class_of
        return u

    def test_top_level_window_wins(self):
        u = self._user32()
        u.FindWindowW.return_value = 555
        with mock.patch.object(potplayer, "_user32", u):
            self.assertEqual(potplayer.find_window(), 555)

    def test_reads_hwnd_from_settings_for_cross_process_daemon(self):
        db.set_setting(potplayer.EMBED_HWND_KEY, 4321)
        with mock.patch.object(potplayer, "_user32", self._user32()):
            self.assertEqual(potplayer.find_window(), 4321)

    def test_in_process_cache_also_works(self):
        potplayer.set_embedded_hwnd(8765)
        with mock.patch.object(potplayer, "_user32", self._user32()):
            self.assertEqual(potplayer.find_window(), 8765)
        potplayer.set_embedded_hwnd(0)

    def test_stale_or_foreign_hwnd_is_rejected(self):
        db.set_setting(potplayer.EMBED_HWND_KEY, 4321)
        with mock.patch.object(potplayer, "_user32", self._user32(window_alive=0)):
            self.assertEqual(potplayer.find_window(), 0)
        with mock.patch.object(potplayer, "_user32", self._user32(class_name="Notepad")):
            self.assertEqual(potplayer.find_window(), 0)

    def test_no_window_at_all_returns_zero(self):
        with mock.patch.object(potplayer, "_user32", self._user32()):
            self.assertEqual(potplayer.find_window(), 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
