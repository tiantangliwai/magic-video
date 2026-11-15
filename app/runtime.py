"""运行形态与进程启动命令：源码版与 PyInstaller 打包版共用。

打包版只有一个可执行文件，托盘守护通过 ``--daemon`` 参数复用同一个 exe；
源码版仍用 ``pythonw.exe + daemon.py / run.py``。
"""
from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包出的可执行文件里。"""
    return bool(getattr(sys, "frozen", False))


def executable() -> Path:
    return Path(sys.executable).resolve()


def source_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _pythonw() -> Path:
    candidate = executable().with_name("pythonw.exe")
    return candidate if candidate.exists() else executable()


def main_command() -> list[str]:
    """打开主窗口的命令。"""
    if is_frozen():
        return [str(executable())]
    return [str(_pythonw()), str(source_root() / "run.py")]


def daemon_command() -> list[str]:
    """启动托盘守护进程的命令。"""
    if is_frozen():
        return [str(executable()), "--daemon"]
    return [str(_pythonw()), str(source_root() / "daemon.py")]


def command_line(parts: list[str]) -> str:
    """把命令数组转成注册表 Run 项可用的字符串（逐段加引号）。"""
    return " ".join(f'"{part}"' for part in parts)
