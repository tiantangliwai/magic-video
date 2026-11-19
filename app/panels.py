"""独立的工具窗口（设置 / 扫描目录 / 导入列表）。

用户要的是「能拖出主窗口边界、能和主窗口同时操作」的弹窗——HTML 覆盖层做不到
（被 WebView 裁剪、主窗口最小化就一起消失），只能是 pywebview 的独立原生窗口。

设计要点
--------
- **三个窗口各自独立**：可同时打开、互不遮挡时能看到多个、与主窗口并行操作。
- **原生标题栏**：拖动 / 最小化 / 缩放 / 关闭都是系统行为，最稳。
- **标题不带 "Magic Video"**：``embedded_player.find_main_window`` 按标题包含
  ``Magic Video`` 找主窗口、``single_instance.activate_existing`` 按精确标题激活；
  panel 标题若带这个词会被误当成主窗口刷主题 / 被重复启动置前。
- **点 X 只隐藏不销毁**：``closing`` 返回 False 让 WinForms 取消关闭，窗口对象常驻，
  再点开只是 ``show()``，不用重建 WebView（重建既要重新初始化 WebView2，又会丢状态）。
- **真正退出时才 destroy**：``close_all()`` 先置 ``_shutting_down``，closing 钩子放行。
- **独立 Api 实例**：每窗口 ``attach_window`` 自己的窗口，``choose_directory`` 等文件
  对话框的父窗口才正确；守护自启动只由主窗口的 Api 负责，panel 不碰。

主题：首屏 URL 带 ``?theme=``，配合 index.html head 同步脚本，第一帧就是正确主题；
原生标题栏由 ``theme_native.hook_webview_title_bar`` 的类级补丁跟随软件主题。
"""
from __future__ import annotations

from typing import Any

import webview

# 可作为独立窗口打开的面板。key 与前端 VIEW_META / switchView 的名字一致。
PANEL_TITLES: dict[str, str] = {
    "settings": "设置",
    "scan": "扫描目录",
    "import": "导入列表",
}
# 各面板初始尺寸（设置内容最多，给大一点；导入主要是文本域）。
PANEL_SIZES: dict[str, tuple[int, int]] = {
    "settings": (940, 700),
    "scan": (1040, 720),
    "import": (900, 700),
}

_WINDOWS: dict[str, Any] = {}
_VISIBLE: set[str] = set()
_SHUTTING_DOWN = False


def panel_url(name: str, theme: str) -> str:
    """面板首屏 URL：复用 index.html，用 panel 参数让前端进入精简模式。

    必须传本地路径（不是 file:// URI）：pywebview 6.2.1 只对本地路径启动内置 HTTP
    server 解析 query（与 app.main.startup_url 同一约束）。
    """
    from . import config  # 延迟导入，避免模块初始化顺序耦合
    t = str(theme or "").strip().lower()
    if t not in ("light", "dark"):
        t = "light"
    return str(config.WEB_DIR / "index.html") + f"?theme={t}&panel={name}"


def _make_api():
    """每个面板独立的 js_api 实例。

    延迟导入 Api：panels 由 main 导入，而 api 会用到 config / db 等，避免顶层循环。
    """
    from .api import Api
    return Api()


def _on_closing(name: str):
    """原生 X：平时改成隐藏（返回 False 取消关闭）；真正退出时放行销毁。"""
    if _SHUTTING_DOWN:
        return None
    win = _WINDOWS.get(name)
    try:
        if win is not None:
            win.hide()
    except Exception:
        pass
    _VISIBLE.discard(name)
    return False  # Event.set 收集到 False → on_closing 设 args.Cancel=True


def create_panel(name: str, theme: str) -> Any:
    """建一个隐藏的面板窗口并登记；重复调用返回已有窗口。

    启动时预建（``hidden=True``）：winforms 后端对 hidden 窗口会先 Show 再 Hide，
    ``events.shown`` 会置位，之后 ``window.show()/hide()`` 的 ``_shown_call`` 守卫
    不会卡住。比运行时从 js_api 线程建窗更稳——后者有 GUI 线程亲和性风险。
    """
    global _WINDOWS
    if name in _WINDOWS and _WINDOWS[name] is not None:
        return _WINDOWS[name]
    title = PANEL_TITLES.get(name, name)
    width, height = PANEL_SIZES.get(name, (900, 700))
    api = _make_api()
    window = webview.create_window(
        title,
        panel_url(name, theme),
        js_api=api,
        width=width,
        height=height,
        min_size=(720, 520),
        hidden=True,
        text_select=True,
    )
    api.attach_window(window)
    window.events.closing += lambda: _on_closing(name)
    _WINDOWS[name] = window
    return window


def create_all(theme: str) -> None:
    """主窗口建好后调用：预建全部面板窗口（hidden，不显示）。"""
    for name in PANEL_TITLES:
        try:
            create_panel(name, theme)
        except Exception:
            # 单个面板建窗失败不能拖垮主程序：前端 open_panel 会再兜底重试一次
            _WINDOWS.pop(name, None)


def open_panel(name: str, theme: str = "") -> dict:
    """显示（必要时先创建）一个面板窗口并置前；幂等。"""
    if name not in PANEL_TITLES:
        return {"ok": False, "error": f"未知面板：{name}"}
    win = _WINDOWS.get(name)
    try:
        if win is None:
            win = create_panel(name, theme)
        win.show()
        _VISIBLE.add(name)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def close_panel(name: str) -> dict:
    """隐藏一个面板窗口（不销毁，保留状态与滚动位置）。"""
    win = _WINDOWS.get(name)
    if win is None:
        return {"ok": True}
    try:
        win.hide()
        _VISIBLE.discard(name)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def is_open(name: str) -> bool:
    return name in _VISIBLE


def close_all() -> None:
    """退出软件时真正销毁所有面板窗口。

    先置 ``_shutting_down`` 让 closing 钩子放行，再逐个 destroy；失败静默。
    """
    global _SHUTTING_DOWN
    _SHUTTING_DOWN = True
    for name, win in list(_WINDOWS.items()):
        try:
            win.destroy()
        except Exception:
            pass
    _WINDOWS.clear()
    _VISIBLE.clear()


def reset_for_tests() -> None:
    """仅供单测：清空进程内注册表，避免用例之间互相污染。"""
    global _SHUTTING_DOWN
    _WINDOWS.clear()
    _VISIBLE.clear()
    _SHUTTING_DOWN = False
