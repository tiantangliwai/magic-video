"""本地视频文件的人工删除：只移入 Windows 回收站，绝不永久删除、绝不删数据库记录。

安全边界（对应「观看记录不要自动删除」）：
    - 本模块**只碰磁盘文件**，不 import db、不执行任何 DELETE。数据库行的标记由
      ``Api.delete_local_files`` 在文件成功移入回收站后单独 UPDATE。
    - 没有 ``os.remove`` / ``unlink`` / ``rmtree``：回收站 API 不可用时直接失败，
      绝不降级为永久删除。
    - 只处理绝对路径、受支持的视频扩展名、普通文件；目录、符号链接、系统目录一律拒绝。
    - 所有动作都由用户在界面上二次确认后触发，没有任何自动 / 定时调用点。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from pathlib import Path

from . import config

# SHFileOperationW 常量（Windows SDK ShlObj_core.h）
_FO_DELETE = 0x0003
_FOF_SILENT = 0x0004                # 不显示进度对话框
_FOF_NOCONFIRMATION = 0x0010        # 不弹「确认删除」系统框（我们已在界面里确认过）
_FOF_ALLOWUNDO = 0x0040             # 关键：进回收站，而不是永久删除
_FOF_NOERRORUI = 0x0400             # 出错时也不弹系统错误框
_TRASH_FLAGS = _FOF_SILENT | _FOF_NOCONFIRMATION | _FOF_ALLOWUNDO | _FOF_NOERRORUI

# 系统目录：即便数据库里被登记（例如误加了整个盘）也绝不从这里删文件
_BLOCKED_PARTS = {
    "windows", "system32", "syswow64", "program files", "program files (x86)",
    "$recycle.bin", "system volume information", "boot", "efi", "recovery",
}


class _SHFILEOPSTRUCTW(ctypes.Structure):
    """x86 / x64 通用的 SHFILEOPSTRUCTW 布局；指针字段用 c_void_p 便于直接传缓冲区地址。"""

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_uint16),       # FILEOP_FLAGS = WORD
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_void_p),
    ]


def is_safe_video_path(path) -> tuple[bool, str]:
    """纯逻辑校验：能否作为「人工删除」的目标。不访问磁盘、不产生副作用。"""
    text = str(path or "").strip()
    if not text:
        return False, "路径为空"
    p = Path(text)
    if not p.is_absolute():
        return False, "路径不是绝对路径"
    if p.suffix.lower() not in config.VIDEO_EXTS:
        return False, "不是受支持的视频文件"
    parts = {part.lower() for part in p.parts}
    if parts & _BLOCKED_PARTS:
        return False, "路径位于系统目录，已拒绝"
    return True, ""


def is_safe_directory(path, roots=None) -> tuple[bool, str]:
    """校验「用资源管理器打开」的目录是否安全。

    与 :func:`is_safe_video_path` 不同，这里针对的是**目录**：不检查视频后缀，
    但必须是绝对路径、真实存在、不在系统目录黑名单里，并且（给了 roots 时）
    落在已登记的扫描根目录内。只做只读判断，绝不改动磁盘。
    """
    text = str(path or "").strip()
    if not text:
        return False, "路径为空"
    p = Path(text)
    if not p.is_absolute():
        return False, "路径不是绝对路径"
    if {part.lower() for part in p.parts} & _BLOCKED_PARTS:
        return False, "路径位于系统目录，已拒绝"
    try:
        resolved = p.resolve()
        if {part.lower() for part in resolved.parts} & _BLOCKED_PARTS:
            return False, "路径位于系统目录，已拒绝"
        if not resolved.is_dir():
            return False, "目录不存在或已被移动"
    except OSError as exc:
        return False, f"目录不可访问：{exc}"
    allowed = [str(r) for r in (roots or []) if str(r or "").strip()]
    if allowed:
        for raw in allowed:
            try:
                root = Path(raw).resolve()
            except OSError:
                continue
            if resolved == root or resolved.is_relative_to(root):
                break
        else:
            return False, "目录不在已添加的扫描目录内，已拒绝"
    return True, ""


def _shell_trash(path: str) -> tuple[bool, str]:
    """调用 SHFileOperationW(FO_DELETE + FOF_ALLOWUNDO) 把单个文件移入回收站。"""
    if os.name != "nt":
        return False, "移入回收站仅支持 Windows"
    try:
        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        func = shell32.SHFileOperationW
        func.argtypes = [ctypes.POINTER(_SHFILEOPSTRUCTW)]
        func.restype = ctypes.c_int
        # pFrom 必须是「双 NUL 结尾」的宽字符串列表；create_unicode_buffer 会再补一个 NUL
        buffer = ctypes.create_unicode_buffer(path + "\0")
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = _FO_DELETE
        op.pFrom = ctypes.addressof(buffer)
        op.pTo = None
        op.fFlags = _TRASH_FLAGS
        op.fAnyOperationsAborted = False
        op.hNameMappings = None
        op.lpszProgressTitle = None
        code = func(ctypes.byref(op))
        if code != 0:
            return False, f"回收站操作失败（错误码 {code}）"
        if op.fAnyOperationsAborted:
            return False, "回收站操作被取消，文件未删除"
        return True, ""
    except Exception as exc:                 # 任何异常都只失败，绝不退回 os.remove
        return False, f"回收站接口不可用：{exc}"


def trash_file(path) -> dict:
    """把单个本地视频文件移入回收站。失败时返回原因，磁盘保持原样。"""
    ok, reason = is_safe_video_path(path)
    if not ok:
        return {"ok": False, "error": reason}
    p = Path(str(path).strip())
    try:
        if p.is_symlink():
            return {"ok": False, "error": "符号链接已拒绝"}
        if not p.is_file():
            return {"ok": False, "error": "文件不存在或已被移动"}
    except OSError as exc:
        return {"ok": False, "error": f"无法访问文件：{exc}"}
    success, error = _shell_trash(str(p))
    return {"ok": success, "error": error}
