"""单实例保护：任务栏里重复点击图标时只保留一个窗口。

用 Windows 命名互斥体（进程级，随进程退出自动释放），比锁文件更能避免
「崩溃后残留锁文件导致再也起不来」。第二次启动不新建 WebView2，而是把已有窗口
恢复到前台再退出——既解决多窗口，也避免多个进程抢同一份 WebView2 资源。

守护进程（--daemon）与打包自检（--selftest）不走这条路径，不受影响。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

MUTEX_NAME = "Local\\MagicVideo_SingleInstance_v1"
WINDOW_TITLE = "Magic Video · 播放历史"
ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9
SMTO_ABORTIFHUNG = 0x0002

try:  # 非 Windows 平台安全降级：单实例检查直接放行，纯逻辑仍可单测
    _kernel32 = ctypes.windll.kernel32
    _user32 = ctypes.windll.user32
    _kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    _kernel32.CreateMutexW.restype = wintypes.HANDLE
    _kernel32.GetLastError.argtypes = []
    _kernel32.GetLastError.restype = wintypes.DWORD
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
    _user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    _user32.SetForegroundWindow.restype = wintypes.BOOL
except (AttributeError, OSError):  # pragma: no cover - 仅在非 Windows 触发
    _kernel32 = _user32 = None

# 句柄必须在进程生命周期内一直持有，提前 CloseHandle 会让互斥体失效
_held_handle = None


def acquire() -> bool:
    """抢到互斥体返回 True；已有实例返回 False。非 Windows 恒为 True。"""
    global _held_handle
    if _kernel32 is None:
        return True
    _held_handle = _kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return _kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def activate_existing(title: str = WINDOW_TITLE) -> bool:
    """把已运行的主窗口恢复并置前；找不到窗口返回 False。"""
    if _user32 is None:
        return False
    hwnd = _user32.FindWindowW(None, title)
    if not hwnd:
        return False
    try:
        _user32.ShowWindow(hwnd, SW_RESTORE)
        return bool(_user32.SetForegroundWindow(hwnd))
    except Exception:
        return False


def guard() -> bool:
    """入口用：可以启动返回 True；已有实例时激活它并返回 False。"""
    if acquire():
        return True
    activate_existing()
    return False


def is_windows() -> bool:
    return sys.platform == "win32"
