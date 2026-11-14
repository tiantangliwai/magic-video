"""Windows 开机自启：只写当前用户的 HKCU Run 项，不需要管理员权限。"""
from __future__ import annotations

import sys
from pathlib import Path

try:  # 非 Windows 平台导入不报错，相关接口安全降级
    import winreg
except ImportError:  # pragma: no cover
    winreg = None

from . import config, db

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
ENTRY_NAME = "MagicVideoDaemon"


def _pythonw() -> Path:
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return candidate if candidate.exists() else Path(sys.executable)


def command() -> str:
    return f'"{_pythonw()}" "{config.APP_DIR / "daemon.py"}"'


def is_enabled() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, ENTRY_NAME)
            return bool(value)
    except OSError:
        return False


def enable() -> dict:
    if winreg is None:
        return {"ok": False, "error": "开机自启仅支持 Windows"}
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, ENTRY_NAME, 0, winreg.REG_SZ, command())
        db.init_db()
        db.set_setting("autostart", 1)
        return {"ok": True, "command": command()}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def disable() -> dict:
    if winreg is None:
        return {"ok": False, "error": "开机自启仅支持 Windows"}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_WRITE) as key:
            winreg.DeleteValue(key, ENTRY_NAME)
    except FileNotFoundError:
        pass
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    db.init_db()
    db.set_setting("autostart", 0)
    return {"ok": True}


def status() -> dict:
    return {"enabled": is_enabled(), "command": command()}
