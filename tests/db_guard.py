"""全局数据库隔离守卫：禁止任何单元测试连接用户的真实数据库。

背景：项目既有约定是「涉及数据库的用例继承 ``IsolatedDbTestCase``」，但这是
**软约定**——只要有人忘了继承（例如纯逻辑测试里顺手 ``Api()`` 一下），
``Api.__init__`` 里的 ``db.init_db()`` 就会把用户真实的 ``D:/magicvideo/library.db``
改成新 schema 或写入默认设置。2026-11-17 就发现两处这样的漏网之鱼。

守卫在 ``import tests`` 时安装（见 ``tests/__init__.py``），patch 全局
``sqlite3.connect``：任何目标路径解析到「安装时刻的真实 DB_PATH」的连接
立即抛 ``AssertionError`` 并给出修复提示。这样即使将来有人写了
``class XxxTests(unittest.TestCase): Api()``，也会当场失败而不是悄悄改用户数据。

注意：
- 只在测试进程生效，不影响软件本体；
- 只拦真实库，临时库 / 子进程里 ``MAGIC_VIDEO_DATA_DIR`` 指向的库照常放行；
- ``file:`` URI（含 ``mode=ro``）与 Windows 各种路径写法都会归一化后比较。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlparse

_ORIGINAL_CONNECT = sqlite3.connect
_REAL_DB: Path | None = None
_INSTALLED = False


def real_db_path() -> Path:
    """返回安装守卫时固定的真实库路径。未安装时调用是编程错误。"""
    if _REAL_DB is None:
        raise RuntimeError("db_guard 尚未安装")
    return _REAL_DB


def is_installed() -> bool:
    return _INSTALLED


def _resolve_target(database) -> Path | None:
    """把 sqlite3.connect 的第一个参数归一化成绝对路径；非文件路径返回 None。"""
    if isinstance(database, bytes):
        try:
            database = database.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(database, str) or not database.strip():
        return None
    text = database.strip()
    if text.startswith("file:"):
        parsed = urlparse(text)
        text = unquote(parsed.path)
        # file:///D:/x/y.db → /D:/x/y.db → D:/x/y.db
        if len(text) >= 3 and text[0] == "/" and text[2] == ":":
            text = text[1:]
    if not text or text == ":memory:":
        return None
    try:
        return Path(text).expanduser().resolve()
    except OSError:
        return None


def _guarded_connect(database, *args, **kwargs):
    target = _resolve_target(database)
    if target is not None and _REAL_DB is not None and target == _REAL_DB:
        raise AssertionError(
            f"单元测试禁止连接真实数据库：{target}。\n"
            "修复方式：让测试类继承 tests.base.IsolatedDbTestCase（它会把 "
            "config.DB_PATH 指到临时目录），或在用例里 mock 掉相关写库调用。"
        )
    return _ORIGINAL_CONNECT(database, *args, **kwargs)


def install() -> None:
    """安装守卫（幂等）。真实库路径在首次安装时固定，之后测试改写 config 不影响。"""
    global _REAL_DB, _INSTALLED
    if _INSTALLED:
        return
    from app import config  # 延迟导入：tests/__init__.py 执行时 app 尚未被测试改写

    _REAL_DB = Path(config.DB_PATH).expanduser().resolve()
    sqlite3.connect = _guarded_connect  # type: ignore[assignment]
    _INSTALLED = True


def uninstall() -> None:
    """仅供守卫自身的契约测试使用；生产测试流程中不调用。"""
    global _INSTALLED
    if not _INSTALLED:
        return
    sqlite3.connect = _ORIGINAL_CONNECT  # type: ignore[assignment]
    _INSTALLED = False
