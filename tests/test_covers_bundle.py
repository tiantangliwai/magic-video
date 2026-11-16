"""随包公共封面资产（assets/covers/）：清单完整性 + 运行期离线兜底。

两条底线：

- 清单里每一条都必须真能对应到仓库里的文件，且 sha256 与体积对得上，
  否则 PR 改图不更新哈希时会静默漂移。
- 运行期只做「资产 → 数据目录」单向复制：本机已有缓存优先，资产坏了要能
  安安静静地忽略，绝不能影响抓取与本地封面。
"""
from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from app import config, covers, db
from tests.base import IsolatedDbTestCase

ROOT = Path(__file__).resolve().parent.parent
ASSET_DIR = ROOT / "assets" / "covers"
INDEX_PATH = ASSET_DIR / "index.json"
JPEG = b"\xff\xd8\xff" + b"\x00" * 700


class BundleIndexTests(unittest.TestCase):
    """仓库里的封面资产本身必须是自洽的（提 PR 的人不必跑应用也能发现错）。"""

    @classmethod
    def setUpClass(cls):
        cls.index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))

    def test_index_has_schema_and_count(self):
        self.assertEqual(self.index.get("schema"), 1)
        covers_list = self.index.get("covers") or []
        self.assertGreater(len(covers_list), 0)
        self.assertEqual(self.index.get("count"), len(covers_list))

    def test_every_entry_maps_to_a_real_file(self):
        for item in self.index["covers"]:
            name = item.get("file") or ""
            self.assertTrue(name, "清单条目缺 file")
            path = ASSET_DIR / name
            self.assertTrue(path.is_file(), f"清单指向的图片不存在：{name}")
            self.assertGreaterEqual(path.stat().st_size, 512, f"图片过小：{name}")

    def test_sha256_and_bytes_match_the_file(self):
        for item in self.index["covers"]:
            path = ASSET_DIR / item["file"]
            data = path.read_bytes()
            self.assertEqual(item.get("sha256"), hashlib.sha256(data).hexdigest(),
                             f"sha256 与文件不符（改图后没跑 --refresh-index？）：{item['file']}")
            self.assertEqual(item.get("bytes"), len(data),
                             f"bytes 与文件不符：{item['file']}")

    def test_titles_are_present_and_files_unique(self):
        seen = set()
        for item in self.index["covers"]:
            self.assertTrue(str(item.get("title") or "").strip(), f"缺标题：{item.get('file')}")
            self.assertNotIn(item["file"], seen, f"文件名重复：{item['file']}")
            seen.add(item["file"])

    def test_manual_covers_stay_out_of_the_shared_bundle(self):
        """手工封面是个人选择，混进公共资产会让「清除封面」删不掉。"""
        for item in self.index["covers"]:
            self.assertNotEqual(str(item.get("source") or ""), "manual", item["file"])

    def test_build_dist_ships_the_assets(self):
        """打包脚本必须把 assets/ 一起带进安装包，否则新装仍然没封面。"""
        build = (ROOT / "build_dist.py").read_text(encoding="utf-8")
        self.assertIn("'assets'", build)
        self.assertIn("--add-data", build)


class BundledFallbackTests(IsolatedDbTestCase):
    """本机缓存缺图时，从随包资产补一份；资产异常时静默降级。"""

    def _make_bundle(self, entries: list[dict], files: dict[str, bytes]) -> None:
        config.BUNDLED_COVER_DIR.mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            (config.BUNDLED_COVER_DIR / name).write_bytes(data)
        (config.BUNDLED_COVER_DIR / "index.json").write_text(
            json.dumps({"schema": 1, "count": len(entries), "covers": entries},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        covers._BUNDLED_CACHE.update(stamp=None, entries=[], files={})

    def test_resolve_hits_bundle_and_materialises_into_cache_dir(self):
        self._make_bundle(
            [{"file": "shared01.jpg", "title": "葬送的芙莉莲",
              "aliases": ["Sousou no Frieren"], "source": "agedm"}],
            {"shared01.jpg": JPEG},
        )
        name = covers.resolve_cover("葬送的芙莉莲")
        self.assertEqual(name, "shared01.jpg")
        copied = config.COVER_DIR / "shared01.jpg"
        self.assertTrue(copied.is_file(), "命中资产后应复制到本机封面目录")
        self.assertEqual(copied.read_bytes()[:3], b"\xff\xd8\xff")

    def test_alias_also_resolves(self):
        self._make_bundle(
            [{"file": "shared02.jpg", "title": "葬送的芙莉莲",
              "aliases": ["Sousou no Frieren"], "source": "agedm"}],
            {"shared02.jpg": JPEG},
        )
        self.assertEqual(covers.resolve_cover("Sousou no Frieren"), "shared02.jpg")

    def test_local_cache_wins_over_bundle(self):
        """本机抓到的图比随包的老图新，不能被资产覆盖。"""
        self._make_bundle(
            [{"file": "shared03.jpg", "title": "葬送的芙莉莲", "source": "agedm"}],
            {"shared03.jpg": b"\xff\xd8\xff" + b"\x11" * 700},
        )
        config.COVER_DIR.mkdir(parents=True, exist_ok=True)
        local = config.COVER_DIR / "shared03.jpg"
        local.write_bytes(b"\xff\xd8\xff" + b"\x22" * 700)
        self.assertTrue(covers._cover_file_ok("shared03.jpg"))
        self.assertEqual(local.read_bytes()[3:4], b"\x22", "本机文件被资产覆盖了")

    def test_missing_index_degrades_silently(self):
        config.BUNDLED_COVER_DIR.mkdir(parents=True, exist_ok=True)
        covers._BUNDLED_CACHE.update(stamp=None, entries=[], files={})
        self.assertEqual(covers.bundled_covers(), [])
        self.assertEqual(covers.resolve_cover("任何标题"), "")

    def test_broken_index_degrades_silently(self):
        config.BUNDLED_COVER_DIR.mkdir(parents=True, exist_ok=True)
        (config.BUNDLED_COVER_DIR / "index.json").write_text("{不是 JSON", encoding="utf-8")
        covers._BUNDLED_CACHE.update(stamp=None, entries=[], files={})
        self.assertEqual(covers.bundled_covers(), [])
        self.assertEqual(covers.resolve_cover("任何标题"), "")

    def test_entry_without_file_is_not_claimed(self):
        """清单里有、图片被删了：不能假装有封面。"""
        self._make_bundle(
            [{"file": "gone.jpg", "title": "某作品", "source": "agedm"}],
            {},
        )
        self.assertEqual(covers.resolve_cover("某作品"), "")

    def test_db_entries_come_before_bundle(self):
        db.execute(
            "INSERT INTO subjects(title_cn, cover_local, source) VALUES (?, ?, 'manual')",
            ("葬送的芙莉莲", "mine.jpg"),
        )
        config.COVER_DIR.mkdir(parents=True, exist_ok=True)
        (config.COVER_DIR / "mine.jpg").write_bytes(JPEG)
        self._make_bundle(
            [{"file": "shared04.jpg", "title": "葬送的芙莉莲", "source": "agedm"}],
            {"shared04.jpg": JPEG},
        )
        self.assertEqual(covers.resolve_cover("葬送的芙莉莲"), "mine.jpg")


class ClearedCoverTests(IsolatedDbTestCase):
    """点过「清除封面」之后，公共资产不能再把同名图补回来。"""

    def _bundle(self, title: str, name: str) -> None:
        config.BUNDLED_COVER_DIR.mkdir(parents=True, exist_ok=True)
        (config.BUNDLED_COVER_DIR / name).write_bytes(JPEG)
        (config.BUNDLED_COVER_DIR / "index.json").write_text(
            json.dumps({"schema": 1, "count": 1,
                        "covers": [{"file": name, "title": title, "source": "agedm"}]},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        covers._BUNDLED_CACHE.update(stamp=None, entries=[], files={})

    def test_cleared_cover_is_not_resurrected_by_bundle(self):
        self._bundle("葬送的芙莉莲", "shared05.jpg")
        self.assertEqual(covers.resolve_cover("葬送的芙莉莲"), "shared05.jpg")
        # 用户手选后又清除：文件删掉 + 记墓碑
        covers.set_manual_cover("葬送的芙莉莲", JPEG)
        covers.clear_manual_cover("葬送的芙莉莲")
        self.assertEqual(covers.resolve_cover("葬送的芙莉莲"), "",
                         "清除封面后不能又被公共资产补回来")

    def test_saving_a_cover_lifts_the_tombstone(self):
        self._bundle("葬送的芙莉莲", "shared06.jpg")
        covers.clear_manual_cover("葬送的芙莉莲")
        self.assertEqual(covers.resolve_cover("葬送的芙莉莲"), "")
        covers.set_manual_cover("葬送的芙莉莲", JPEG)
        name = covers.resolve_cover("葬送的芙莉莲")
        self.assertTrue(name, "重新设定封面后必须能解析到图")
        self.assertTrue((config.COVER_DIR / name).is_file())


if __name__ == "__main__":
    unittest.main()
