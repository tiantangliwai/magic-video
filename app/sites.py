"""站点适配层：AGE动漫 / Seeduck 的 HTML 解析**只有这一份**。

为什么单独成层：封面抓取（``app/covers.py``）与网页搜索（``app/websearch.py``）
都要读同样的两个站点。以前两边各写一套正则，靠人肉保持一致——站点一改版就得
改两处，漏一处就静默失效。现在所有选择器、分块规则、URL 补全、标题清洗都收敛到
本模块，两边只做「怎么用这些条目」的差异逻辑：

- 封面抓取：需要**打分选一**（``covers.exact_pick`` / ``strict_pick``），要全部候选图。
- 网页搜索：需要**按顺序全量返回**，要详情页链接与年份/类型等 meta。

硬约束（改这里就等于同时改两个功能，必须连带跑 ``tests/test_covers.py``、
``tests/test_manual_cover.py``、``tests/test_websearch.py``）：

1. AGE 的 ``data-original`` 才是真实封面；``src`` 是所有搜索结果共用的占位图。
   ``data-original`` 常是百度图片代理 ``...&src=<urlencoded>``，其 ``src`` 参数
   才是原始 CDN 直连地址，优先用它。
2. AGE 结果块用 ``finditer`` + 字符串末尾兜底，保证最后一块（常为精确匹配）不丢。
3. Seeduck 的标题用同条目 ``<h2>`` 短标题，``<a title>`` 常带日文副标题会干扰打分。

解析失败不再静默：每次解析都会记一条统计（见 :func:`health`），空结果会被计数，
调用方据此给出「站点可能改版」的提示。
"""
from __future__ import annotations

import html
import re
import threading
import urllib.parse

AGEDM_SEARCH = "https://www.agedm.io/search"
SEEDUCK_SEARCH = "https://seeduck.cc/s/{kw}/"
# Seeduck 旧分类页兜底池（封面抓取用；网页搜索不需要）
SEEDUCK_PAGES = (   # 兼容旧契约的兜底池：只有搜索页全部失败时才用
    "https://seeduck.cc/",
    "https://seeduck.cc/categories/1/movies/",
    "https://seeduck.cc/categories/2/movies/",
    "https://seeduck.cc/categories/1/types/1/movies/",
    "https://seeduck.cc/categories/2/types/25/movies/",
)

SOURCE_LABELS = {"agedm": "AGE动漫", "seeduck": "Seeduck"}
SOURCE_HOSTS = {"agedm": "https://www.agedm.io", "seeduck": "https://seeduck.cc"}

_BAIDU_PROXY_RE = re.compile(r"^https?://gimg\d*\.baidu\.com/", re.I)
_RAW_CDN_HOSTS = ("cdn.aqdstatic.com",)

# ---- AGE：搜索结果块与字段 ----
_BLOCK_RE = re.compile(r'<div class="card cata_video_item\b.*?(?=<div class="card cata_video_item\b|\Z)',
                       re.S)
_TITLE_RE = re.compile(r'<a\b[^>]*title="([^"]+)"', re.S)
_ORIG_RE = re.compile(r'data-original="([^"]+)"')
_SRC_RE = re.compile(r'class="video_thumbs[^"]*"\s+src="([^"]+)"')
# 详情页锚点：href 在前、class 与 title 在后（实测顺序，别写死 class 在前）
_AGE_HREF_RE = re.compile(
    r'<a\b[^>]*href="([^"]+)"[^>]*class="[^"]*d-block[^"]*"[^>]*title="([^"]+)"', re.S)
_AGE_STATUS_RE = re.compile(r'<span class="video_play_status[^"]*">([^<]*)</span>')

# ---- Seeduck：条目块与字段 ----
# 条目块（<a class="image"> 到下一个同级块），块内 <h2> 是干净短标题
_SEED_ITEM_RE = re.compile(
    r'<a\b[^>]*class="image"[^>]*>.*?(?=<a\b[^>]*class="image"|\Z)', re.S)
_SEED_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"', re.S)
_SEED_H2_RE = re.compile(r'<h2>(.*?)</h2>', re.S)
_SEED_HREF_RE = re.compile(r'<a\b[^>]*class="image"[^>]*href="([^"]*)"', re.S)
_SEED_META_RE = re.compile(r"<li>\s*([0-9]{4}\s*/[^<]{0,120})</li>")
# 旧页面结构（只有 <a title class="image"><img src>）
_SEED_RE = re.compile(
    r'<a[^>]+title="([^"]+)"[^>]*class="image"[^>]*>\s*<img[^>]+src="([^"]+)"',
    re.S,
)

_TAG_RE = re.compile(r"<[^>]+>")

# ---- 解析健康度（站点改版时不再静默） ----
_HEALTH_LOCK = threading.Lock()
_HEALTH = {"agedm": {"parsed": 0, "empty": 0}, "seeduck": {"parsed": 0, "empty": 0}}


def _bump(source: str, count: int) -> None:
    with _HEALTH_LOCK:
        row = _HEALTH.setdefault(source, {"parsed": 0, "empty": 0})
        row["parsed"] += 1
        if not count:
            row["empty"] += 1


def health() -> dict:
    """返回各站点的解析统计，供界面显示「解析失败」提示。

    ``empty`` 连续增长且 ``parsed`` 同比增加，基本可以判定站点改版（不是网络问题，
    网络失败在抓取层就以 error 返回了，不会走到解析）。
    """
    with _HEALTH_LOCK:
        return {k: dict(v) for k, v in _HEALTH.items()}


def reset_health() -> None:
    with _HEALTH_LOCK:
        _HEALTH.clear()


def unescape(text: str) -> str:
    return html.unescape(str(text or "")).strip()


def clean_title(text: str) -> str:
    """去标签、去 Seeduck 的 ``#`` 锚点符号、反转义。"""
    return html.unescape(_TAG_RE.sub("", str(text or ""))).replace("#", "").strip()


def absolute_url(source: str, href: str) -> str:
    """相对链接补全；``http://`` 升级为 ``https://`` 免得浏览器拦混合内容。"""
    text = html.unescape(str(href or "").strip())
    if not text:
        return ""
    if text.startswith("//"):
        return "https:" + text
    if text.startswith("http://"):
        return "https://" + text[len("http://"):]
    if text.startswith("https://"):
        return text
    if text.startswith("/"):
        return SOURCE_HOSTS.get(source, "") + text
    return ""


def search_url(source: str, keyword: str) -> str:
    kw = str(keyword or "").strip()
    if source == "agedm":
        return AGEDM_SEARCH + "?" + urllib.parse.urlencode({"query": kw})
    return SEEDUCK_SEARCH.format(kw=urllib.parse.quote(kw, safe=""))


def blocks(body: str) -> list[str]:
    """AGE 搜索页结果块；用字符串末尾兜底，保证最后一块（常为精确匹配）不被丢掉。"""
    return [m.group(0) for m in _BLOCK_RE.finditer(body)]


def image_urls(block: str) -> list[str]:
    """AGE 块内的**全部**图片地址（``data-original`` + ``video_thumbs`` 的 ``src``）。

    这是 ``covers.parse_agedm`` 的历史契约，保留 ``src`` 缩略图作为候选；真实下载
    走 :func:`age_candidates`，那里已排除占位图。
    """
    urls = [html.unescape(u) for u in (_ORIG_RE.findall(block) + _SRC_RE.findall(block))]
    return list(dict.fromkeys(u for u in urls if u.startswith("http")))


def age_candidates(block: str) -> list[str]:
    """只取 data-original 的真实封面，彻底排除所有结果共用的占位 src。

    data-original 是百度图片代理 ``...&src=<urlencoded>``，其 src 参数就是
    原始 CDN 直连地址：优先放它，其次放代理本身。两者都比占位图可靠。
    """
    out: list[str] = []
    for raw in _ORIG_RE.findall(block):
        url = html.unescape(raw)
        m = re.search(r"[?&]src=([^&]+)", url)
        if m and _BAIDU_PROXY_RE.match(url):
            try:
                origin = urllib.parse.unquote(m.group(1))
                if origin.startswith("http") and any(h in origin for h in _RAW_CDN_HOSTS):
                    out.append(origin)
            except (ValueError, UnicodeDecodeError):
                pass
        if url.startswith("http"):
            out.append(url)
    return list(dict.fromkeys(out))


def age_entries(body: str) -> list[dict]:
    """AGE 搜索结果页 → 条目列表。

    每条返回 ``{title, href, url, status, images, covers}``：

    - ``title``：块内第一个带 ``title`` 的锚点文本（**未**做 clean，保留 ``#``）。
    - ``href`` / ``url``：详情页链接（相对路径已补全），没有则为空串。
    - ``status``：``video_play_status`` 文本，如「第10集(完结)」。
    - ``images``：全部图片地址（含占位 src），见 :func:`image_urls`。
    - ``covers``：真实封面候选，见 :func:`age_candidates`。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for block in blocks(body):
        titles = _TITLE_RE.findall(block)
        if not titles:
            continue
        links = _AGE_HREF_RE.findall(block)
        href = links[0][0] if links else ""
        raw_title = unescape(titles[0])
        if not raw_title:
            continue
        url = absolute_url("agedm", href)
        status = _AGE_STATUS_RE.search(block)
        entry = {
            "title": raw_title,
            "href": href,
            "url": url,
            "status": unescape(status.group(1)) if status else "",
            "images": image_urls(block),
            "covers": age_candidates(block),
        }
        key = raw_title + "|" + url
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    _bump("agedm", len(out))
    return out


def seeduck_entries(body: str, *, require_image: bool = False,
                    legacy: bool = True) -> list[dict]:
    """Seeduck 搜索结果页 → 条目列表。

    标题优先用同条目 ``<h2>`` 的干净短标题；没有 h2（旧首页/分类页）时回退到
    ``<a title>``——后者常带日文副标题，直接参与打分会干扰匹配。

    ``require_image=True``：丢掉没有封面的条目（封面抓取只要图）。
    ``legacy=True``：新结构一条都没解析到时，再试旧页面结构。
    """
    out: list[dict] = []
    seen: set[str] = set()
    for chunk in _SEED_ITEM_RE.findall(body):
        images = [html.unescape(u) for u in _SEED_IMG_RE.findall(chunk)
                  if u.startswith("http")]
        if require_image and not images:
            continue
        heading = _SEED_H2_RE.search(chunk)
        if heading:
            title = clean_title(heading.group(1))
        else:
            titles = _TITLE_RE.findall(chunk)
            title = unescape(titles[0]) if titles else ""
        if not title:
            continue
        href = _SEED_HREF_RE.search(chunk)
        url = absolute_url("seeduck", href.group(1)) if href else ""
        key = title + "|" + url
        if key in seen:
            continue
        seen.add(key)
        meta = _SEED_META_RE.search(chunk)
        out.append({
            "title": title,
            "href": href.group(1) if href else "",
            "url": url,
            "meta": clean_title(meta.group(1)) if meta else "",
            "images": images,
            "covers": images[:1],
        })
    if out or not legacy:
        _bump("seeduck", len(out))
        return out
    # 兼容旧页面结构（只有 <a title class="image"><img src>）
    for name, url in _SEED_RE.findall(body):
        if not url.startswith("http"):
            continue
        title = unescape(name)
        key = title + "|" + url
        if not title or key in seen:
            continue
        seen.add(key)
        out.append({
            "title": title, "href": "", "url": "", "meta": "",
            "images": [html.unescape(url)], "covers": [html.unescape(url)],
        })
    _bump("seeduck", len(out))
    return out
