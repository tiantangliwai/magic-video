"""系统托盘：在常驻守护进程中运行，主窗口由托盘菜单按需拉起。"""
from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

from PIL import Image, ImageDraw

from . import config, db, scanner


def make_icon(size: int = 64) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2, 2, size - 2, size - 2), radius=14, fill=(59, 110, 245, 255))
    draw.polygon(
        [(size * 0.40, size * 0.28), (size * 0.40, size * 0.72), (size * 0.72, size * 0.50)],
        fill=(255, 255, 255, 255),
    )
    return image


class Tray:
    def __init__(self, watcher):
        self.watcher = watcher
        self.icon = None

    def _open_main(self, icon=None, item=None):
        try:
            subprocess.Popen(
                [str(Path(sys.executable).with_name("pythonw.exe")) if
                 Path(sys.executable).with_name("pythonw.exe").exists() else sys.executable,
                 str(config.APP_DIR / "run.py")],
                cwd=str(config.APP_DIR), close_fds=True,
            )
        except Exception:
            pass

    def _record_label(self, item=None) -> str:
        return "暂停记录" if db.get_int("recording_enabled", 1) else "恢复记录"

    def _toggle_record(self, icon=None, item=None):
        new_value = 0 if db.get_int("recording_enabled", 1) else 1
        db.set_setting("recording_enabled", new_value)
        if self.icon:
            self.icon.update_menu()

    def _scan_now(self, icon=None, item=None):
        def work():
            try:
                # scanner.scan() 内部已包含中文名即时匹配
                scanner.scan()
            except Exception:
                pass
        threading.Thread(target=work, daemon=True).start()

    def _quit(self, icon=None, item=None):
        self.watcher.stop()
        if self.icon:
            self.icon.stop()

    def start(self) -> None:
        import pystray
        menu = pystray.Menu(
            pystray.MenuItem("打开主界面", self._open_main, default=True),
            pystray.MenuItem("立即扫描目录", self._scan_now),
            pystray.MenuItem(self._record_label, self._toggle_record),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self.icon = pystray.Icon("MagicVideo", make_icon(), "Magic Video · 播放历史", menu)
        self.icon.run()
