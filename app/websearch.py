"""网页搜索：直接抓 AGE 动漫 / Seeduck 的搜索页，把结果转成可点的卡片。

为什么不是「内嵌浏览器」：本程序是 pywebview + WebView2 单页界面，
把第三方站点整个嵌进来要处理登录、广告、脚本冲突，代价远大于收益。
这里只取搜索结果这一层——标题、详情页链接、封面、年份/类型——渲染成卡片，
点卡片用系统默认浏览器打开详情页（:func:`app.api.Api.open_url`）。

解析规则与封面抓取共用同一套选择器（见 ``app/covers.py``）：
- AGE：``<div class="card cata_video_item">`` 切块，块内 ``<a class="d-block" title>``
  是详情页，``data-original`` 是真实封面（``src`` 是所有结果共用的占位图，不能用）。
- Seeduck：``<a class="image" href><img src>`` + 同条目 ``<h2>`` 短标题。

两个站点的抓取都在线程里跑、各有超时预算：任一站点挂了只影响它自己的结果，
调用方仍然能拿到另一边的数据 + 该站点的搜索页链接（前端据此展示「去站点搜索」）。
"""
from __future__ import annotations

import html
import re
import threading
import time
import urllib.parse

from . import covers

SOURCE_LABELS = {"agedm": "AGE动漫", "seeduck": "Seeduck"}
SOURCE_HOSTS = {"agedm": "https://www.agedm.io", "seeduck": "https://seeduck.cc"}

# 单次网页搜索的总预算：两个站点并行，最慢的那个也不能把界面卡住
SEARCH_BUDGET_SEC = 8.0
PER_SOURCE_TIMEOUT = (2.5, 5.0)
MAX_ITEMS_PER_SOURCE = 24

# AGE 详情页锚点：href 在前、class 与 title 在后（实测顺序，别写死 class 在前）
_AGE_HREF_RE = re.compile(
    r'<a\b[^>]*href="([^"]+)"[^>]*class="[^"]*d-block[^"]*"[^>]*title="([^"]+)"', re.S)
_AGE_STATUS_RE = re.compile(r'<span class="video_play_status[^"]*">([^<]*)</span>')
# Seeduck 条目锚点
_SEED_HREF_RE = re.compile(r'<a\b[^>]*class="image"[^>]*href="([^"]*)"', re.S)
_SEED_META_RE = re.compile(r"<li>\s*([0-9]{4}\s*/[^<]{0,120})</li>")
_TAG_RE = re.compile(r"<[^>]+>")

_CACHE_TTL = 45.0
_CACHE: dict[str, tuple[float, dict]] = {}
_CACHE_LOCK = threading.Lock()


def search_url(source: str, keyword: str) -> str:
    kw = str(keyword or "").strip()
    if source == "agedm":
        return covers.AGEDM_SEARCH + "?" + urllib.parse.urlencode({"query": kw})
    return covers.SEEDUCK_SEARCH.format(kw=urllib.parse.quote(kw, safe=""))


def _absolute(source: str, href: str) -> str:
    text = html.unescape(str(href or "").strip())
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("http://"):
        # AGE 给的详情页链接是 http，升级到 https 免得浏览器拦混合内容
        return "https://" + text[len("http://"):]
    if text.startswith("https://"):
        return text
    if text.startswith("/"):
        return SOURCE_HOSTS.get(source, "") + text
    return ""


def _clean_title(text: str) -> str:
    return html.unescape(_TAG_RE.sub("", str(text or ""))).replace("#", "").strip()


def parse_agedm(body: str) -> list[dict]:
    """AGE 搜索结果页 → 卡片列表。"""
    out: list[dict] = []
    seen: set[str] = set()
    for block in covers._blocks(body):
        links = _AGE_HREF_RE.findall(block)
        if not links:
            # 站点改版时退一步：块里任意带 title 的锚点也算
            fallback = covers._TITLE_RE.findall(block)
            if not fallback:
                continue
            href, title = "", html.unescape(fallback[0]).strip()
        else:
            href, title = links[0][0], links[0][1]
        title = _clean_title(title)
        if not title:
            continue
        url = _absolute("agedm", href)
        key = title + "|" + url
        if key in seen:
            continue
        seen.add(key)
        images = covers._age_candidates(block)
        status = _AGE_STATUS_RE.search(block)
        out.append({
            "source": "agedm",
            "source_label": SOURCE_LABELS["agedm"],
            "title": title,
            "url": url or search_url("agedm", title),
            "cover": images[0] if images else "",
            "meta": html.unescape(status.group(1)).strip() if status else "",
        })
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out


def parse_seeduck(body: str) -> list[dict]:
    """Seeduck 搜索结果页 → 卡片列表（标题用同条目 <h2>，比 <a title> 干净）。"""
    out: list[dict] = []
    seen: set[str] = set()
    for chunk in covers._SEED_ITEM_RE.findall(body):
        images = [html.unescape(u) for u in covers._SEED_IMG_RE.findall(chunk)
                  if u.startswith("http")]
        heading = covers._SEED_H2_RE.search(chunk)
        if heading:
            title = _clean_title(heading.group(1))
        else:
            titles = covers._TITLE_RE.findall(chunk)
            title = _clean_title(titles[0]) if titles else ""
        if not title:
            continue
        href = _SEED_HREF_RE.search(chunk)
        url = _absolute("seeduck", href.group(1)) if href else ""
        key = title + "|" + url
        if key in seen:
            continue
        seen.add(key)
        meta = _SEED_META_RE.search(chunk)
        out.append({
            "source": "seeduck",
            "source_label": SOURCE_LABELS["seeduck"],
            "title": title,
            "url": url or search_url("seeduck", title),
            "cover": images[0] if images else "",
            "meta": html.unescape(_clean_title(meta.group(1))) if meta else "",
        })
        if len(out) >= MAX_ITEMS_PER_SOURCE:
            break
    return out


def _fetch(source: str, keyword: str, result: dict) -> None:
    """抓一个站点；失败只记录原因，不抛到调用方（另一方结果照常返回）。"""
    url = search_url(source, keyword)
    try:
        resp = covers._get(url, timeout=PER_SOURCE_TIMEOUT, retries=1)
        if resp is None:
            result["error"] = "请求失败（网络不可达或站点拒绝）"
            return
        if getattr(resp, "status_code", 200) >= 400:
            result["error"] = f"站点返回 HTTP {resp.status_code}"
            return
        text = resp.text or ""
        items = parse_agedm(text) if source == "agedm" else parse_seeduck(text)
        result["items"] = items
        if not items:
            result["error"] = "页面结构解析不到结果（站点可能改版）"
    except Exception as exc:  # 网络库异常五花八门，一律降级为「该站点没结果」
        result["error"] = str(exc)[:160]


def search(keyword: str, limit: int = 0, use_cache: bool = True) -> dict:
    """并行抓两个站点，返回统一结构的结果。

    ``{"ok", "keyword", "items", "errors": {source: 原因}, "links": {source: 搜索页}}``
    ``items`` 里每项都带 ``source`` / ``source_label``，前端据此标来源。
    """
    kw = str(keyword or "").strip()
    if not kw:
        return {"ok": False, "error": "请输入关键词", "items": [], "errors": {}, "links": {}}
    cache_key = kw.lower()
    if use_cache:
        with _CACHE_LOCK:
            item = _CACHE.get(cache_key)
            if item and (time.monotonic() - item[0]) < _CACHE_TTL:
                return item[1]

    buckets = {src: {} for src in SOURCE_LABELS}
    threads = [threading.Thread(target=_fetch, args=(src, kw, buckets[src]), daemon=True)
               for src in SOURCE_LABELS]
    started = time.monotonic()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(0.2, SEARCH_BUDGET_SEC - (time.monotonic() - started)))

    items: list[dict] = []
    errors: dict[str, str] = {}
    for src in SOURCE_LABELS:
        bucket = buckets[src]
        if bucket.get("items"):
            items.extend(bucket["items"])
        if bucket.get("error"):
            errors[src] = bucket["error"]
        if not bucket.get("items") and not bucket.get("error"):
            errors[src] = "超时未返回"
    # 黑名单同样作用于搜索结果：用户不想看到的名称在两站结果里也不展示
    from . import blacklist
    if blacklist.active():
        items = [it for it in items if not blacklist.hit(it.get("title"))]
    try:
        size = int(limit)
    except (TypeError, ValueError):
        size = 0
    if size > 0:
        items = items[:size]

    payload = {
        "ok": True, "keyword": kw, "items": items, "errors": errors,
        "links": {src: search_url(src, kw) for src in SOURCE_LABELS},
        "labels": dict(SOURCE_LABELS),
    }
    if use_cache:
        with _CACHE_LOCK:
            if len(_CACHE) > 32:
                _CACHE.clear()
            _CACHE[cache_key] = (time.monotonic(), payload)
    return payload


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
