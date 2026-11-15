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
import time
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
POT_SET_PLAY_STATUS = 0x5007     # 0 切换 / 1 暂停 / 2 播放
POT_SET_PLAY_ORDER = 0x5008      # 0 上一集 / 1 下一集（走 PotPlayer 自己的播放列表）
POT_SEND_VIRTUAL_KEY = 0x5010    # 2023-08-30 之后的版本支持，不需要窗口焦点

# WM_COMMAND 指令 ID：PotPlayer 的菜单命令编号，官方未写文档但跨版本稳定，
# 社区库（AutoHotkey PotPlayer x64 Function Library / PotPlayerAPI.NET）长期在用。
# 相比「投快捷键」的优势：不受用户自定义键位影响，也不需要窗口有键盘焦点，
# 内嵌成子窗口时同样有效——这正是「顶栏倍速/暂停按钮点了没反应」的根因。
WM_COMMAND = 0x0111
POT_CMD = {
    "play_pause": 10014,
    "play": 20001,
    "pause": 20000,
    "stop": 20002,
    "prev": 10123,
    "next": 10124,
    "volume_up": 10035,
    "volume_down": 10036,
    "toggle_mute": 10037,
}

STATE_LABEL = {-1: "stopped", 1: "paused", 2: "playing"}
WINDOW_CLASSES = ("PotPlayer64", "PotPlayer", "PotPlayerMini64")

# SendMessageW 是同步的：PotPlayer 卡住/正忙时会把调用线程挂住（界面每 3 秒轮询一次，
# 一旦挂住整个窗口就"卡住"）。改用 SendMessageTimeoutW + SMTO_ABORTIFHUNG，超时即返回 0。
SMTO_ABORTIFHUNG = 0x0002
SEND_TIMEOUT_MS = 300

try:  # 非 Windows 平台也能安全导入本模块（纯函数与测试仍可用）
    _user32 = ctypes.windll.user32
    _user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
    _user32.FindWindowW.restype = wintypes.HWND
    _user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.SendMessageW.restype = ctypes.c_longlong
    _user32.SendMessageTimeoutW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_ssize_t),
    ]
    _user32.SendMessageTimeoutW.restype = ctypes.c_longlong
    _user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _user32.IsWindow.argtypes = [wintypes.HWND]
    _user32.IsWindow.restype = wintypes.BOOL
    # 运行时控制：把按键消息直接投到 PotPlayer 窗口（内嵌时是 WS_CHILD，没有键盘焦点）
    _user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    _user32.PostMessageW.restype = wintypes.BOOL
    _user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
    _user32.MapVirtualKeyW.restype = wintypes.UINT
except (AttributeError, OSError):  # pragma: no cover - 仅在非 Windows 触发
    _user32 = None

# 内嵌播放时 PotPlayer 会变成主窗口的子窗口，FindWindowW 只搜顶层窗口。
# 这个 HWND 同时写进 settings（守护进程是独立进程，从那里读），本进程内则用它做快速回退。
_EMBEDDED_HWND = 0
EMBED_HWND_KEY = "embed_hwnd"


def set_embedded_hwnd(hwnd: int) -> None:
    """由 app.embedded_player 在 attach / detach 时调用（避免循环导入）。"""
    global _EMBEDDED_HWND
    _EMBEDDED_HWND = int(hwnd or 0)


def embedded_hwnd() -> int:
    return _EMBEDDED_HWND


def _class_name(hwnd: int) -> str:
    if _user32 is None or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(256)
    try:
        _user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def _cached_embedded_hwnd() -> int:
    """跨进程取值：优先本进程缓存，其次 settings 里持久化的 HWND。"""
    candidates = [_EMBEDDED_HWND]
    try:
        candidates.append(int(db.get_setting(EMBED_HWND_KEY, 0) or 0))
    except Exception:
        pass
    return next((v for v in candidates if v), 0)


def find_window() -> int:
    if _user32 is None:
        return 0
    for cls in WINDOW_CLASSES:
        hwnd = _user32.FindWindowW(cls, None)
        if hwnd:
            return int(hwnd)
    # 子窗口回退：确认窗口还在、类仍是 PotPlayer，避免拿陈旧 HWND 去发消息
    cached = _cached_embedded_hwnd()
    if cached and _user32.IsWindow(cached) and _class_name(cached) in WINDOW_CLASSES:
        return int(cached)
    return 0


def window_title(hwnd: int) -> str:
    if _user32 is None or not hwnd:
        return ""
    buf = ctypes.create_unicode_buffer(2048)
    _user32.GetWindowTextW(hwnd, buf, 2048)
    return buf.value


def send(hwnd: int, code: int, lparam: int = 0) -> int:
    """读一次播放器状态；带超时，绝不让无响应的播放器阻塞调用线程。"""
    if _user32 is None or not hwnd:
        return 0
    result = ctypes.c_ssize_t(0)
    try:
        ok = _user32.SendMessageTimeoutW(
            hwnd, WM_USER, code, lparam, SMTO_ABORTIFHUNG, SEND_TIMEOUT_MS,
            ctypes.byref(result),
        )
    except Exception:
        return 0
    return int(result.value) if ok else 0


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


# ---------------------------------------------------------------- 运行时播放控制
# PotPlayer 没有公开的运行时控制 API：WM_USER 命令码只覆盖「状态回读 + seek」，
# CmdLine64.txt 也只有启动参数。播放/暂停、停止、倍速、全屏只能把快捷键投到
# 播放器窗口。内嵌后窗口是 WS_CHILD、通常没有键盘焦点，所以用 PostMessage
# 直接投递 WM_KEYDOWN/WM_KEYUP，而不是只作用于前台线程的 SendInput。
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
_KEYUP_BITS = 0xC0000000  # 转换位(31) + 先前按下位(30)

VK_CODES = {
    "SPACE": 0x20, "ENTER": 0x0D, "ESC": 0x1B, "S": 0x53,
    "C": 0x43, "X": 0x58, "Z": 0x5A, "P": 0x50,
    "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22,
}

# 默认键位取 PotPlayer 常见默认绑定；各版本/用户自定义可能不同，
# 全部可在「设置 → 播放控制」里覆盖，避免换个版本整套按钮失效。
DEFAULT_KEY_BINDINGS = {
    "play_pause": "SPACE",
    "stop": "S",
    "faster": "C",
    "slower": "X",
    "reset_speed": "Z",
    "fullscreen": "ENTER",
}
KEY_SETTINGS = {name: f"pot_key_{name}" for name in DEFAULT_KEY_BINDINGS}
SPEED_PRESETS = (1.0, 1.25, 1.5, 2.0)
SPEED_STEPS = (0.1, 0.25, 0.5)
DEFAULT_SPEED_STEP = 0.1


def binding(action: str) -> str:
    """读一个动作的键位：设置里存的值必须落在白名单里，否则用默认值。"""
    default = DEFAULT_KEY_BINDINGS.get(action, "")
    if not default:
        return ""
    try:
        value = str(db.get_setting(KEY_SETTINGS[action], default) or default).strip().upper()
    except Exception:
        return default
    return value if value in VK_CODES else default


def speed_step() -> float:
    """倍速快捷键每次改变多少；只认固定档位，杜绝脏配置导致按几十次。"""
    try:
        value = float(db.get_setting("pot_speed_step", DEFAULT_SPEED_STEP))
    except Exception:
        return DEFAULT_SPEED_STEP
    return value if value in SPEED_STEPS else DEFAULT_SPEED_STEP


def send_key(action: str, hwnd: int | None = None) -> dict:
    """把 action 对应的按键投到 PotPlayer 窗口。不依赖键盘焦点。

    优先走 ``POT_SEND_VIRTUAL_KEY``（PotPlayer 自己受理虚拟键，不需要焦点、
    也不受输入法/前台窗口影响）；老版本不认这个命令时再退回直接投 WM_KEYDOWN。
    """
    vk = VK_CODES.get(binding(action), 0)
    if not vk:
        return {"ok": False, "error": f"未知的播放控制动作：{action}"}
    target = int(hwnd or 0) or find_window()
    if not target:
        return {"ok": False, "error": "PotPlayer 未运行"}
    if _user32 is None:
        return {"ok": False, "error": "当前系统不支持 Win32 按键注入"}
    # 1) 官方虚拟键通道
    try:
        if _user32.PostMessageW(target, WM_USER, POT_SEND_VIRTUAL_KEY, vk):
            return {"ok": True, "action": action, "key": binding(action),
                    "hwnd": target, "via": "virtual-key"}
    except Exception:
        pass
    # 2) 直接投键盘消息（老版本兜底）
    try:
        scan = int(_user32.MapVirtualKeyW(vk, 0) or 0)
        down = (scan << 16) | 1
        up = (scan << 16) | _KEYUP_BITS | 1
        ok_down = _user32.PostMessageW(target, WM_KEYDOWN, vk, down)
        ok_up = _user32.PostMessageW(target, WM_KEYUP, vk, up)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    if not (ok_down and ok_up):
        return {"ok": False, "error": "按键投递失败（播放器窗口可能已关闭）"}
    return {"ok": True, "action": action, "key": binding(action), "hwnd": target,
            "via": "wm-keydown"}


def _post(hwnd: int, message: int, code: int, lparam: int = 0) -> bool:
    """异步投一条消息：播放器卡住也不会把界面线程带住。"""
    if _user32 is None or not hwnd:
        return False
    try:
        return bool(_user32.PostMessageW(hwnd, message, code, lparam))
    except Exception:
        return False


def play_status(hwnd: int) -> int:
    """当前播放状态：-1 停止 / 1 暂停 / 2 播放；读不到返回 0。"""
    return send(hwnd, POT_GET_PLAY_STATUS)


def _wait_status(hwnd: int, before: int, want: int | None,
                 tries: int = 5, delay: float = 0.04) -> int:
    """等状态变化，最多 ~200ms；返回最后一次读到的状态。"""
    for _ in range(tries):
        current = play_status(hwnd)
        if want is None:
            if current != before:
                return current
        elif current == want:
            return current
        time.sleep(delay)
    return play_status(hwnd)


# 每个动作期望到达的状态；None 表示「任意变化即可」（切换语义）
_ACTION_TARGET = {"play_pause": None, "play": 2, "pause": 1, "stop": -1}


def control(action: str, hwnd: int | None = None) -> dict:
    """播放/暂停、停止：优先用 PotPlayer 的 WM_COMMAND 菜单指令并校验生效。

    旧实现只投快捷键，遇到自定义键位、内嵌子窗口、播放器不在前台时全都静默失败，
    用户看到的是「按钮点了没反应」。现在按「命令 → 播放状态 → 快捷键」三条路依次尝试，
    每条都用 ``POT_GET_PLAY_STATUS`` 复查是否真的生效，最后如实返回走了哪条。
    """
    name = str(action or "").strip()
    target = int(hwnd or 0) or find_window()
    if not target:
        return {"ok": False, "error": "PotPlayer 未运行"}
    before = play_status(target)
    want = _ACTION_TARGET.get(name, 0)
    # 已经是目标状态（例如重复点「停止」）直接当成功，不必再折腾
    if want not in (None, 0) and before == want:
        return {"ok": True, "action": name, "state": before, "via": "already"}

    # 注意：这里只登记「怎么发」，不能提前发。三条通道同时发出去会互相打架
    # （例如暂停命令 + 状态切换 + 空格键 = 连续切换三次，画面看起来毫无反应）。
    attempts: list[tuple[str, object]] = []
    if name in POT_CMD:
        command = POT_CMD[name]
        attempts.append((f"command:{command}",
                         lambda: _post(target, WM_COMMAND, command)))
    status_code = {"play_pause": 0, "play": 2, "pause": 1, "stop": 1}.get(name)
    if status_code is not None:
        attempts.append((f"status:{status_code}",
                         lambda: _post(target, WM_USER, POT_SET_PLAY_STATUS, status_code)))
    key_action = {"play_pause": "play_pause", "stop": "stop"}.get(name)
    if key_action:
        attempts.append((f"key:{binding(key_action)}",
                         lambda: bool(send_key(key_action, target).get("ok"))))

    if not attempts:
        return {"ok": False, "error": f"不支持的播放控制：{name}"}

    posted = False
    for label, send in attempts:
        if not send():
            continue
        posted = True
        after = _wait_status(target, before, want)
        if want is None:
            done = after != before
        else:
            done = after == want
        if done:
            return {"ok": True, "action": name, "state": after, "via": label}
    if not posted:
        return {"ok": False, "error": "指令没有送出去（播放器窗口可能已关闭）"}
    hint = ""
    if before == -1:
        hint = "（播放器当前是停止状态，先点播放再试）"
    return {"ok": False, "state": play_status(target),
            "error": f"指令已送出但状态没变，可能是 PotPlayer 版本不认这个命令{hint}"}



def speed_plan(target: float) -> dict:
    """从 1.0 出发算要按几次加速键；步进到不了时返回最近可达值，不假装精确。"""
    step = speed_step()
    try:
        wanted = float(target)
    except (TypeError, ValueError):
        return {"ok": False, "error": "倍速必须是数字"}
    if wanted <= 0 or wanted > 4:
        return {"ok": False, "error": "倍速超出支持范围"}
    presses = max(0, min(int(round((wanted - 1.0) / step)), 40))
    actual = round(1.0 + presses * step, 2)
    return {
        "ok": True, "wanted": wanted, "actual": actual, "presses": presses,
        "step": step, "exact": abs(actual - wanted) < 1e-6,
    }


def apply_speed(target: float, hwnd: int | None = None) -> dict:
    """先复位到 1.0，再按 N 次加速键 —— 结果与当前倍速无关，可重复调用。"""
    plan = speed_plan(target)
    if not plan["ok"]:
        return plan
    reset = send_key("reset_speed", hwnd)
    if not reset["ok"]:
        return reset
    for _ in range(plan["presses"]):
        one = send_key("faster", hwnd)
        if not one["ok"]:
            return one
    return plan
