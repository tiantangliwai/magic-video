"""Everything 快速检索：本机全盘文件名检索（可降级）。

本机实测（2026-11-15）：``D:\\tool\\Everything`` 只有 Everything.exe，
没有官方命令行工具 es.exe；进程以 Services 会话常驻，未开 HTTP 服务、无 IPC 窗口。
因此按三级降级设计，**没有任何外部依赖也能用**：

1. es.exe（可在设置里指定路径）→ ``es.exe -n <limit> <query>``，最快、结果最全；
2. Everything HTTP 服务（可在设置里填地址）→ ``?s=<query>&j=1`` 拿 JSON；
3. 库内 SQL：直接检索本程序已扫描的 media_files，毫秒级，功能永远可用。

所有外部调用都有短超时；失败只降级、不抛异常、不阻塞 UI。
"""
from __future__ import annotations

import json
import subprocess
import urllib.parse
from pathlib import Path

import requests

from . import config, db

DEFAULT_ES_PATH = r"D:\tool\Everything\es.exe"
VIDEO_EXTS = tuple(sorted(config.VIDEO_EXTS))
ES_TIMEOUT = 6
HTTP_TIMEOUT = (1.5, 3.0)


def es_exe() -> Path:
    configured = str(db.get_setting("everything_es_path", DEFAULT_ES_PATH) or "").strip()
    return Path(configured)


def http_base() -> str:
    return str(db.get_setting("everything_http_url", "") or "").strip().rstrip("/")


def capabilities() -> dict:
    """给设置页展示：当前能用到哪一级。"""
    es = es_exe()
    return {
        "es_path": str(es),
        "es_available": es.is_file(),
        "http_url": http_base(),
        "http_configured": bool(http_base()),
        "library_fallback": True,
        "installed": Path(r"D:\tool\Everything\Everything.exe").is_file(),
    }


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
            text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0 and not proc.stdout:
            return [], f"es.exe 退出码 {proc.returncode}"
        rows = []
        for line in (proc.stdout or "").splitlines():
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
        resp = requests.get(url, timeout=HTTP_TIMEOUT)
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
                "capabilities": capabilities()}

    rows, http_error = search_http(q, limit, library)
    if rows:
        return {"ok": True, "rows": rows, "source": "everything-http", "hint": "来自 Everything HTTP 服务",
                "capabilities": capabilities()}

    rows, _ = search_library(q, limit, library)
    hint = "Everything 未启用命令行/HTTP，已自动降级为「本程序媒体库检索」"
    if es_error and "不存在" not in es_error:
        hint += f"（{es_error}）"
    return {
        "ok": True, "rows": rows, "source": "library", "hint": hint,
        "capabilities": capabilities(),
        "degraded": True,
    }
