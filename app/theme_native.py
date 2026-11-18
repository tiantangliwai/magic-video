"""让 pywebview 的**原生窗口标题栏**跟随黑 / 白主题。

网页里的顶栏由 CSS 变量控制，早就跟着变深了；但 WinForms 窗体的非客户区
（标题栏、边框、标题文字）由 DWM 绘制，CSS 完全管不到，Windows 亮色主题下
永远是白色一条。这里用 ``DwmSetWindowAttribute`` 打开「沉浸式深色模式」，
并在 Win11 22H2+ 上把标题栏 / 边框 / 标题文字颜色也设成主题色。

约束：
- 不访问 ``window.native``（非 UI 线程会触发 WebView2 COM 反射报错），
  主窗口 HWND 用 ``embedded_player.find_main_window()`` 按 PID + 标题枚举。
- 任何一步失败都静默降级：主题切换、播放记录、界面都不受影响。
- 没有定时器、没有常驻线程，只在「启动同步」和「用户切换主题」时调用一次。
"""
from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

# DWMWA_*（Win11 22H2+ 的颜色属性在旧系统上会静默失败，不影响深色模式本身）
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
DWMWA_BORDER_COLOR = 34
DWMWA_CAPTION_COLOR = 35
DWMWA_TEXT_COLOR = 36
DWMWA_COLOR_DEFAULT = 0xFFFFFFFF

# COLORREF 是 0x00BBGGRR，与 CSS 的 #RRGGBB 字节序相反
_DARK_CAPTION = 0x002B221E   # --surface  #1e222b
_DARK_BORDER = 0x00473A33    # --border   #333a47
_DARK_TEXT = 0x00F2EBE8     # --text     #e8ebf2
_COLOR_ATTRS = (DWMWA_CAPTION_COLOR, DWMWA_BORDER_COLOR, DWMWA_TEXT_COLOR)

try:  # 非 Windows 平台也能安全导入，单测可直接跑纯逻辑分支
    _dwm = ctypes.windll.dwmapi
    _dwm.DwmSetWindowAttribute.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ]
    _dwm.DwmSetWindowAttribute.restype = ctypes.c_long   # HRESULT
except (AttributeError, OSError, ValueError):  # pragma: no cover - 仅非 Windows 触发
    _dwm = None


def _set(hwnd: int, attr: int, value: int) -> bool:
    """写一个 4 字节 DWM 属性；HRESULT 非 0 或任何异常都视为不支持。"""
    if _dwm is None or not hwnd:
        return False
    try:
        payload = ctypes.c_uint32(int(value) & 0xFFFFFFFF)
        hr = _dwm.DwmSetWindowAttribute(
            wintypes.HWND(int(hwnd)), wintypes.DWORD(int(attr)),
            ctypes.byref(payload), wintypes.DWORD(4),
        )
        return int(hr) == 0
    except Exception:
        return False


def _find_window(retries: int = 5, require_visible: bool = True) -> int:
    """pywebviewready 触发时窗口可能还没显示；单次调用内短重试，不建常驻线程。"""
    from . import embedded_player  # 延迟导入，避免模块初始化顺序耦合
    for attempt in range(max(1, retries)):
        hwnd = embedded_player.find_main_window(require_visible=require_visible)
        if hwnd:
            return int(hwnd)
        if attempt + 1 < retries:
            time.sleep(0.1)
    return 0


def apply(theme: str, *, require_visible: bool = True, retries: int = 5) -> dict:
    """把原生标题栏同步到 light / dark；返回状态字典，绝不抛异常。

    ``require_visible=False`` 供窗口 ``before_show`` 时调用（HWND 已建、尚未显示），
    这样标题栏在窗口第一次绘制前就是主题色，不会先闪一条白。
    """
    dark = str(theme or "").strip().lower() == "dark"
    if sys.platform != "win32" or _dwm is None:
        return {"ok": False, "error": "当前系统不支持 DWM 标题栏主题"}

    hwnd = _find_window(retries=retries, require_visible=require_visible)
    if not hwnd:
        return {"ok": False, "error": "找不到主窗口句柄"}

    # 20 是 1903+ 正式属性，19 是 1809 的旧值；任一成功即可
    enabled = _set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0) or \
        _set(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, 1 if dark else 0)
    if dark:
        _set(hwnd, DWMWA_CAPTION_COLOR, _DARK_CAPTION)
        _set(hwnd, DWMWA_BORDER_COLOR, _DARK_BORDER)
        _set(hwnd, DWMWA_TEXT_COLOR, _DARK_TEXT)
    else:
        for attr in _COLOR_ATTRS:
            _set(hwnd, attr, DWMWA_COLOR_DEFAULT)

    return {"ok": bool(enabled), "dark": dark, "hwnd": int(hwnd),
            "error": "" if enabled else "DWM 沉浸式深色模式不可用"}
