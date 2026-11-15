"""Magic Video 主窗口入口（打包版同时承担 ``--daemon`` 守护入口）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import create_window  # noqa: E402


def _selftest() -> None:
    """打包版自检：确认依赖、资源路径与数据库可用，结果写入数据目录。

    绿色版 / MSI 安装版都可用 ``MAGIC_VIDEO_DATA_DIR`` 把数据指到临时目录后运行，
    不会污染用户真实播放记录。
    """
    import importlib.util
    import json
    from datetime import datetime

    from app import __version__, config, db

    deps = {
        name: bool(importlib.util.find_spec(name))
        for name in ("webview", "clr", "pystray", "PIL", "psutil", "requests")
    }
    try:
        import clr  # noqa: F401  # 真正加载 CLR，验证 .NET 运行时可用
        deps["clr_load"] = True
    except Exception:
        deps["clr_load"] = False

    db.init_db()
    config.ensure_dirs()
    checks = {
        **deps,
        "web_index": (config.WEB_DIR / "index.html").is_file(),
        "dictionary": config.BUILTIN_ALIAS_FILE.is_file(),
        "database": config.DB_PATH.is_file(),
    }
    report = {
        "ok": all(checks.values()),
        "version": __version__,
        "frozen": bool(getattr(sys, "frozen", False)),
        "resource_dir": str(config.RESOURCE_DIR),
        "data_dir": str(config.DATA_DIR),
        "checks": checks,
        "checked_at": datetime.now().isoformat(timespec="seconds"),
    }
    (config.DATA_DIR / "selftest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    if "--selftest" in sys.argv:
        _selftest()
        return
    if "--daemon" in sys.argv:
        from app.daemon import run_daemon
        run_daemon()
        return
    create_window(debug="--debug" in sys.argv)


if __name__ == "__main__":
    main()
