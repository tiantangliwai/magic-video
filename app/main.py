"""创建 pywebview 原生窗口（外观是独立软件，界面是本地 HTML）。"""
from __future__ import annotations

import webview

from . import config, db, embedded_player, single_instance
from .api import Api


def create_window(debug: bool = False) -> None:
    # 任务栏/快捷方式重复点击时，只把已有窗口置前并退出，绝不再开第二个窗口。
    # 第二个进程一旦也去初始化 WebView2，会与已有实例抢资源并显著拖慢启动。
    if not single_instance.guard():
        return

    # Api() 内部已完成 init_db() + 内置词典装载，这里不再重复执行（启动提速）。
    api = Api()
    settings = db.all_settings()

    window = webview.create_window(
        "Magic Video · 播放历史",
        str(config.WEB_DIR / "index.html"),
        js_api=api,
        width=int(settings.get("window_width", 1180)),
        height=int(settings.get("window_height", 780)),
        min_size=(960, 620),
        # 不传 background_color：EdgeChromium 后端会在 CoreWebView2 尚未就绪时
        # 访问 DefaultBackgroundColor，在 WebView2 157 上会抛 E_NOINTERFACE。
        # 背景色完全由 style.css 控制，界面观感不变。
        text_select=True,
    )
    api.attach_window(window)
    # private_mode=True：每次启动用临时 WebView2 profile，避免升级安装后仍加载缓存里的旧 app.js。
    # 所有状态都在 SQLite，不需要浏览器端持久化；配合 index.html 的 app.js?v=<版本> 双保险。
    try:
        webview.start(gui="edgechromium", debug=debug, private_mode=True)
    finally:
        # 主窗口关闭时把 PotPlayer 还原成独立窗口，绝不留下孤儿 WS_CHILD 窗口。
        embedded_player.cleanup()
