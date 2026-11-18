"""播放前蓝牙音箱保障（Windows / CoreAudio）。

产品口径（写进 FEATURES，改之前先想清楚）：

- 只解决「链路还在、默认输出被切走」这一类断联：起播时若目标音箱处于 ACTIVE
  但不是默认设备，就用 CoreAudio 的 IPolicyConfig 把它切回默认（纯用户态，约 5ms）。
- 「蓝牙链路真断」（端点 Unplugged / NotPresent）时，Windows 无稳定公开的 A2DP
  重连 API，软件**无法可靠自动重连**：只返回 link_down，由前端提示并引导用户去
  系统蓝牙设置手动连接，绝不假装已连上。
- 未配置设备名（默认空）时秒退，不触碰任何 COM；任何异常都吞掉并返回 error，
  **绝不影响 PotPlayer 正常起播**——蓝牙只是尽力而为的增强，不是播放的前置条件。

依赖 comtypes + pycaw（纯 Python，随包分发）：pycaw 封装了 COM 接口 GUID 与
IPolicyConfigVista 回退，避免手写脆弱的 vtable。两个库都懒加载，非 Windows /
未安装时 import 失败即降级为 error，上层无感。测试通过 patch 本模块的
_audio() 注入假实现，不触碰真实音频设备。
"""

from __future__ import annotations

import sys
from typing import Any

from . import db

SETTING_NAME = "bluetooth_speaker_name"

# 返回状态常量（前端按此分支）
DISABLED = "disabled"        # 未配置设备名
SWITCHED = "switched"        # 已从别的设备切到目标音箱
ACTIVE = "active"            # 目标音箱本来就是默认，无需操作
LINK_DOWN = "link_down"      # 配对在但链路断开（Unplugged/NotPresent），软件连不上
NOT_FOUND = "not_found"      # 系统里找不到名字匹配的设备
ERROR = "error"              # COM 不可用 / 异常，静默降级


def configured_name() -> str:
    """读取用户配置的目标音箱名（子串匹配，大小写不敏感）；空表示禁用。"""
    try:
        return str(db.get_setting(SETTING_NAME, "") or "").strip()
    except Exception:
        return ""


def _audio() -> Any:
    """懒加载 pycaw AudioUtilities。集中在此，便于测试整体 mock。"""
    from pycaw.pycaw import AudioUtilities  # type: ignore
    return AudioUtilities


def _is_render_endpoint(endpoint_id: str) -> bool:
    """CoreAudio 渲染端点 id 形如 {0.0.0....}；{0.0.1....} 是 capture。"""
    return str(endpoint_id or "").startswith("{0.0.0.")


def _state_name(state: Any) -> str:
    """把 AudioDeviceState 枚举统一成小写字符串，跨 pycaw 版本兼容。"""
    name = getattr(state, "name", None)
    return str(name or state or "").lower()


def _friendly(dev: Any) -> str:
    return str(getattr(dev, "FriendlyName", "") or "")


def probe(name: str | None = None) -> dict:
    """检测目标音箱并在需要时切为默认输出。

    返回 ``{status, device, message, action}``：
    - device：匹配到的 FriendlyName（便于前端回显）
    - message：给用户的简短说明（前端 toast 用）
    - action：非空时前端显示对应操作按钮（当前仅 open_settings）
    """
    target = (name if name is not None else configured_name())
    if not target:
        return {"status": DISABLED, "device": "", "message": "", "action": ""}

    try:
        audio = _audio()
        devices = audio.GetAllDevices()
        needle = target.lower()

        # 只在渲染端点里按 FriendlyName 子串匹配，取状态最好（Active 优先）的一条。
        candidates = [d for d in devices
                      if _is_render_endpoint(getattr(d, "id", ""))
                      and needle in _friendly(d).lower()]
        if not candidates:
            # 部分蓝牙/声卡驱动在设备关机后会把它从系统端点列表里彻底摘掉，
            # 此时「没找到」更可能是没开机/没连上，而不是名字写错；所以要给
            # 「打开蓝牙设置」按钮，文案也不能把锅全甩给配置。
            # （仍连在列表里但状态非 Active 的情况走上方的 LINK_DOWN。）
            return {"status": NOT_FOUND, "device": "", "action": "open_settings",
                    "message": f"系统里没看到「{target}」，可能是设备未开机或蓝牙未连接；"
                               f"若已连接请核对设置里的设备名"}

        candidates.sort(key=lambda d: 0 if _state_name(getattr(d, "state", None)) == "active" else 1)
        dev = candidates[0]
        label = _friendly(dev)
        state = _state_name(getattr(dev, "state", None))

        if state != "active":
            return {"status": LINK_DOWN, "device": label, "action": "open_settings",
                    "message": f"蓝牙音箱「{label}」当前未连接，请在系统蓝牙设置里连接"}

        default_id = str(getattr(audio.GetSpeakers(), "id", "") or "")
        if default_id and default_id == str(getattr(dev, "id", "")):
            return {"status": ACTIVE, "device": label, "action": "",
                    "message": f"已在使用「{label}」"}

        audio.SetDefaultDevice(dev.id)
        return {"status": SWITCHED, "device": label, "action": "",
                "message": f"已切回蓝牙音箱「{label}」"}
    except Exception as exc:  # 无 pycaw / 非 Windows / COM 异常：一律静默降级
        return {"status": ERROR, "device": "", "action": "",
                "message": f"蓝牙设备检测不可用：{type(exc).__name__}"}


def open_bluetooth_settings() -> dict:
    """打开 Windows 蓝牙设置页，供前端「一键连接」按钮调用。"""
    if sys.platform != "win32":
        return {"ok": False, "error": "仅 Windows 支持"}
    try:
        import os
        os.startfile("ms-settings:bluetooth")  # type: ignore[attr-defined]
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"无法打开蓝牙设置：{exc}"}
