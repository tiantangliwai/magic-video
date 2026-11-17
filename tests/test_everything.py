"""Everything 快速检索：三级降级、LIKE 转义、短超时、结果结构。"""
from __future__ import annotations

from unittest import mock

from app import everything
from app.api import Api
from tests.base import IsolatedDbTestCase


class EverythingDegradeTests(IsolatedDbTestCase):

    def setUp(self):
        super().setUp()
        self.api = Api()
        self.media("Frieren - 01.mkv", title_cn="葬送的芙莉莲")
        self.media("Frieren - 02.mkv", title_cn="葬送的芙莉莲")
        self.media("Other Show - 01.mp4", title_cn="别的作品")

    def test_library_fallback_when_no_es_and_no_http(self):
        with mock.patch.object(everything, "es_exe", return_value=self.tmp / "missing.exe"), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("Frieren")
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "library")
        self.assertTrue(r["degraded"])
        self.assertEqual(len(r["rows"]), 2)
        self.assertTrue(all(x["in_library"] and x["file_id"] for x in r["rows"]))

    def test_search_by_chinese_title(self):
        with mock.patch.object(everything, "es_exe", return_value=self.tmp / "missing.exe"), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("葬送")
        self.assertEqual(len(r["rows"]), 2)

    def test_es_output_parsed_and_marked_in_library(self):
        fake = mock.Mock()
        fake.is_file.return_value = True
        path = str(self.tmp / "Frieren - 01.mkv")
        with mock.patch.object(everything, "es_exe", return_value=fake), \
             mock.patch.object(everything.subprocess, "run", return_value=mock.Mock(
                 returncode=0, stdout=path + "\nD:\\outside\\Another - 03.mkv\nnotavideo.txt\n")):
            r = everything.search("Frieren")
        self.assertEqual(r["source"], "everything-es")
        self.assertEqual(len(r["rows"]), 2)
        self.assertTrue(r["rows"][0]["in_library"])
        self.assertFalse(r["rows"][1]["in_library"])

    def test_es_gbk_bytes_decode_to_chinese_path(self):
        """es.exe 在中文 Windows 输出 GBK bytes；中文路径必须正确解码，不能变乱码。"""
        path = "G:\\动漫\\从零开始的异世界生活\\[DMG]S01 - 01.mkv"
        fake = mock.Mock(); fake.is_file.return_value = True
        proc = mock.Mock(returncode=0, stdout=(path + "\n").encode("gbk"), stderr=b"")
        with mock.patch.object(everything, "es_exe", return_value=fake), \
             mock.patch.object(everything.subprocess, "run", return_value=proc):
            r = everything.search("Re:Zero")
        self.assertEqual(r["source"], "everything-es")
        self.assertEqual(r["rows"][0]["filename"], "[DMG]S01 - 01.mkv")
        self.assertEqual(r["rows"][0]["path"], path)
        self.assertIn("动漫", r["rows"][0]["path"])  # 不是 replace 出来的 ?

    def test_es_utf8_bytes_also_decode(self):
        """已是 UTF-8 的输出（纯 ASCII / 英文）也要正常解码，不能强制 GBK。"""
        self.assertEqual(everything._decode_output("plain str"), "plain str")
        self.assertEqual(everything._decode_output(b"D:\\videos\\Show - 01.mkv"),
                         "D:\\videos\\Show - 01.mkv")

    def test_no_hit_when_channel_ok_is_not_reported_as_channel_down(self):
        """es.exe 正常执行但关键词没在整机文件名命中：不能报『通道暂时不可用』。"""
        fake = mock.Mock(); fake.is_file.return_value = True
        # stdout 为空但 returncode=0 → es_error='' → 通道正常、0 命中
        with mock.patch.object(everything, "es_exe", return_value=fake), \
             mock.patch.object(everything.subprocess, "run",
                               return_value=mock.Mock(returncode=0, stdout=b"", stderr=b"")), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("一定搜不到的词")
        self.assertEqual(r["source"], "library")
        self.assertTrue(r["channel_ok"])
        self.assertFalse(r["degraded"], "通道正常只是没命中，不该标记为降级/不可用")
        self.assertIn("未找到匹配", r["hint"])
        self.assertIn("通道正常", r["hint"])
        self.assertNotIn("通道暂时不可用", r["hint"])

    def test_channel_really_down_still_reports_degraded(self):
        """通道真失败（es 超时且无 HTTP）时仍要报降级，并说明原因。"""
        import subprocess
        fake = mock.Mock(); fake.is_file.return_value = True
        with mock.patch.object(everything, "es_exe", return_value=fake), \
             mock.patch.object(everything.subprocess, "run",
                                side_effect=subprocess.TimeoutExpired("es", 6)), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("Frieren")
        self.assertEqual(r["source"], "library")
        self.assertFalse(r["channel_ok"])
        self.assertTrue(r["degraded"])
        self.assertIn("通道暂时不可用", r["hint"])

    def test_es_timeout_degrades_to_library(self):
        import subprocess
        fake = mock.Mock(); fake.is_file.return_value = True
        with mock.patch.object(everything, "es_exe", return_value=fake), \
             mock.patch.object(everything.subprocess, "run",
                                side_effect=subprocess.TimeoutExpired("es", 6)), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("Frieren")
        self.assertEqual(r["source"], "library")
        self.assertTrue(r["degraded"])

    def test_http_results_parsed(self):
        resp = mock.Mock(status_code=200)
        resp.json.return_value = {"results": [
            {"path": "D:\\videos", "name": "Spy x Family - 01.mkv"},
            {"path": "D:\\videos", "name": "readme.txt"},
        ]}
        with mock.patch.object(everything, "es_exe", return_value=self.tmp / "missing.exe"), \
             mock.patch.object(everything, "http_base", return_value="http://127.0.0.1:8080"), \
             mock.patch.object(everything.requests, "get", return_value=resp):
            r = everything.search("Spy")
        self.assertEqual(r["source"], "everything-http")
        self.assertEqual(len(r["rows"]), 1)

    def test_like_wildcards_are_escaped(self):
        with mock.patch.object(everything, "es_exe", return_value=self.tmp / "missing.exe"), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("%_\\")
        self.assertTrue(r["ok"])
        self.assertEqual(r["rows"], [])  # 不能因通配符匹配出全部文件

    def test_empty_query_rejected(self):
        r = everything.search("   ")
        self.assertFalse(r["ok"])

    def test_limit_is_clamped(self):
        with mock.patch.object(everything, "es_exe", return_value=self.tmp / "missing.exe"), \
             mock.patch.object(everything, "http_base", return_value=""):
            r = everything.search("Frieren", limit=99999)
        self.assertTrue(r["ok"])

    def test_api_exposes_search_and_capabilities(self):
        self.assertTrue(hasattr(self.api, "everything_search"))
        self.assertTrue(hasattr(self.api, "everything_capabilities"))


if __name__ == "__main__":
    unittest.main()
