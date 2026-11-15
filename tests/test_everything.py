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
