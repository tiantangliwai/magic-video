"""PotPlayer 软件内嵌播放（可降级）。

思路：不碰 pywebview 的 .NET 控件树（``window.native`` 会在非 UI 线程抛
E_NOINTERFACE），只用纯 Win32：按进程 PID + 标题找到主窗口 HWND，再用
``SetParent`` 把 PotPlayer 变成主窗口的子窗口，按前端给出的 CSS 矩形摆位。

三条硬约束：
1. **任何一步失败都自动降级为原来的外部播放**，绝不因为内嵌失败导致点播放没反应；
2. 内嵌后 PotPlayer 不再是顶层窗口，``FindWindowW`` 找不到它，所以把 HWND
   写进 ``settings.embed_hwnd``，让**另一个进程**的守护记录器也能继续读进度；
3. 退出 / 关闭主窗口时必须 ``detach()`` 还原，绝不留下没有父窗口的
   ``WS_CHILD`` 孤儿窗口。

这是一次性用户动作（点击播放），不是定时任务；模块内只有「等窗口出现」的
短轮询，没有常驻后台线程。
"""
from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes

from . import db, potplayer

# ---- Win32 常量 ----
GWL_STYLE = -16
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_CAPTION = 0x00C00000
WS_THICKFRAME = 0x00040000
WS_SYSMENU = 0x00080000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000
WS_VISIBLE = 0x10000000
_STYLE_REMOVE = WS_POPUP | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX

SWP_FRAMECHANGED = 0x0020
SWP_SHOWWINDOW = 0x0040
SWP_NOACTIVATE = 0x0010
HWND_TOP = 0
SW_SHOW = 5

EMBED_HWND_KEY = "embed_hwnd"
MAIN_TITLE_HINT = "Magic Video"
DEFAULT_RECT = (0, 0, 960, 540)
_POLL_INTERVAL = 0.15

try:  # 非 Windows 也能安全导入（纯函数与单测仍可用）
    _user32 = ctypes.windll.user32
    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _user32.EnumWindows.argtypes = [_WNDENUMPROC, wintypes.LPARAM]
    _user32.EnumWindows.restype = wintypes.BOOL
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    _user32.IsWindowVisible.argtypes = [wintypes.HWND]
    _user32.IsWindowVisible.restype = wintypes.BOOL
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = wintypes.LONG
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
    _user32.SetWindowLongW.restype = wintypes.LONG
    _user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    _user32.SetParent.restype = wintypes.HWND
    _user32.GetParent.argtypes = [wintypes.HWND]
    _user32.GetParent.restype = wintypes.HWND
    _user32.SetWindowPos.argtypes = [
        wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
        ctypes.c_int, ctypes.c_int, wintypes.UINT,
    ]
    _user32.SetWindowPos.restype = wintypes.BOOL
    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL
    _user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.ShowWindow.restype = wintypes.BOOL
except (AttributeError, OSError, ValueError):  # pragma: no cover - 仅在非 Windows 触发
    _user32 = None
    _WNDENUMPROC = None

HAS_WIN32 = bool(_user32 is not None and sys.platform == "win32")

_STATE = {
    "attached": False,
    "pot_hwnd": 0,
    "main_hwnd": 0,
    "orig_style": 0,
    "orig_parent": 0,
    "orig_rect": DEFAULT_RECT,
    "rect": DEFAULT_RECT,
    "path": "",
    "reason": "",
}
_LOCK = threading.RLock()


# ---------------------------------------------------------------- 纯函数 / 小工具

def rect_from_css(rect, dpr: float | None = None) -> tuple[int, int, int, int]:
    """前端 ``getBoundingClientRect()`` 的 CSS 像素 → Win32 物理像素 ``(x,y,w,h)``。

    WebView2 的内容区与主窗口客户区基本重合（无菜单栏 / 边框偏移），
    所以这里只做 DPR 缩放与合法性裁剪；异常输入一律退回默认矩形，
    绝不把异常值传给 SetWindowPos。
    """
    if not isinstance(rect, dict):
        return DEFAULT_RECT
    try:
        scale = float(rect.get("dpr", dpr) or 1.0)
        if not 0.5 <= scale <= 4.0:
            scale = 1.0
        x = int(round(float(rect.get("x", rect.get("left", 0)) or 0) * scale))
        y = int(round(float(rect.get("y", rect.get("top", 0)) or 0) * scale))
        w = int(round(float(rect.get("w", rect.get("width", 0)) or 0) * scale))
        h = int(round(float(rect.get("h", rect.get("height", 0)) or 0) * scale))
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RECT
    x, y = max(0, min(x, 10000)), max(0, min(y, 10000))
    w, h = max(200, min(w, 10000)), max(150, min(h, 10000))
    return x, y, w, h


def _as_long(value: int) -> int:
    """Win32 LONG 是有符号 32 位；含 WS_POPUP(0x80000000) 的样式要先折算，避免 ctypes 溢出。"""
    value = int(value) & 0xFFFFFFFF
    return value if value < 0x80000000 else value - 0x100000000


def _class_name(hwnd: int) -> str:
    if not HAS_WIN32 or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    try:
        _user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    try:
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return DEFAULT_RECT
        return (int(rect.left), int(rect.top),
                max(320, int(rect.right) - int(rect.left)),
                max(240, int(rect.bottom) - int(rect.top)))
    except Exception:
        return DEFAULT_RECT


def _remember_hwnd(hwnd: int) -> None:
    """把 HWND 写进 settings：守护进程是独立进程，靠这个值继续读播放进度。"""
    try:
        db.set_setting(EMBED_HWND_KEY, int(hwnd or 0))
    except Exception:
        pass  # 数据库暂不可用时不影响本次播放，只是后台记录会退回外部窗口查找


def find_main_window() -> int:
    """按 PID + 标题找 pywebview 主窗口；不访问 ``window.native``，避免 COM 反射。"""
    if not HAS_WIN32:
        return 0
    pid, hits = os.getpid(), []

    def collect(hwnd, _lparam):
        owner = wintypes.DWORD()
        try:
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if int(owner.value) != pid or not _user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 256)
            if MAIN_TITLE_HINT in (buf.value or ""):
                hits.append(int(hwnd))
                return False
        except Exception:
            pass
        return True

    try:
        _user32.EnumWindows(_WNDENUMPROC(collect), 0)
    except Exception:
        return 0
    return hits[0] if hits else 0


# ---------------------------------------------------------------- 嵌入 / 还原

def _restore(hwnd: int) -> None:
    """把子窗口还原成原来的独立顶层窗口；异常也吞掉，保证状态一定被清掉。"""
    try:
        _user32.SetParent(hwnd, 0)
        _user32.SetWindowLongW(hwnd, GWL_STYLE, _as_long(_STATE["orig_style"]))
        x, y, w, h = _STATE["orig_rect"]
        _user32.SetWindowPos(hwnd, HWND_TOP, x, y, w, h,
                             SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE)
        _user32.ShowWindow(hwnd, SW_SHOW)
    except Exception:
        pass
    potplayer.set_embedded_hwnd(0)
    _remember_hwnd(0)


def attach(pot_hwnd: int, main_hwnd: int, rect: tuple[int, int, int, int]) -> dict:
    """把 PotPlayer 变成主窗口子窗口并摆到 rect。失败返回 ``ok=False`` + 原因。"""
    if not HAS_WIN32:
        return {"ok": False, "error": "当前系统不支持 Win32 窗口嵌入"}
    pot_hwnd, main_hwnd = int(pot_hwnd or 0), int(main_hwnd or 0)
    if not pot_hwnd or not main_hwnd:
        return {"ok": False, "error": "缺少播放器或主窗口句柄"}
    if not _user32.IsWindow(pot_hwnd) or not _user32.IsWindow(main_hwnd):
        return {"ok": False, "error": "窗口句柄已失效"}

    with _LOCK:
        if _STATE["attached"] and _STATE["pot_hwnd"] == pot_hwnd:
            _STATE["rect"] = rect
            try:
                _user32.SetWindowPos(pot_hwnd, HWND_TOP, *rect,
                                    SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE)
            except Exception:
                pass
            return {"ok": True, "reused": True, "hwnd": pot_hwnd, "rect": rect}
        if _STATE["attached"]:
            _restore(_STATE["pot_hwnd"])

        orig_style = int(_user32.GetWindowLongW(pot_hwnd, GWL_STYLE))
        orig_parent = int(_user32.GetParent(pot_hwnd) or 0)
        orig_rect = _window_rect(pot_hwnd)
        new_style = (orig_style | WS_CHILD | WS_VISIBLE) & ~_STYLE_REMOVE

        try:
            _user32.SetWindowLongW(pot_hwnd, GWL_STYLE, _as_long(new_style))
            _user32.SetParent(pot_hwnd, main_hwnd)
            if int(_user32.GetParent(pot_hwnd) or 0) != main_hwnd:
                raise OSError("SetParent 未生效")
            moved = _user32.SetWindowPos(
                pot_hwnd, HWND_TOP, *rect,
                SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            if not moved:
                raise OSError("SetWindowPos 失败")
        except Exception as exc:
            _STATE.update(orig_style=orig_style, orig_rect=orig_rect)
            _restore(pot_hwnd)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        _STATE.update(
            attached=True, pot_hwnd=pot_hwnd, main_hwnd=main_hwnd,
            orig_style=orig_style, orig_parent=orig_parent, orig_rect=orig_rect,
            rect=rect, reason="",
        )
        potplayer.set_embedded_hwnd(pot_hwnd)
        _remember_hwnd(pot_hwnd)
        return {"ok": True, "hwnd": pot_hwnd, "rect": rect}


def detach() -> dict:
    """退出内嵌：还原 PotPlayer 为独立窗口，清理跨进程 HWND 缓存。"""
    with _LOCK:
        if not _STATE["attached"]:
            return {"ok": True, "was_attached": False}
        hwnd = _STATE["pot_hwnd"]
        _restore(hwnd)
        _STATE.update(attached=False, pot_hwnd=0, main_hwnd=0, rect=DEFAULT_RECT,
                      orig_style=0, orig_parent=0, orig_rect=DEFAULT_RECT, path="", reason="")
        return {"ok": True, "was_attached": True}


def sync(rect: tuple[int, int, int, int]) -> dict:
    """窗口大小 / DPI 变化时重新摆放已嵌入的播放器。"""
    with _LOCK:
        if not _STATE["attached"]:
            return {"ok": False, "error": "当前没有内嵌播放器"}
        hwnd = _STATE["pot_hwnd"]
        if not _user32.IsWindow(hwnd):
            detach()
            return {"ok": False, "error": "播放器窗口已关闭"}
        _STATE["rect"] = rect
        try:
            ok = _user32.SetWindowPos(
                hwnd, HWND_TOP, *rect,
                SWP_FRAMECHANGED | SWP_SHOWWINDOW | SWP_NOACTIVATE,
            )
            return {"ok": bool(ok), "hwnd": hwnd, "rect": rect}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------- 对外播放入口

def _wait_pot_window(timeout: float) -> int:
    """PotPlayer 是异步启动的，短轮询等它的窗口出现；超时交给调用方降级。"""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        hwnd = potplayer.find_window()
        if hwnd:
            return hwnd
        if time.monotonic() >= deadline:
            return 0
        time.sleep(_POLL_INTERVAL)


def play(path: str, position_ms: int = 0, rect: dict | None = None,
         *, wait_window: float = 4.0) -> dict:
    """在软件内播放；任一环节失败自动降级为原来的外部播放（播放照常开始）。

    返回 ``{ok, embedded, fallback, reason, ...}``；``embedded=False`` 时
    PotPlayer 已经在独立窗口里正常播放，不是错误。
    """
    px_rect = rect_from_css(rect)
    started = potplayer.play(path, position_ms)
    if not started.get("ok"):
        return {**started, "embedded": False, "fallback": False}

    main_hwnd = find_main_window()
    if not main_hwnd:
        reason = "找不到主窗口句柄，已自动切回独立窗口播放"
    else:
        pot_hwnd = _wait_pot_window(wait_window)
        if not pot_hwnd:
            reason = "未检测到 PotPlayer 窗口，已自动切回独立窗口播放"
        else:
            result = attach(pot_hwnd, main_hwnd, px_rect)
            if result.get("ok"):
                with _LOCK:
                    _STATE["path"] = str(path)
                return {
                    "ok": True, "embedded": True, "fallback": False,
                    "hwnd": pot_hwnd, "rect": list(px_rect),
                    "position_ms": position_ms, "args": started.get("args"),
                }
            detach()
            reason = f"内嵌失败（{result.get('error')}），已自动切回独立窗口播放"

    return {"ok": True, "embedded": False, "fallback": True,
            "reason": reason, "position_ms": position_ms, "args": started.get("args")}


def status() -> dict:
    """供前端轮询的轻量状态；不触发 EnumWindows。"""
    with _LOCK:
        snap = {
            "available": bool(HAS_WIN32),
            "attached": bool(_STATE["attached"]),
            "hwnd": int(_STATE["pot_hwnd"]),
            "rect": list(_STATE["rect"]),
            "path": _STATE["path"],
            "reason": _STATE["reason"],
        }
    if snap["attached"] and HAS_WIN32 and not _user32.IsWindow(snap["hwnd"]):
        detach()  # 用户自己关掉了播放器，顺手把状态清理干净
        snap.update(attached=False, hwnd=0)
    return snap


def cleanup() -> dict:
    """主窗口关闭时调用：绝不留下孤儿 WS_CHILD 窗口。"""
    return detach()
