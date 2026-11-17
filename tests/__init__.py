"""让 tests 成为可发现的包，并在任何用例运行前装好数据库隔离守卫。

铁律：单元测试绝不改动用户真实数据库（``D:/magicvideo/library.db``）。
``IsolatedDbTestCase`` 负责把继承它的用例指到临时库；``db_guard`` 是第二道
防线，拦住任何"忘了继承"的用例，见 ``tests/db_guard.py``。
"""
from . import db_guard

db_guard.install()

