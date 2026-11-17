"""Everything 快速检索：本机全盘文件名检索（可降级）。

背景（2026-11-15 实测）：Everything 核心引擎闭源，官方只开源 es.exe 命令行
客户端（MIT）与 SDK；它们都只是连到「正在运行的 Everything 实例」的 IPC 客户端，
Lite 版还会彻底禁用 IPC/ES/HTTP。本机装的是 1.4.1.1032 完整版，但以
``-svc``（会话 0 服务）运行，用户会话里 es.exe 连不上 IPC（Error 8），
所以最可靠的通道是 Everything 的 HTTP 服务。

三级降级设计，**没有任何外部依赖也能用**：

1. es.exe（设置里可指定路径，留空用默认）→ ``es.exe -n <limit> <query>``；
2. Everything HTTP 服务（设置里填地址，可选 Basic Auth）→ ``?s=<query>&j=1``；
3. 库内 SQL：直接检索本程序已扫描的 media_files，毫秒级，功能永远可用。

另外提供 ``open_app()`` 作为快捷方式：直接在系统里打开 Everything 主程序窗口，
路径由后端固定配置决定，前端不传参。

所有外部调用都有短超时；失败只降级、不抛异常、不阻塞 UI。
"""
from __future__ import annotations

import json
import os
import subprocess
import urllib.parse
from pathlib import Path

import requests

from . import config, db

DEFAULT_ES_PATH = r"D:\tool\Everything\es.exe"
DEFAULT_APP_PATH = r"D:\tool\Everything\Everything.exe"  # 打开主程序用；非 Lite 版
VIDEO_EXTS = tuple(sorted(config.VIDEO_EXTS))
ES_TIMEOUT = 6
HTTP_TIMEOUT = (1.5, 3.0)


def _decode_output(raw) -> str:
    """把 es.exe 的原始输出解码成文本，修掉中文路径 GBK 乱码。

    es.exe 按**系统 ANSI 代码页**输出（中文 Windows = GBK/cp936），而 Python 3.13
    默认 UTF-8 模式、``locale.getpreferredencoding()`` 返回 ``utf-8``：旧代码用
    ``encoding="utf-8", errors="replace"`` 解码 GBK 中文，坏字节被替换成 ``?``，
    全盘搜到的中文动画路径全变乱码、点开就失败。

    这里 strict 尝试 utf-8（兼容纯 ASCII / 已是 UTF-8 的输出），失败回退 GBK。
    单测里 mock 的 stdout 常直接给 str，原样返回即可。
    """
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    for enc in ("gbk", "cp936"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")


def es_exe() -> Path:
    """es.exe 路径。设置里存空串时回退默认路径，而不是变成 Path('.') 导致永远找不到。"""
    configured = str(db.get_setting("everything_es_path", "") or "").strip()
    return Path(configured or DEFAULT_ES_PATH)


def app_exe() -> Path:
    """Everything 主程序路径：设置值优先，空串回退默认（固定常量，前端不可传）。"""
    configured = str(db.get_setting("everything_app_path", "") or "").strip()
    return Path(configured or DEFAULT_APP_PATH)


def http_base() -> str:
    return str(db.get_setting("everything_http_url", "") or "").strip().rstrip("/")


def http_auth() -> tuple[str, str] | None:
    """可选的 Everything HTTP 服务 Basic Auth；只在设置了用户名时启用。"""
    user = str(db.get_setting("everything_http_user", "") or "").strip()
    if not user:
        return None
    return (user, str(db.get_setting("everything_http_pass", "") or ""))


HTTP_ENABLE_STEPS = (
    "Everything 菜单「工具 → 选项 → HTTP 服务器」→ 勾选「启用 HTTP 服务器」→ 应用",
    "确认服务地址（默认 http://127.0.0.1:80），把它填到「设置 → 全盘快搜」的服务地址里",
)


def capabilities() -> dict:
    """给设置页展示：当前能用到哪一级，以及降级时的具体补救步骤。

    ``degraded=True`` 表示 es.exe 与 HTTP 都不可用，检索范围只剩本软件媒体库。
    这时前端必须**明说原因和操作路径**，而不是让人以为「搜不到就是没有」。
    注意本模块只做只读探测：不改 Everything 的 ini、不重启它的服务（项目铁律）。
    """
    es = es_exe()
    http = http_base()
    available = es.is_file()
    degraded = not available and not http
    if available:
        reason = ""
    elif http:
        reason = ""
    elif app_exe().is_file():
        reason = "已安装 Everything，但没用 es.exe（Lite 版或未下载）也未启用 HTTP 服务"
    else:
        reason = "未检测到 Everything（es.exe 与 HTTP 服务都不可用）"
    return {
        "es_path": str(es),
        "es_available": available,
        "http_url": http,
        "http_configured": bool(http),
        "library_fallback": True,
        "installed": app_exe().is_file(),
        "app_path": str(app_exe()),
        "degraded": degraded,
        "degraded_reason": reason,
        "http_enable_steps": list(HTTP_ENABLE_STEPS),
    }


def open_app() -> dict:
    """打开 Everything 主程序（快捷方式用）。路径取自后端固定配置，前端不传参。

    只放行真实存在的 .exe 文件，用 os.startfile 而非 shell 命令，避免拼接注入。
    Lite 版不含 IPC/es.exe 支持，打开本身没问题，但检索仍会降级。
    """
    exe = app_exe()
    if not exe.is_absolute():
        return {"ok": False, "error": "Everything 路径不是绝对路径"}
    if exe.suffix.lower() != ".exe":
        return {"ok": False, "error": "Everything 路径不是可执行文件"}
    if not exe.is_file():
        return {"ok": False, "error": f"找不到 Everything 主程序：{exe}"}
    if not hasattr(os, "startfile"):
        return {"ok": False, "error": "当前系统不支持直接打开程序"}
    try:
        os.startfile(str(exe))     # 不是 shell 命令，无法被拼接注入
        return {"ok": True, "path": str(exe)}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}


def _row(path: str, library: dict[str, dict]) -> dict:
    text = str(path or "").strip().strip('"')
    if not text:
        return {}
    p = Path(text)
    hit = library.get(text.replace("/", "\\").lower())
    return {
        "path": text,
        "filename": p.name,
        "in_library": bool(hit),
        "file_id": hit["id"] if hit else None,
        "title_cn": (hit or {}).get("title_cn") or "",
    }


def _library_index() -> dict[str, dict]:
    return {
        str(r["path"]).replace("/", "\\").lower(): r
        for r in db.rows("SELECT id, path, title_cn FROM media_files")
    }


def search_es(query: str, limit: int, library: dict) -> tuple[list[dict], str]:
    exe = es_exe()
    if not exe.is_file():
        return [], "es.exe 不存在"
    # -n 限制条数；-a-d 不列目录；按文件扩展名过滤交给下面统一处理
    args = [str(exe), "-n", str(min(limit * 3, 2000)), query]
    try:
        proc = subprocess.run(
            args, capture_output=True, timeout=ES_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # 不指定 text/encoding：拿到原始 bytes 后按 GBK 解码（见 _decode_output）。
        stdout = _decode_output(proc.stdout)
        if proc.returncode != 0 and not stdout:
            stderr = _decode_output(proc.stderr).strip()
            # Error 8：Everything 以 -svc（会话 0 服务）运行，用户会话没有 IPC 窗口，
            # 这是会话隔离，不是没装 es.exe。给出可操作的下一步。
            if "Error 8" in stderr or "IPC not found" in stderr:
                return [], ("es.exe 已就位，但连不上 Everything 的 IPC（本机 Everything 以服务模式 -svc 运行）；"
                            "请在 Everything 选项里启用 HTTP 服务并填入地址，或改用普通模式启动 Everything")
            return [], f"es.exe 退出码 {proc.returncode}" + (f"：{stderr[:120]}" if stderr else "")
        rows = []
        for line in stdout.splitlines():
            candidate = line.strip()
            if not candidate or not candidate.lower().endswith(VIDEO_EXTS):
                continue
            row = _row(candidate, library)
            if row:
                rows.append(row)
            if len(rows) >= limit:
                break
        return rows, ""
    except subprocess.TimeoutExpired:
        return [], "es.exe 超时"
    except Exception as exc:
        return [], f"es.exe 调用失败：{type(exc).__name__}"


def search_http(query: str, limit: int, library: dict) -> tuple[list[dict], str]:
    base = http_base()
    if not base:
        return [], "未配置 HTTP 服务地址"
    url = base + "/?" + urllib.parse.urlencode({"s": query, "j": "1", "n": min(limit * 3, 2000),
                                                "path_column": "1"})
    try:
        resp = requests.get(url, timeout=HTTP_TIMEOUT, auth=http_auth())
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        data = resp.json()
        results = data.get("results") if isinstance(data, dict) else data
        rows = []
        for item in results or []:
            candidate = item.get("path", "") if isinstance(item, dict) else str(item)
            if isinstance(item, dict) and item.get("name") and item.get("path"):
                candidate = str(item["path"]).rstrip("\\/") + "\\" + str(item["name"])
            if not str(candidate).lower().endswith(VIDEO_EXTS):
                continue
            row = _row(candidate, library)
            if row:
                rows.append(row)
            if len(rows) >= limit:
                break
        return rows, ""
    except Exception as exc:
        return [], f"HTTP 检索失败：{type(exc).__name__}"


def search_library(query: str, limit: int, library: dict) -> tuple[list[dict], str]:
    escaped = str(query).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like = f"%{escaped}%"
    rows = db.rows(
        "SELECT id, path, filename, title_cn, title_guess FROM media_files "
        "WHERE (filename LIKE ? ESCAPE '\\' OR COALESCE(title_cn,'') LIKE ? ESCAPE '\\' "
        "OR COALESCE(title_guess,'') LIKE ? ESCAPE '\\' OR path LIKE ? ESCAPE '\\') "
        "ORDER BY id DESC LIMIT ?",
        (like, like, like, like, int(limit)),
    )
    return [
        {"path": r["path"], "filename": r["filename"], "in_library": True,
         "file_id": r["id"], "title_cn": r["title_cn"] or r["title_guess"] or ""}
        for r in rows
    ], ""


def search(query: str, limit: int = 200) -> dict:
    """统一入口：返回 {ok, rows, source, hint, capabilities}。"""
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "rows": [], "source": "none", "hint": "请输入关键词"}
    try:
        limit = max(1, min(int(limit), 2000))
    except (TypeError, ValueError):
        limit = 200
    library = _library_index()

    rows, es_error = search_es(q, limit, library)
    if rows:
        return {"ok": True, "rows": rows, "source": "everything-es", "hint": "来自 Everything 命令行",
                "capabilities": capabilities(), "degraded": False, "channel_ok": True}

    rows, http_error = search_http(q, limit, library)
    if rows:
        return {"ok": True, "rows": rows, "source": "everything-http", "hint": "来自 Everything HTTP 服务",
                "capabilities": capabilities(), "degraded": False, "channel_ok": True}

    rows, _ = search_library(q, limit, library)
    # 关键：通道「执行成功但 0 命中」≠「通道不可用」。
    # search_es / search_http 的 error 为空串才代表通道真的跑通了（哪怕没匹配到视频文件）；
    # 旧代码只看有没有 rows，把「关键词在文件名里搜不到」误报成「通道暂时不可用」。
    es_ok = not es_error          # es.exe 存在且执行成功（0 命中也算成功）
    http_ok = not http_error      # HTTP 已配置且请求成功（0 命中也算成功）
    channel_ok = es_ok or http_ok
    es_ready = es_exe().is_file()
    if channel_ok:
        # 至少一条全盘通道是好的，只是这个关键词没在整机文件名里命中。
        # 结果仍来自媒体库（source=library），但绝不能说通道挂了。
        hint = "全盘检索未找到匹配的视频文件（Everything 通道正常），以下是本程序媒体库的结果"
        degraded = False
    elif es_ready or http_base():
        # 配了通道但本次调用真失败（超时 / IPC 连不上 / HTTP 错误）。
        hint = "全盘检索通道暂时不可用，已自动降级为「本程序媒体库检索」"
        detail = es_error or http_error
        if detail:
            hint += f"（{detail}）"
        degraded = True
    else:
        # 根本没配任何全盘通道。
        hint = "Everything 未启用命令行/HTTP，已自动降级为「本程序媒体库检索」"
        degraded = True
    return {
        "ok": True, "rows": rows, "source": "library", "hint": hint,
        "capabilities": capabilities(),
        "degraded": degraded,
        "channel_ok": channel_ok,
    }
