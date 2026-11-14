"""创建 pywebview 原生窗口（外观是独立软件，界面是本地 HTML）。"""
from __future__ import annotations

import webview

from . import config, db, metadata
from .api import Api


def create_window(debug: bool = False) -> None:
    db.init_db()
    metadata.ensure_builtin_loaded()
    api = Api()
    settings = db.all_settings()

    window = webview.create_window(
        "Magic Video · 播放历史",
        str(config.WEB_DIR / "index.html"),
        js_api=api,
        width=int(settings.get("window_width", 1180)),
        height=int(settings.get("window_height", 780)),
        min_size=(960, 620),
        background_color="#F7F8FA",
        text_select=True,
    )
    api.attach_window(window)
    webview.start(gui="edgechromium", debug=debug, private_mode=False)
