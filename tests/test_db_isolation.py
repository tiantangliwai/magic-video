"""数据库隔离守护测试：确保任何单元测试都不会改动用户真实数据库。

三道防线：
1. db_guard：运行期拦截到真实库的连接（见 tests/db_guard.py）；
2. IsolatedDbTestCase：继承者自动把 config 指到临时目录；
3. 本文件的静态契约：扫描所有 test_*.py，任何"碰数据库"的类必须继承隔离基类，
   防止将来有人新增 ``class XxxTests(unittest.TestCase)`` 里 ``Api()`` 一下又写真实库。
"""
from __future__ import annotations

import ast
import sqlite3
import tempfile
import unittest
from pathlib import Path

from app import config

from . import db_guard
from .base import IsolatedDbTestCase

TESTS_DIR = Path(__file__).resolve().parent

# 这些调用一旦落到真实库就会改数据；出现在非隔离类里视为违规。
_WRITE_CALLS = {
    "execute", "executemany", "set_setting", "init_db", "get_conn", "session",
    "checkpoint", "connect", "insert", "update", "delete", "commit", "executescript",
}
# Api()/Tray() 构造内部就是 db.init_db()，等价于写库。
_DB_CLIENTS = {"Api", "Tray"}
_WRITE_PREFIXES = ("db.", "sqlite3.", "api.db.", "self.api.")


def _load_classes():
    """返回 {模块文件: [(类名, 直接基类列表, 类节点)]}。"""
    result = {}
    for path in sorted(TESTS_DIR.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = []
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                bases = [ast.unparse(b) for b in node.bases]
                classes.append((node.name, bases, node))
        result[path] = classes
    return result


def _inherits_isolated(name: str, bases_by_class: dict[str, list[str]], seen=None) -> bool:
    """递归判定某个类是否（传递地）继承 IsolatedDbTestCase。"""
    seen = seen or set()
    if name in seen:
        return False
    seen.add(name)
    for base in bases_by_class.get(name, []):
        short = base.split(".")[-1]
        if short == "IsolatedDbTestCase":
            return True
        if _inherits_isolated(short, bases_by_class, seen):
            return True
    return False


def _db_calls(node: ast.ClassDef):
    """返回类体内所有可疑数据库调用的行号与源码文本。"""
    hits = []
    for child in ast.walk(node):
        if not isinstance(child, ast.Call):
            continue
        func = ast.unparse(child.func)
        short = func.split(".")[-1]
        is_client = short in _DB_CLIENTS and func in _DB_CLIENTS
        is_db_write = (
            short in _WRITE_CALLS
            and (func.startswith(_WRITE_PREFIXES) or short == "connect")
        )
        if is_client or is_db_write:
            hits.append((child.lineno, func))
    return hits


class GuardRuntimeTests(unittest.TestCase):
    """守卫本身必须在运行期生效：拦真实库、放行临时库。"""

    def test_guard_is_installed(self):
        self.assertTrue(db_guard.is_installed())

    def test_real_db_connection_is_blocked(self):
        real = db_guard.real_db_path()
        with self.assertRaises(AssertionError) as ctx:
            sqlite3.connect(str(real))
        self.assertIn("禁止连接真实数据库", str(ctx.exception))

    def test_real_db_uri_form_is_blocked(self):
        real = db_guard.real_db_path().as_posix()
        with self.assertRaises(AssertionError):
            sqlite3.connect(f"file:{real}?mode=ro", uri=True)

    def test_temp_db_connection_is_allowed(self):
        with tempfile.TemporaryDirectory(prefix="mv-guard-test-") as tmp:
            path = Path(tmp) / "scratch.db"
            conn = sqlite3.connect(str(path))
            try:
                conn.execute("CREATE TABLE t(x INTEGER)")
                conn.commit()
            finally:
                conn.close()
            self.assertTrue(path.exists())

    def test_real_db_path_is_the_configured_one(self):
        # 守卫固定的必须是"未改写前"的真实库，不是某个临时库。
        self.assertEqual(db_guard.real_db_path(), Path(config.DEFAULT_DATA_DIR) / "library.db")


class IsolationContractTests(IsolatedDbTestCase):
    """继承隔离基类的用例，自己必须真的落在临时库上。"""

    def test_db_path_points_into_temp_dir(self):
        self.assertEqual(Path(config.DB_PATH).resolve(), (self.tmp / "library.db").resolve())
        self.assertNotEqual(Path(config.DB_PATH).resolve(), db_guard.real_db_path())

    def test_writing_temp_db_does_not_touch_real_db(self):
        before = db_guard.real_db_path().stat().st_mtime_ns if db_guard.real_db_path().exists() else None
        self.media("Isolation Probe.mkv")
        after = db_guard.real_db_path().stat().st_mtime_ns if db_guard.real_db_path().exists() else None
        self.assertEqual(before, after, "临时库写操作不应改变真实库 mtime")


class StaticContractTests(unittest.TestCase):
    """静态扫描：碰数据库的测试类必须继承隔离基类。"""

    def test_db_touching_classes_all_inherit_isolated_base(self):
        classes = _load_classes()
        bases_by_class = {
            name: bases
            for _, items in classes.items()
            for name, bases, _ in items
        }
        violations = []
        for path, items in classes.items():
            # 本文件就是验证守卫的，故意调用 sqlite3.connect 正是不变量本身，豁免。
            if path.name == "test_db_isolation.py":
                continue
            for name, bases, node in items:
                if _inherits_isolated(name, bases_by_class):
                    continue
                for lineno, func in _db_calls(node):
                    violations.append(f"{path.name}:{lineno} {name}.{func}")
        self.assertEqual(violations, [],
                         "以下测试类碰数据库却没继承 IsolatedDbTestCase：\n" + "\n".join(violations))

    def test_two_known_offenders_now_inherit_isolated_base(self):
        classes = _load_classes()
        bases_by_class = {
            name: bases
            for _, items in classes.items()
            for name, bases, _ in items
        }
        for file_name, class_name in (
            ("test_startup.py", "JsApiReflectionTests"),
            ("test_packaging.py", "FrozenIntegrationTests"),
        ):
            path = TESTS_DIR / file_name
            bases = bases_by_class.get(class_name, [])
            self.assertTrue(
                _inherits_isolated(class_name, bases_by_class),
                f"{path}:{class_name} 仍继承 {bases}，必须继承 IsolatedDbTestCase",
            )

    def test_both_runners_install_guard(self):
        for name in ("run_tests.py", "run_changed_tests.py"):
            source = (TESTS_DIR.parent / name).read_text(encoding="utf-8")
            self.assertIn("db_guard.install()", source, f"{name} 没有安装数据库隔离守卫")


if __name__ == "__main__":
    unittest.main()
