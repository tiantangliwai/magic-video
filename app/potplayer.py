"""PotPlayer 桥接：播放状态回读与带断点启动。

实测依据（2026-11-14，PotPlayerMini64.exe，窗口类 PotPlayer64）：
    向窗口发送 WM_USER(0x0400)，wParam 为命令码：
      0x5000 -> 音量 0~100
      0x5002 -> 总时长(ms)
      0x5004 -> 当前位置(ms)
      0x5006 -> 状态 -1 停止 / 1 暂停 / 2 播放
    启动参数 /seek=hh:mm:ss、/current、/sub、/title 来自官方 CmdLine64.txt。
无需修改 PotPlayer 任何设置，也无需开启它的 HTTP 服务。
"""
from __future__ import annotations

import ctypes
import re
import subprocess
from ctypes import wintypes
from pathlib import Path

from . import config, db

WM_USER = 0x0400
POT_GET_VOLUME = 0x5000
POT_GET_TOTAL_TIME = 0x5002
POT_GET_PROGRESS_TIME = 0x5003
POT_GET_CURRENT_TIME = 0x5004
POT_SET_CURRENT_TIME = 0x5005
POT_GET_PLAY_STATUS = 0x5006

STATE_LABEL = {-1: "stopped", 1: "paused", 2: "playing"}
WINDOW_CLASSES = ("PotPlayer64", "PotPlayer", "PotPlayerMini64")

_user32 = ctypes.windll.user32
_user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
_user32.FindWindowW.restype = wintypes.HWND
_user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
_user32.SendMessageW.restype = ctypes.c_longlong
_user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]


def find_window() -> int:
    for cls in WINDOW_CLASSES:
        hwnd = _user32.FindWindowW(cls, None)
        if hwnd:
            return int(hwnd)
    return 0


def window_title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(2048)
    _user32.GetWindowTextW(hwnd, buf, 2048)
    return buf.value


def send(hwnd: int, code: int, lparam: int = 0) -> int:
    return int(_user32.SendMessageW(hwnd, WM_USER, code, lparam))


def self_check(hwnd: int) -> bool:
    """确认消息码在当前 PotPlayer 版本上仍然有效。"""
    try:
        volume = send(hwnd, POT_GET_VOLUME)
        state = send(hwnd, POT_GET_PLAY_STATUS)
        return 0 <= volume <= 100 and state in (-1, 1, 2)
    except Exception:
        return False


def parse_media_name(title: str) -> str:
    """从 'xxx.mkv - PotPlayer' 中取出媒体名。"""
    match = re.match(r"^(.*?)\s+-\s+PotPlayer.*$", title, re.IGNORECASE)
    name = match.group(1) if match else title
    return name.strip()


def read_status() -> dict:
    """读取一次播放状态；播放器未运行时返回 available=False。"""
    hwnd = find_window()
    if not hwnd:
        return {"available": False, "reason": "PotPlayer 未运行"}

    title = window_title(hwnd)
    total = send(hwnd, POT_GET_TOTAL_TIME)
    position = send(hwnd, POT_GET_CURRENT_TIME)
    state_code = send(hwnd, POT_GET_PLAY_STATUS)
    duration_ms = max(0, total)
    position_ms = max(0, min(position, duration_ms)) if duration_ms else max(0, position)
    percent = round(position_ms / duration_ms * 100, 2) if duration_ms else 0.0

    return {
        "available": True,
        "hwnd": hwnd,
        "title": title,
        "media_name": parse_media_name(title),
        "position_ms": position_ms,
        "duration_ms": duration_ms,
        "state": STATE_LABEL.get(state_code, "unknown"),
        "state_code": state_code,
        "percent": percent,
        "healthy": self_check(hwnd),
    }


def format_seek(ms: int) -> str:
    total_seconds = max(0, int(ms) // 1000)
    hours, rest = divmod(total_seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def resolve_media(media_name: str) -> dict | None:
    """把窗口标题里的媒体名对应回 media_files 记录。"""
    if not media_name:
        return None
    hit = db.row("SELECT * FROM media_files WHERE filename = ?", (media_name,))
    if hit:
        return hit
    candidate = Path(media_name)
    if candidate.is_absolute():
        hit = db.row("SELECT * FROM media_files WHERE path = ?", (str(candidate),))
        if hit:
            return hit
    stripped = Path(media_name).stem
    return db.row(
        "SELECT * FROM media_files WHERE filename LIKE ? ORDER BY id LIMIT 1",
        (f"%{stripped[:24]}%",),
    )


def potplayer_exe() -> str:
    return db.get_setting("potplayer_path", config.POTPLAYER_DEFAULT)


def play(path: str, position_ms: int = 0, use_current: bool = True) -> dict:
    """启动 / 复用 PotPlayer 播放指定文件，并从断点位置开始。"""
    exe = Path(potplayer_exe())
    if not exe.exists():
        return {"ok": False, "error": f"找不到 PotPlayer：{exe}"}
    if not Path(path).exists():
        return {"ok": False, "error": f"视频文件不存在：{path}"}

    args = [str(exe), str(path)]
    if position_ms and position_ms > 1000:
        args.append(f"/seek={format_seek(position_ms)}")
    if use_current:
        args.append("/current")
    args.append("/autoplay")

    try:
        subprocess.Popen(args, close_fds=True)
        return {"ok": True, "args": args}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def seek_current(ms: int) -> bool:
    hwnd = find_window()
    if not hwnd:
        return False
    send(hwnd, POT_SET_CURRENT_TIME, max(0, int(ms)))
    return True
