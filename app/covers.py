"""动漫封面：后台异步抓取、本地缓存、入库。

设计约束（与中文名匹配一致）：
- **不是定时任务**。只能由用户在界面点「抓取封面」或 CLI 主动触发一次后台任务。
- 全程在 daemon 线程里跑，绝不阻塞导入 / 扫描 / 播放 / UI。
- 短超时、整批失败快速降级；任何网络异常都只记状态，不抛给调用方。
- 图片只写进本程序自己的 ``COVER_DIR``，绝不修改用户视频目录。

数据源（2026-11-15 二次实测，用户给出确切搜索 URL 后修正）：
- 主源 AGE动漫 ``https://www.agedm.io/search?query=<关键词>``：结果块
  ``<div class="card cata_video_item">`` 里 ``<a title="...">`` 为标题，
  ``img.video_thumbs`` 的 ``data-original`` 是真实封面（百度图片代理，
  形如 ``https://gimg3.baidu.com/gimg/app=2001&...&src=<urlencoded CDN>``），
  **``src`` 是所有结果共用的占位图**，绝不能当封面候选。
  从代理 URL 的 ``src=`` 参数可解出原始 CDN 直连地址，最稳。
  精确匹配常常排在后面（「葬送的芙莉莲」第 2 块、「进击的巨人」第 3 块），
  所以必须按标题精确优先、切块不能丢最后一条。
- 备源 SeedHub ``https://seeduck.cc/s/<关键词>/``：**搜索页实测 HTTP 200 可用**
  （旧注释「搜索被 CF 拦」是错的）。结构为
  ``<a title="长标题" class="image"><img src="真实封面">`` + 同条目 ``<h2>`` 短标题。
  改用按关键词搜索替代固定首页池，避免用别的作品的图凑数。
- 本机走系统 HTTPS 代理且为 MITM 证书，Python 必须 ``verify=False`` 才能连通
  （curl 用系统 CA 所以能通）；这是本机环境事实，不是放松校验的通用理由。
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import threading
import time
import urllib.parse
from pathlib import Path

from . import config, db, sites, textmatch

# 站点常量与选择器统一由 ``app/sites.py``（唯一解析层）提供，这里只做别名转出：
# 旧调用方与单测直接引用 ``covers._blocks`` / ``covers._TITLE_RE`` / ``covers.SEEDUCK_PAGES``，
# 保留这些名字可避免为了「重构」而大面积改测试。
AGEDM_SEARCH = sites.AGEDM_SEARCH
SEEDUCK_SEARCH = sites.SEEDUCK_SEARCH
SEEDUCK_PAGES = sites.SEEDUCK_PAGES
_BAIDU_PROXY_RE = sites._BAIDU_PROXY_RE
_RAW_CDN_HOSTS = sites._RAW_CDN_HOSTS
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = (2.5, 5.0)          # connect, read
IMAGE_TIMEOUT = (2.5, 6.0)
SEARCH_TIMEOUT = (2.0, 4.0)   # 关键词多轮尝试时用更短的超时，避免单部作品卡太久
SEARCH_BUDGET_SEC = 16.0      # 单个作品的搜索总预算；超了就降级/放弃，任务不阻塞
MAX_AGEDM_QUERIES = 4         # 主源最多试几个关键词
MAX_BYTES = 3 * 1024 * 1024   # 单张封面上限，防止异常响应撑爆磁盘

# 切块用 finditer + \Z 兜底终点：旧版用 ``(?=...|</main>)`` 的 split 捕获组，
# 真实页面最后一块后面没有 </main>，会被整块丢掉；而精确匹配常恰好是最后一块。
_BLOCK_RE = sites._BLOCK_RE
_TITLE_RE = sites._TITLE_RE
_ORIG_RE = sites._ORIG_RE
_SRC_RE = sites._SRC_RE
_SEED_RE = sites._SEED_RE
_SEED_ITEM_RE = sites._SEED_ITEM_RE
_SEED_IMG_RE = sites._SEED_IMG_RE
_SEED_H2_RE = sites._SEED_H2_RE
_TAG_RE = sites._TAG_RE

_JOB = {
    "running": False,
    "total": 0,
    "done": 0,
    "ok": 0,
    "failed": 0,
    "current": "",          # 当前正在抓取的作品名，供界面显示实时进度
    "last_query": "",       # 上一次实际用的搜索关键词（诊断「为什么没抓到」）
    "last_hit": "",         # 上一次命中的站点标题
    "recent_failures": [],  # 最近若干条失败原因，界面里可直接看到
    "source_hits": {},
    "started_at": "",
    "finished_at": "",
    "error": "",
    "cancel": False,
}
_job_lock = threading.Lock()


def _norm_key(title: str) -> str:
    """作品名归一化，作为封面缓存与 subjects 的匹配键。"""
    return re.sub(r"\s+", "", str(title or "")).strip().lower()


def cache_path(title: str) -> Path:
    """封面本地文件名：用哈希避免中文/特殊字符带来的路径问题。"""
    key = _norm_key(title)
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return config.COVER_DIR / f"{digest}.jpg"


# 剧集墙里同一部番可能按「… 01」「… 02」拆成几十个分组，不该各抓一张封面
_EP_SUFFIX_RE = re.compile(
    r"(?:\s*\[\s*\d{1,3}\s*\]|\s*第\s*\d{1,3}\s*[集话話]|\s*EP?\s*\d{1,3}|"
    r"\s*[-_]?\s*(?<!\d)\d{1,3})\s*$",
    re.IGNORECASE,
)


def cover_key(title: str) -> str:
    """把展示标题归一成「作品名」：去掉结尾的季标记与集号，作为搜索词。"""
    original = str(title or "").strip()
    text = _strip_season(original)
    previous = None
    while text and previous != text:
        previous = text
        text = _EP_SUFFIX_RE.sub("", text).strip(" -_·:：")
    return text or original


# 结尾的季标记。``cover_key()`` 故意把它去掉（同部番各集共用一个搜索词），
# 但**封面缓存键不能去季**：否则「哥布林杀手」和「哥布林杀手 S2」会哈希到同一个
# 文件名，后设的那张直接把先设的覆盖掉（用户反馈「两季封面强制一致」）。
_SEASON_TAIL_RE = re.compile(
    r"(?:[\s·・\-_:：]*第\s*([一二三四五六七八九十两\d]{1,3})\s*[季期]"
    r"|[\s·・\-_:：]*[Ss](?:eason)?\s*0?(\d{1,2})"
    r"|[\s·・\-_:：]*(\d{1,2})\s*(?:st|nd|rd|th)\s*[Ss]eason)\s*$",
    re.IGNORECASE,
)
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_number(text: str) -> int:
    """「二」「十二」「20」→ 数字；认不出返回 0。"""
    text = str(text or "").strip()
    if not text:
        return 0
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_NUM.get(head, 1) if head else 1
        ones = _CN_NUM.get(tail, 0) if tail else 0
        return tens * 10 + ones
    return _CN_NUM.get(text, 0)


def season_key(title: str) -> str:
    """封面缓存键：**保留季**、去掉集号，季标记归一成 ``#s{季号}``。

    没有季标记时与 :func:`cover_key()` 完全一致，所以存量缓存/资产不用迁移；
    带季的标题（``… S2`` / ``… 第二季`` / ``… · 第 2 季``）会得到独立键，
    两季可以各存一张封面。多种季写法归一到同一季号，避免同一季存出两份。
    """
    original = str(title or "").strip()
    text = original
    season = 0
    while text:
        # 季标记优先：先认「… S2」再剥集号，否则 `… S2 - 01` 的集号规则会把
        # S2 里的 2 当成集号吃掉（剩下「… S」）。
        match = _SEASON_TAIL_RE.search(text)
        if match:
            number = _cn_number(match.group(1) or match.group(2) or match.group(3) or "")
            if number > 0:
                if not season:
                    season = number
                text = text[:match.start()].strip(" ·・-_:：")
                continue
        trimmed = _EP_SUFFIX_RE.sub("", text).strip(" -_·:：")
        if trimmed and trimmed != text:
            text = trimmed
            continue
        break
    key = cover_key(text) if text else ""
    if not key:
        return cover_key(original)
    return f"{key}#s{season}" if season else key


_SESSION = None
_SESSION_LOCK = threading.Lock()


def _session():
    """复用连接：本机 HTTPS 代理（MITM）偶发 RemoteDisconnected，短重试最有效。"""
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            import requests
            _SESSION = requests.Session()
            _SESSION.headers.update({"User-Agent": UA, "Referer": "https://www.agedm.io/"})
        return _SESSION


def _get(url: str, *, timeout=TIMEOUT, stream: bool = False, retries: int = 2):
    """单次请求；网络/代理抖动时最多重试 retries 次，非 200 不重试（交给调用方换源）。"""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    last = None
    for attempt in range(max(1, retries + 1)):
        try:
            return _session().get(url, timeout=timeout, verify=False, stream=stream)
        except Exception as exc:
            last = exc
            if attempt < retries:
                time.sleep(0.25 * (attempt + 1))
    raise last


def _strip_season(title: str) -> str:
    """「XX 第二季」搜不到时用主标题再试一次。"""
    return re.sub(r"\s*(第[一二三四五六七八九十\d]{1,3}\s*[季期]|[Ss]\d{1,2})\s*$", "", str(title or "")).strip()


def _short_title(a: str) -> bool:
    """归一化后（去季）极短的标题：1-2 字禁止模糊子串，只能精确相等。

    单字母 / 单汉字 / 两字标题做裸子串匹配几乎必然误命中：K 会命中「花宵道中
    ...Flowered Skin...」里的 k，「破」命中「天元突破」，「终」命中「终结的炽
    天使」。这类标题只有精确同名才算命中（用户 2026-11-19 反馈）。
    """
    return len(_norm_key(_strip_season(a))) <= 2


def _fuzzy_match(candidate: str, want: str) -> bool:
    a, b = _norm_key(candidate), _norm_key(want)
    if not a or not b:
        return False
    # 短标题只认精确相等，不让子串包含糊到别的作品
    if _short_title(candidate) or _short_title(want):
        return a == b or _norm_key(_strip_season(a)) == _norm_key(_strip_season(b))
    return a == b or b in a or a in b or _norm_key(_strip_season(a)) == _norm_key(_strip_season(b))


# 分块与封面候选的唯一实现在 app/sites.py；这里保持旧名字给内部与单测使用。
_blocks = sites.blocks
_age_candidates = sites.age_candidates


def parse_agedm(body: str, want: str) -> tuple[list[str], str] | None:
    """从 AGE动漫 搜索结果里挑出与 want 最匹配的 (候选封面URL列表, 命中标题)。

    两轮：先找**标题完全一致**的结果（避免「葬送的芙莉莲」误拿到「第二季」封面），
    没有完全一致时才放宽到包含匹配。解析本身由 ``sites.age_entries`` 完成。
    """
    blocks = [(e["title"], e["images"]) for e in sites.age_entries(body) if e["images"]]

    key = _norm_key(want)
    for name, urls in blocks:
        if _norm_key(name) == key:
            return urls, name
    # 次优：去掉「第二季 / S2」后完全一致（站点只收录续季时也能拿到该系列封面）
    for name, urls in blocks:
        if _norm_key(_strip_season(name)) == key:
            return urls, name
    for name, urls in blocks:
        if _fuzzy_match(name, want):
            return urls, name
    return None


def parse_seeduck(body: str, want: str) -> tuple[list[str], str] | None:
    """从 SeedHub 搜索页里按 <h2> 短标题（旧页回退 <a title>）配对封面。

    精确匹配优先，避免「零之使魔」被「零之使魔2 ～双月的骑士～」抢走。
    """
    pairs = _seed_pairs(body)
    hit = exact_pick(pairs, want)
    if hit:
        return [hit[0][0]], hit[1]
    hit = strict_pick(pairs, want)
    if hit:
        return [hit[0][0]], hit[1]
    for name, urls in pairs:
        if _fuzzy_match(name, want) and urls:
            return [urls[0]], name
    return None


# 站点改版时精确 class 会失效：再准备一套「锚点 title + 任意图片地址」的通用兜底。
# 注意不含裸 src：AGE 的 src 是所有结果共用的占位图，收进来会污染候选。
_GENERIC_ANCHOR_RE = re.compile(r'<a\b[^>]*title="([^"]+)"[^>]*>(.*?)</a>', re.S)
_GENERIC_IMG_RE = re.compile(r'(?:data-original|data-src|data-lazy-src)="([^"]+)"')


def _seed_pairs(body: str) -> list[tuple[str, list[str]]]:
    """Seeduck 搜索页条目 → (短标题, [真实封面 URL])。

    解析由 ``sites.seeduck_entries`` 统一完成；这里只保留「只要第一张图」的封面语义。
    """
    out: list[tuple[str, list[str]]] = []
    for entry in sites.seeduck_entries(body, require_image=True):
        if entry["title"] and entry["images"]:
            out.append((entry["title"], [entry["images"][0]]))
    return out


def _pairs(body: str, seed_style: bool = False) -> list[tuple[str, list[str]]]:
    """把搜索结果页拆成 [(标题, 候选图片URL列表)]；先精确规则，失败再通用兜底。"""
    out: list[tuple[str, list[str]]] = _seed_pairs(body) if seed_style else []
    if not out and not seed_style:
        for block in _blocks(body):
            titles = _TITLE_RE.findall(block)
            if not titles:
                continue
            name = html.unescape(titles[0]).strip()
            urls = _age_candidates(block)   # 只取 data-original 解出的真实封面
            if urls:
                out.append((name, urls))
    if out:
        return out
    for name, chunk in _GENERIC_ANCHOR_RE.findall(body):
        urls = [html.unescape(u) for u in _GENERIC_IMG_RE.findall(chunk)]
        urls = list(dict.fromkeys(u for u in urls if u.startswith("http")))
        if urls:
            out.append((html.unescape(name).strip(), urls))
    return out


_SEQUEL_EXTRA_RE = re.compile(
    r"[\d\s第季期部篇上下中後后剧场版完结篇ovaoadsppartⅡⅢⅣⅤⅥⅦ"
    r"·:：\-_（）()\[\]]+", re.IGNORECASE,
)


_SEED_CACHE_TTL = 1800.0        # 一次抓取任务内共享，半小时后自动过期
_SEED_CACHE: dict[str, tuple[float, list[tuple[str, list[str]]]]] = {}
_SEED_CACHE_LOCK = threading.Lock()
# 按关键词的搜索页缓存：同一批作品常共用关键词，避免重复请求
_SEED_SEARCH_CACHE: dict[str, tuple[float, list[tuple[str, list[str]]]]] = {}
_SEED_SEARCH_LOCK = threading.Lock()


def clear_seed_cache() -> None:
    with _SEED_CACHE_LOCK:
        _SEED_CACHE.clear()
    with _SEED_SEARCH_LOCK:
        _SEED_SEARCH_CACHE.clear()


def seed_pairs() -> list[tuple[str, list[str]]]:
    """备源页面整批共享：每页只抓一次，避免 300 部作品 × 5 页把代理打挂。

    解析出的空结果不缓存（可能是临时 403 / Cloudflare），下次任务会重试。
    """
    out: list[tuple[str, list[str]]] = []
    for page in SEEDUCK_PAGES:
        with _SEED_CACHE_LOCK:
            cached = _SEED_CACHE.get(page)
        if cached and time.monotonic() - cached[0] < _SEED_CACHE_TTL:
            out.extend(cached[1])
            continue
        pairs: list[tuple[str, list[str]]] = []
        try:
            resp = _get(page, timeout=SEARCH_TIMEOUT, retries=1)
            if resp.status_code == 200:
                pairs = _pairs(resp.text, seed_style=True)
        except Exception:
            pairs = []
        if pairs:                      # 空结果不缓存，下一轮换个时机再试
            with _SEED_CACHE_LOCK:
                _SEED_CACHE[page] = (time.monotonic(), pairs)
            out.extend(pairs)
    return out


def pick_from_pairs(pairs: list[tuple[str, list[str]]], queries: list[str],
                    threshold: float = 0.62) -> tuple[list[str], str, str, float] | None:
    """从 (标题, 图片URL) 列表里挑与关键词最像的一条。"""
    if not queries:
        return None
    best = None
    for name, urls in pairs:
        nk = textmatch.normalize(name)
        for query in queries:
            qk = textmatch.normalize(query)
            score = textmatch.match_score(name, query)
            need = max(threshold, 0.78 if len(qk) <= 3 else 0.0)
            if score < need:
                continue
            # 短词容易误配：「Zero」会把《终结者 Zero》当成《Re:Zero》
            strict = len(qk) <= 3 or (not re.search(r"[\u4e00-\u9fff]", query) and len(qk) <= 6)
            if strict and nk != qk:
                extra = nk.replace(qk, "", 1)
                if not extra or not _SEQUEL_EXTRA_RE.fullmatch(extra):
                    continue
            rank = (score, 0.15 if nk == qk else 0)
            if best is None or rank > best[0]:
                best = (rank, urls, name, query, score)
    if not best:
        return None
    return best[1], best[2], best[3], best[4]


def rank_pick(body: str, queries: list[str], *, seed_style: bool = False,
              threshold: float = 0.62) -> tuple[list[str], str, str, float] | None:
    """一页 HTML 的便捷入口：先拆 (标题, URL) 再按相似度挑。"""
    return pick_from_pairs(_pairs(body, seed_style=seed_style), queries, threshold)


def exact_pick(pairs: list[tuple[str, list[str]]], query: str):
    """只认「标题完全一致」或「去掉季标记后完全一致」，不做模糊凑数。

    真实站点里精确匹配常排在结果第 2~3 块，必须优先于任何相似度打分，
    否则「零之使魔」会拿到「零之使魔 第三季」或别的作品的封面。
    """
    key = _norm_key(query)
    if not key:
        return None
    base = _norm_key(_strip_season(query))
    for name, urls in pairs:
        if _norm_key(name) == key and urls:
            return urls, name, query, 1.0
    if base and base != key:
        for name, urls in pairs:
            if _norm_key(name) == base and urls:
                return urls, name, query, 0.98
    return None


def strict_pick(pairs: list[tuple[str, list[str]]], query: str,
                threshold: float = 0.90):
    """严格相似度兜底：高分 + 差值只能是季号/续作后缀，否则宁可不返回。

    与 `pick_from_pairs` 的 0.62 宽松阈值不同，这里用于「按关键词搜到的页面」，
    只接受确实像同一部作品的结果；无关标题一律返回 None，避免抓错图。
    """
    qk = textmatch.normalize(query)
    if not qk or not pairs:
        return None
    best = None
    for name, urls in pairs:
        if not urls:
            continue
        nk = textmatch.normalize(name)
        score = textmatch.match_score(name, query)
        if score < threshold:
            continue
        if nk != qk:
            # 允许「标题 第二季」「标题2」这类续作后缀，但不许出现实义词
            extra = nk.replace(qk, "", 1) if qk in nk else ""
            if qk in nk and nk.startswith(qk):
                if not extra or not _SEQUEL_EXTRA_RE.fullmatch(extra):
                    continue
            elif nk in qk and len(nk) >= 2:
                continue    # 候选是查询的前缀，容易是另一部作品，拒绝
            else:
                continue
        rank = (score, 0.2 if nk == qk else 0)
        if best is None or rank > best[0]:
            best = (rank, urls, name, query, score)
    return best[1:] if best else None


def _seeduck_search(query: str) -> list[tuple[str, list[str]]]:
    """Seeduck 按关键词搜索页（实测 HTTP 200 可用），带短 TTL 缓存。"""
    url = SEEDUCK_SEARCH.format(kw=urllib.parse.quote(query, safe=""))
    cache_key = "search:" + url
    with _SEED_SEARCH_LOCK:
        cached = _SEED_SEARCH_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < _SEED_CACHE_TTL:
        return cached[1]
    pairs: list[tuple[str, list[str]]] = []
    try:
        resp = _get(url, timeout=SEARCH_TIMEOUT, retries=1)
        if resp.status_code == 200:
            pairs = _seed_pairs(resp.text)
    except Exception:
        pairs = []
    if pairs:      # 空结果不缓存，下轮换时机重试
        with _SEED_SEARCH_LOCK:
            _SEED_SEARCH_CACHE[cache_key] = (time.monotonic(), pairs)
    return pairs


def search_cover(title: str) -> dict:
    """同步查一次封面；``{ok, urls, source, hit, query}``。

    策略（2026-11-15 按用户给出的两个确切搜索 URL 重做）：
    1. ``textmatch.query_variants()`` 从原始标题生成短关键词；
    2. 逐关键词请求 ``agedm.io/search?query=``，结果块只取 ``data-original``
       （解出原始 CDN），**标题精确匹配优先**，没有精确才用严格相似度；
    3. AGE 全落空再逐关键词请求 ``seeduck.cc/s/<kw>/`` 搜索页，同样精确优先；
    4. 两个搜索页都无果才退回旧的首页/分类页固定池，且只接受严格匹配；
    5. 有总时间预算与请求数上限，任一环节失败立刻换源，绝不阻塞任务线程。

    旧版把 AGE 共用占位 ``src`` 当候选、用 0.62 相似度在固定池里凑数，
    是「很多封面都是错的」的根因；现在宁可返回未找到，也不拿别的作品的图。
    """
    original = str(title or "").strip()
    if not original:
        return {"ok": False, "error": "空标题"}
    queries = textmatch.query_variants(original, limit=MAX_AGEDM_QUERIES + 1)
    if not queries:
        queries = [cover_key(original)]
    deadline = time.monotonic() + SEARCH_BUDGET_SEC
    net_fail = 0

    def result(hit, source):
        return {"ok": True, "urls": hit[0], "source": source,
                "hit": hit[1], "query": hit[2]}

    # 主源：AGE动漫，短关键词轮番试；每个关键词先精确、再严格兜底
    loose_agedm = None
    for query in queries[:MAX_AGEDM_QUERIES]:
        if time.monotonic() >= deadline or net_fail >= 2:
            break
        try:
            url = AGEDM_SEARCH + "?" + urllib.parse.urlencode({"query": query})
            resp = _get(url, timeout=SEARCH_TIMEOUT, retries=1)
            if resp.status_code != 200:
                continue
            pairs = _pairs(resp.text)
            hit = exact_pick(pairs, query)
            if hit:
                return result(hit, "agedm")
            if loose_agedm is None:
                loose_agedm = strict_pick(pairs, query)
        except Exception:
            net_fail += 1
            continue
    if loose_agedm:
        return result(loose_agedm, "agedm")

    # 备源：Seeduck 按关键词搜索页（实测可用，不再是固定首页池）
    for query in queries[:2]:
        if time.monotonic() >= deadline:
            break
        pairs = _seeduck_search(query)
        hit = exact_pick(pairs, query)
        if hit:
            return result(hit, "seeduck")
        hit = strict_pick(pairs, query)
        if hit:
            return result(hit, "seeduck")

    # 最后兜底：旧首页/分类页固定池，整批共享缓存；只接受严格匹配防止凑数
    pairs = seed_pairs()
    for query in queries:
        hit = exact_pick(pairs, query) or strict_pick(pairs, query)
        if hit:
            return result(hit, "seeduck")
    return {"ok": False, "error": "两个源都没有匹配到封面", "query": queries[0]}


def download_cover(url: str, dest: Path) -> bool:
    """下载并落盘；校验 Content-Type / 大小，异常直接返回 False。"""
    try:
        resp = _get(url, timeout=IMAGE_TIMEOUT, stream=True)
        if resp.status_code != 200:
            return False
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if ctype and not ("image" in ctype or "octet-stream" in ctype):
            return False
        chunks, size = [], 0
        for chunk in resp.iter_content(65536):
            size += len(chunk)
            if size > MAX_BYTES:
                return False
            chunks.append(chunk)
        if size < 512:
            return False
        config.ensure_dirs()
        dest.write_bytes(b"".join(chunks))
        return True
    except Exception:
        return False


def local_cover(title: str) -> str:
    """已缓存的封面相对文件名。

    顺序：**带季键**（本季单独设过的图）→ 作品级键（S2 没单独设就用 S1 那张）
    → 原串哈希。带季优先是「两季各自一张封面」能生效的关键。
    """
    keys: list[str] = []
    for key in (season_key(title), cover_key(title), str(title or "").strip()):
        if key and key not in keys:
            keys.append(key)
    for key in keys:
        path = cache_path(key)
        try:
            if path.is_file() and path.stat().st_size > 512:
                return path.name
        except OSError:
            continue
    return ""


_IMAGE_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def validate_image_bytes(data: bytes) -> tuple[bool, str]:
    """校验图片字节：魔数 + 大小边界。绝不信任前端传来的内容。"""
    if not data or len(data) < 512:
        return False, "图片太小或为空"
    if len(data) > MAX_BYTES:
        return False, f"图片超过 {MAX_BYTES // 1024 // 1024} MB 上限"
    if any(magic == data[:len(magic)] for magic, _ in _IMAGE_MAGIC):
        return True, ""
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return True, ""
    if data[:2] == b"BM":
        return True, ""
    return False, "不是有效的图片文件（仅支持 JPG/PNG/WebP/GIF/BMP）"


def manual_marker(title: str) -> str:
    """人工封面的 subjects 标记键（带季，与缓存键同一套规则）。"""
    return _norm_key(season_key(str(title or "")))


def manual_filename(title: str) -> str:
    """按缓存文件名判定人工封面：哈希唯一，不受标题空格/大小写影响。"""
    return cache_path(season_key(str(title or ""))).name


def is_manual(title: str) -> bool:
    """该作品是否已由用户人工设定封面。

    用 cover_local 文件名而不是标题做匹配——标题可能含空格、季标记，
    ``lower(title_cn)`` 与 ``_norm_key()`` 会不一致导致保护失效。
    """
    name = manual_filename(title)
    try:
        row = db.row(
            "SELECT 1 FROM subjects WHERE source = 'manual' AND cover_local = ? LIMIT 1",
            (name,),
        )
        return bool(row and _cover_file_ok(name))
    except Exception:
        return False


def set_manual_cover(title: str, data: bytes, origin: str = "manual:datauri") -> dict:
    """把用户粘贴/选择的图片落盘，覆盖该作品的自动封面缓存。

    写在同一 ``cache_path(season_key(title))`` 上，``resolve_cover`` 的哈希直查
    天然优先命中；``subjects.source='manual'`` 让后台抓取永远不覆盖用户选择。

    键**带季**：第二季设封面不会动到第一季的那张（否则两季共用同一文件、
    后设的覆盖先设的）。``subjects`` 里存的是展示名，方便按名字精确匹配。
    """
    display = str(title or "").strip()
    key_title = season_key(display)
    if not key_title:
        return {"ok": False, "error": "作品名为空"}
    ok, why = validate_image_bytes(data or b"")
    if not ok:
        return {"ok": False, "error": why}
    try:
        config.ensure_dirs()
        dest = cache_path(key_title)
        dest.write_bytes(data)
        # 覆盖掉可能存在的旧自动记录，保证 subjects 里只剩 manual 这一条
        save_cover_record(display, str(origin)[:200], "manual", dest.name)
        return {"ok": True, "file": dest.name, "title": key_title,
                "size": len(data), "source": "manual"}
    except OSError as exc:
        return {"ok": False, "error": f"写入封面失败：{exc}"}


def clear_manual_cover(title: str) -> dict:
    """撤销人工封面，恢复自动抓取。

    只删本程序 ``COVER_DIR`` 里这一张由人工设定写入的缓存文件（可重抓），
    不动用户的任何视频/图片文件；下次抓封面会自动重新填充。
    """
    key = manual_marker(title)
    if not key:
        return {"ok": False, "error": "作品名为空"}
    name = manual_filename(title)
    try:
        db.execute("DELETE FROM subjects WHERE source = 'manual' AND cover_local = ?",
                   (name,))
        dest = config.COVER_DIR / name
        try:
            if dest.is_file():
                dest.unlink()
        except OSError:
            pass
        # 记一笔墓碑：否则随包公共封面会立刻把同名图补回来，「清除」形同无效
        _mark_cleared(manual_marker(title), True)
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def rename_cover_record(old_title: str, new_title: str) -> int:
    """作品改名后，把 subjects 里挂在旧标题上的封面记录改挂到新标题。

    只改展示名（``title_cn``），**封面文件与 ``cover_local`` 原样不动**：同一张图
    继续可用（``resolve_cover`` 会按新名在 ``exact`` 索引里命中同一条记录）。
    不迁移的话，「看过的列表」改名会把封面键一起改掉——卡片上那张图会凭空消失，
    用户会当成改名把封面弄丢了。返回更新的行数。
    """
    old = str(old_title or "").strip()
    new = str(new_title or "").strip()
    if not old or not new or _norm_key(old) == _norm_key(new):
        return 0
    key = _norm_key(old)
    # 先数一遍再更新：db.execute 返回的是 lastrowid/rowcount 混合值（UPDATE 后
    # lastrowid 仍是上一次插入的 id），不能拿来当"改了几行"。用 db.execute 而
    # 不是自开 session，是为了让它顺带 bump 写计数、让剧集墙缓存签名失效。
    n = db.scalar(
        "SELECT COUNT(*) FROM subjects WHERE lower(COALESCE(title_cn,'')) = ? "
        "OR lower(COALESCE(title_orig,'')) = ?", (key, key))
    if not n:
        return 0
    db.execute(
        "UPDATE subjects SET title_cn = ?, updated_at = ? "
        "WHERE lower(COALESCE(title_cn,'')) = ? OR lower(COALESCE(title_orig,'')) = ?",
        (new, db.now(), key, key),
    )
    return int(n)


def save_cover_record(title: str, url: str, source: str, filename: str) -> None:
    """写 subjects 表，供剧集卡片直接读；同一张图只保留一条记录。

    判重除了标题，还看 ``cover_local``：同一季的两种写法（``… S2`` /
    ``… · 第 2 季``）会落到同一个缓存文件，不该在 subjects 里长出两行。
    """
    # 有新图落盘（手选或重新抓到）就解除「清除」标记，否则下次会因墓碑拒用这张图
    _mark_cleared(manual_marker(title), False)
    key = _norm_key(title)
    existing = db.row("SELECT id FROM subjects WHERE cover_local = ? OR "
                      "lower(COALESCE(title_cn,'')) = ? OR "
                      "lower(COALESCE(title_orig,'')) = ? LIMIT 1",
                      (filename, key, key))
    now = db.now()
    if existing:
        db.execute("UPDATE subjects SET cover_url = ?, cover_local = ?, source = ?, updated_at = ? WHERE id = ?",
                   (url, filename, source, now, existing["id"]))
    else:
        db.execute(
            "INSERT INTO subjects(title_cn, cover_url, cover_local, source, confidence, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 0.5, ?, ?)",
            (str(title).strip(), url, filename, source, now, now),
        )


def fetch_one(title: str, force: bool = False) -> dict:
    """抓一个作品的封面：先查缓存 → 搜索 → 下载 → 入库。

    缓存键用 `season_key()`（**保留季**、去尾集号）：同一部番的不同季各存一张，
    互不覆盖；逐集分组的卡片仍共用同一张，因为它们的标题只差集号。
    搜索词用原始标题（`search_cover` 内部会自己去季重试）。
    """
    display = str(title or "").strip()
    key_title = season_key(display)
    dest = cache_path(key_title)
    # 人工设定的封面优先级最高：连 force 重抓也不能覆盖用户的选择，
    # 想恢复自动结果必须先调 clear_manual_cover()。
    if is_manual(display):
        return {"ok": True, "cached": True, "file": dest.name, "source": "manual"}
    if not force and dest.is_file() and dest.stat().st_size > 512:
        return {"ok": True, "cached": True, "file": dest.name, "source": "cache"}
    found = search_cover(display)
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error", "未找到"),
                "query": found.get("query", "")}
    used_url = ""
    for candidate in found.get("urls") or []:
        if download_cover(candidate, dest):
            used_url = candidate
            break
    if not used_url:
        return {"ok": False, "error": "封面下载失败", "query": found.get("query", "")}
    save_cover_record(display, used_url, found["source"], dest.name)
    return {"ok": True, "cached": False, "file": dest.name,
            "source": found["source"], "hit": found.get("hit"),
            "query": found.get("query", "")}


_TECH_TAG_RE = re.compile(
    r"(?:1080p|720p|2160p|4k|bdrip|webrip|web-dl|h264|h265|hevc|x264|x265|10bit|flac|aac|"
    r"mkv|mp4|gb|big5|chs|cht|raws?|zero tv|zerotv|nf|hd|bd|tv|sp|ova|oad|"
    r"www\.|\[|\]|\{|\}|第\s*\d+\s*集)", re.IGNORECASE
)
# 番号式编号（FC2PPV-3094718 / SSIS-12345 之类）不可能是作品名，抓了也必定 404
_JUNK_ID_RE = re.compile(r"^(?:[A-Za-z0-9]{1,12}[-_]\d{4,8}|\d{6,10})$", re.IGNORECASE)
_HAS_CN_RE = re.compile(r"[\u4e00-\u9fff]")
# 真实清单里会有「Q - 完」「剧场版 （完）」「S01 （完）」这类只有标记、没有片名的行
_MARKER_RE = re.compile(
    r"(?:S\s*\d{1,2}|第[一二三四五六七八九十\d]{1,3}\s*[季期]|剧场版|[（(]\s*完\s*[)）]|"
    r"\b完\b|OVA|OAD|\bSP\b|\b完结\b)", re.IGNORECASE,
)
_NOISE_RE = re.compile(r"[\s\-_·:：,，.。!！?？\[\]{}()（）〈〉《》\"'“”‘’\u200b-\u200f\ufeff]+")
_CN_CHAR_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RUN_RE = re.compile(r"[A-Za-z]{4,}")


def clean_candidate(title: str) -> str:
    """只保留像「作品名」的候选：去掉字幕组技术标签后仍要有可读中文/英文标题。"""
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", str(title or "")).strip()
    if not text or len(text) > 32 or len(text) < 2:
        return ""
    if _TECH_TAG_RE.search(text) or _JUNK_ID_RE.match(text):
        return ""
    # 纯拉丁字母且带大量连字符/数字的长串，多半是发布组文件名
    if re.fullmatch(r"[A-Za-z0-9\s\-_.:'&!]+", text) and len(text) > 22:
        return ""
    # 去掉「第二季 / （完）/ 剧场版」等标记与标点后，必须还剩至少 2 个中文字
    # 或一段 ≥4 字母的英文词，否则就是清单里的残缺行（如「- 完」「Q - 完」）
    bare = _NOISE_RE.sub("", _MARKER_RE.sub("", text))
    if len(_CN_CHAR_RE.findall(bare)) < 2 and not _LATIN_RUN_RE.search(bare):
        return ""
    return text


def poster_labels() -> list[str]:
    """剧集墙卡片标题（与 `Api.get_poster_wall()` 同一套 series 分组逻辑）。

    封面按「抓取标题」哈希落盘，只有直接拿卡片标题去抓，缓存才能被
    `cover_map()` 直接命中；用导入清单标题抓到的图很可能展示不出来。

    标题**保留季标记**（``season_key``）：否则第二季永远抓不到自己那张，
    只会复用第一季的图。
    """
    try:
        from . import library_tree
        rows = db.rows(
            "SELECT id, dir_id, path, filename, title_guess, title_cn, "
            "season_no, episode_no FROM media_files"
        )
        tree = library_tree.build_tree(rows, "series", None)
        seen, labels = set(), []
        for group in tree.get("groups", []):
            key = season_key(str(group.get("label") or ""))
            normalized = _norm_key(key)
            if key and normalized and normalized not in seen:
                seen.add(normalized)
                labels.append(key)
        return labels
    except Exception:
        return []


def cover_titles(limit: int = 0) -> list[str]:
    """待抓封面的作品名。

    排序直接决定「抓到的图能不能显示」：优先级为
    **剧集墙卡片标题 → 导入清单中文名 → 媒体库中文名 → 其余英文标题**，
    每档里中文名排前、文件数多的排前。实测 AGE动漫 / SeedHub 只对中文作品名稳定命中，
    字幕组英文文件名、番号编号与「（完）」残行先被 `clean_candidate()` 过滤，
    免得把一次性任务的配额浪费在必定失败的请求上。
    """
    scored: list[tuple[int, int, int, str, str]] = []
    # (来源档位, 是否非中文, 负的文件数, 排序用标题, 待清洗标题)
    for label in poster_labels():
        scored.append((0, 0 if _HAS_CN_RE.search(label) else 1, 0, label, label))
    for r in db.rows("SELECT COALESCE(NULLIF(TRIM(title_cn),''), NULLIF(TRIM(title),'')) AS title "
                    "FROM watched_list"):
        raw = str(r["title"] or "")
        scored.append((1, 0 if _HAS_CN_RE.search(raw) else 1, 0, raw, raw))
    for r in db.rows(
        "SELECT COALESCE(NULLIF(TRIM(title_cn),''), NULLIF(TRIM(title_guess),'')) AS title, "
        "COUNT(*) AS n FROM media_files "
        "WHERE COALESCE(title_cn,'') <> '' OR COALESCE(title_guess,'') <> '' "
        "GROUP BY title"
    ):
        raw = str(r["title"] or "")
        scored.append((2, 0 if _HAS_CN_RE.search(raw) else 1, -int(r["n"] or 0), raw, raw))

    seen, out = set(), []
    for _tier, _not_cn, _negn, _sort, raw in sorted(scored, key=lambda item: item[:4]):
        title = clean_candidate(raw)
        key = _norm_key(title)
        if not title or key in seen:
            continue
        # 用户人工设定过的作品不排进抓取队列，避免一次任务把它们全刷掉
        if is_manual(title):
            continue
        seen.add(key)
        out.append(title)
        if limit and len(out) >= limit:
            break
    return out


def status() -> dict:
    with _job_lock:
        snap = dict(_JOB)
        snap["recent_failures"] = [dict(x) for x in _JOB["recent_failures"]]
    snap["cached"] = sum(1 for p in config.COVER_DIR.glob("*.jpg")) if config.COVER_DIR.is_dir() else 0
    return snap


def start_job(limit: int = 0, force: bool = False) -> dict:
    """启动**一次性**后台抓取任务（非定时）；重复调用不会起第二个线程。"""
    with _job_lock:
        if _JOB["running"]:
            return {"ok": False, "error": "已有抓取任务在运行", **status()}
        clear_seed_cache()          # 每次用户点「抓取」刷新一次备源索引
        titles = cover_titles(limit)
        _JOB.update(running=True, total=len(titles), done=0, ok=0, failed=0, current="",
                    last_query="", last_hit="", recent_failures=[],
                    source_hits={}, started_at=db.now(), finished_at="", error="", cancel=False)

    def work():
        for title in titles:
            with _job_lock:
                if _JOB["cancel"]:
                    break
                _JOB["current"] = title
            try:
                result = fetch_one(title, force=force)
            except Exception as exc:  # 单个作品失败不能拖垮整批
                result = {"ok": False, "error": f"{type(exc).__name__}"}
            with _job_lock:
                _JOB["done"] += 1
                _JOB["last_query"] = str(result.get("query") or "")
                _JOB["last_hit"] = str(result.get("hit") or "")
                if result.get("ok"):
                    _JOB["ok"] += 1
                    if not result.get("cached"):
                        src = result.get("source") or "unknown"
                        _JOB["source_hits"][src] = _JOB["source_hits"].get(src, 0) + 1
                else:
                    _JOB["failed"] += 1
                    failures = _JOB["recent_failures"]
                    failures.append({"title": title, "error": str(result.get("error") or "未找到"),
                                     "query": _JOB["last_query"]})
                    del failures[:-8]      # 只留最近 8 条，别让状态无限膨胀
            time.sleep(0.08)   # 轻微限速，别把站点打疼
        with _job_lock:
            _JOB["running"] = False
            _JOB["finished_at"] = db.now()
            _JOB["current"] = ""

    threading.Thread(target=work, name="mv-cover-fetch", daemon=True).start()
    return {"ok": True, "started": True, "total": len(titles)}


def cancel_job() -> dict:
    with _job_lock:
        _JOB["cancel"] = True
    return {"ok": True}


def subject_covers() -> list[tuple[str, str]]:
    """subjects 里已经落库的 (标题, 本地封面文件名)，供卡片标题回退匹配。"""
    try:
        return [
            (str(r["title"] or ""), str(r["cover_local"] or ""))
            for r in db.rows(
                "SELECT COALESCE(NULLIF(TRIM(title_cn),''), NULLIF(TRIM(title_orig),'')) AS title, "
                "cover_local FROM subjects WHERE COALESCE(cover_local,'') <> '' "
                "ORDER BY (source = 'manual') DESC, id DESC"
            )
        ]
    except Exception:
        return []


_BUNDLED_CACHE: dict = {"stamp": None, "entries": [], "files": {}}


def bundled_covers() -> list[tuple[str, str]]:
    """仓库资产 ``assets/covers/index.json`` 里的 (标题/别名, 文件名)。

    这批封面随安装包分发（不是用户抓的），所以：

    - 新装、换机、没联网都直接有封面，不必等一轮抓取；
    - 其他人改了某部剧的封面提 PR，合并后重新打包就生效。

    文件本身不复制到数据目录，只在真要显示时才由 :func:`_materialize_cover`
    补一份到 ``COVER_DIR``（避免每次启动白拷 4 MB）。索引坏了就当没有，
    绝不影响抓取与本地缓存。
    """
    index = config.BUNDLED_COVER_DIR / "index.json"
    try:
        stat = index.stat()
        stamp = (int(stat.st_mtime_ns), int(stat.st_size))
    except OSError:
        _BUNDLED_CACHE.update(stamp=None, entries=[], files={})
        return []
    if _BUNDLED_CACHE["stamp"] == stamp:
        return list(_BUNDLED_CACHE["entries"])
    entries: list[tuple[str, str]] = []
    files: dict[str, set[str]] = {}
    try:
        data = json.loads(index.read_text(encoding="utf-8"))
        for item in data.get("covers") or []:
            name = str(item.get("file") or "").strip()
            if not name:
                continue
            titles = [str(item.get("title") or "").strip()]
            titles += [str(a or "").strip() for a in (item.get("aliases") or [])]
            for title in titles:
                if title:
                    entries.append((title, name))
                    # 文件名 → 作品键：用于判断这张图是否被用户「清除」过
                    files.setdefault(name, set()).add(_norm_key(cover_key(title)))
    except Exception:
        entries, files = [], {}
    _BUNDLED_CACHE.update(stamp=stamp, entries=entries, files=files)
    return list(entries)


def all_cover_entries() -> list[tuple[str, str]]:
    """本机已落库封面 + 随包资产；前者优先（用户抓到的比公共封面新）。

    被用户「清除封面」过的作品，其资产条目在这里就被剔掉——否则本机已经有
    同名图时（之前补过一次），仅靠 ``_materialize_cover`` 拦不住，清除等于无效。
    """
    entries = subject_covers()
    cleared = _cleared_keys()
    for title, name in bundled_covers():
        if cleared and cleared & _BUNDLED_CACHE["files"].get(name, set()):
            continue
        entries.append((title, name))
    return entries


_CLEARED_KEY = "cover_cleared"


def _cleared_keys() -> set[str]:
    """用户显式「清除封面」过的作品键（``_norm_key(cover_key(title))``）。

    公共封面资产会把同名图补回本机；不记这一笔的话「清除封面」永远清不干净。
    按**作品键**记而不是按文件名：一个作品可能对应资产里的不同文件名。
    只存几十个字符串，塞进 settings 的 JSON 即可，不另建表。
    """
    try:
        raw = db.get_setting(_CLEARED_KEY, "") or ""
    except Exception:
        return set()
    if not raw:
        return set()
    try:
        data = json.loads(raw)
    except Exception:
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def _mark_cleared(key: str, cleared: bool) -> None:
    if not key:
        return
    keys = _cleared_keys()
    if cleared:
        keys.add(key)
    else:
        keys.discard(key)
    try:
        db.set_setting(_CLEARED_KEY, json.dumps(sorted(keys), ensure_ascii=False))
    except Exception:
        pass


def _materialize_cover(filename: str) -> bool:
    """资产里有、本机缓存没有时，复制一份到 ``COVER_DIR``。

    只在真正要读这张图时调用。安装目录可能只读，所以方向永远是
    资产 → 数据目录，绝不反向写。
    """
    if not filename:
        return False
    cleared = _cleared_keys()
    if cleared:
        if not _BUNDLED_CACHE["files"]:
            bundled_covers()          # 冷启动时先读一次索引，才知道这张图属于哪个作品
        if cleared & _BUNDLED_CACHE["files"].get(filename, set()):
            return False              # 用户点过「清除封面」，不要再从公共资产补回来
    try:
        dest = config.COVER_DIR / filename
        if dest.is_file() and dest.stat().st_size > 512:
            return True
        src = config.BUNDLED_COVER_DIR / filename
        if not src.is_file():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(src.read_bytes())
        tmp.replace(dest)
        return True
    except OSError:
        return False


def _cover_file_ok(filename: str) -> bool:
    if not filename:
        return False
    path = config.COVER_DIR / filename
    try:
        if path.is_file() and path.stat().st_size > 512:
            return True
    except OSError:
        pass
    # 本机缓存没有：从随包资产补一份（离线、一次性）
    return _materialize_cover(filename)


def cover_index(entries: list[tuple[str, str]] | None) -> tuple[dict, dict]:
    """把 subjects 列表编译成两张查找表：精确键、去季键。

    旧实现每解析一张封面都要重新 ``_norm_key()`` 全部 subjects（80 条 × 195 张卡），
    再逐个 ``stat()`` 校验文件，实测光这一步就 220ms。表只建一次即可。
    """
    exact: dict[str, str] = {}
    base: dict[str, str] = {}
    for rec_title, filename in entries or []:
        if not filename:
            continue
        key = _norm_key(rec_title)
        if key:
            exact.setdefault(key, filename)
        season_norm = _norm_key(season_key(rec_title))
        if season_norm and season_norm != key:
            exact.setdefault(season_norm, filename)
        # 去季键用于「没单独设过封面的卡片」回落到该作品的图（有图总比白板好）。
        # 两季各自设过时不会走到这里：`local_cover()` 先用带季键命中自己那张。
        base_key = _norm_key(_strip_season(rec_title))
        if base_key:
            base.setdefault(base_key, filename)
    return exact, base


def resolve_cover(title: str, entries: list[tuple[str, str]] | None = None) -> str:
    """把「展示标题」解析成本地封面文件名。

    抓取时的标题（导入清单 / media_files）与剧集墙卡片标题（树分组 label）可能带了
    不同的季标记或后缀，只按哈希查会漏。顺序：
    原哈希 → 去季标记哈希 → subjects 精确匹配 → subjects 去季精确 → subjects 包含匹配。
    条目默认是「本机已落库 + 随包资产」（见 :func:`all_cover_entries`）。
    """
    entries = all_cover_entries() if entries is None else entries
    return resolve_cover_many([("", [title])], entries).get("", "")


def resolve_cover_many(pairs: list[tuple[str, list[str]]],
                       entries: list[tuple[str, str]] | None = None) -> dict[str, str]:
    """批量解析封面：``[(结果键, [候选标题…])]`` → ``{结果键: 封面文件名}``。

    剧集墙一次要解析近 200 张卡，逐张调用会把 subjects 索引重复构建 200 次；
    这里索引与「文件存在」检查都只做一次。
    """
    entries = all_cover_entries() if entries is None else entries
    exact, base = cover_index(entries)
    exists: dict[str, bool] = {}

    def ok(name: str) -> bool:
        if name not in exists:
            exists[name] = _cover_file_ok(name)
        return exists[name]

    out: dict[str, str] = {}
    fuzzy_misses: list[tuple[str, list[str]]] = []
    for key, candidates in pairs or []:
        texts = [str(t or "").strip() for t in candidates or []]
        texts = [t for t in texts if t]
        hit = ""
        for text in texts:
            name = local_cover(text)
            if name and ok(name):
                hit = name
                break
            base_text = _strip_season(text)
            if base_text and base_text != text:
                name = local_cover(base_text)
                if name and ok(name):
                    hit = name
                    break
            norm = _norm_key(text)
            if norm and ok(exact.get(norm, "")):
                hit = exact[norm]
                break
            season_norm = _norm_key(season_key(text))
            if season_norm and ok(exact.get(season_norm, "")):
                hit = exact[season_norm]
                break
            norm_base = _norm_key(base_text)
            if norm_base and ok(base.get(norm_base, "")):
                hit = base[norm_base]
                break
        if hit:
            out[key] = hit
        elif texts:
            fuzzy_misses.append((key, texts))
    if not fuzzy_misses:
        return out
    # 只有完全没有精确命中时才走模糊兜底，避免把别的作品封面套上去。
    # 把 subjects 的归一化键预先算好：旧实现每个候选都重新归一化 80 条记录，
    # 150 张对不上的卡要多花 3 秒（用户反馈的「剧集墙打开很慢」）。
    fuzzy_entries = []
    for rec_title, filename in entries:
        if not filename or not ok(filename):
            continue
        # 带季的封面只按精确/哈希命中：否则「第二季」那张会通过模糊匹配
        # 糊到第一季或别的写法上，用户分开设的两张又变成一样。
        if _SEASON_TAIL_RE.search(str(rec_title or "")):
            continue
        norm = _norm_key(rec_title)
        if norm:
            # 记录 subject 侧是否短标题：短标题既不能被别人的子串命中，
            # 也不能用 norm in a 去糊到别的长作品上（对称保护）。
            fuzzy_entries.append((norm, _norm_key(_strip_season(rec_title)),
                                  filename, len(_norm_key(_strip_season(rec_title))) <= 2))
    for key, texts in fuzzy_misses:
        for text in texts:
            a = _norm_key(text)
            if not a:
                continue
            a_base = _norm_key(_strip_season(a))
            a_short = _short_title(text)
            # 裸子串包含只在「双方都不是短标题」时允许：否则 K 会命中
            # 「花宵道中…Flowered Skin…」的 k、「破」命中「天元突破」、
            # 「终」命中「终结的炽天使」（用户 2026-11-19 反馈）。
            # 精确相等 (a==norm) 与去季 base 相等始终允许，不受影响。
            match = ""
            for norm, norm_base, fn, norm_short in fuzzy_entries:
                if a == norm or (a_base and a_base == norm_base):
                    match = fn
                    break
                if not a_short and not norm_short and (a in norm or norm in a):
                    match = fn
                    break
            if match:
                out[key] = match
                break
    return out


def cover_map(titles: list[str]) -> dict:
    """给前端剧集卡片用：{展示标题: 本地封面文件名}，无封面则不放进字典。"""
    entries = subject_covers()
    out = {}
    for title in titles or []:
        name = resolve_cover(title, entries)
        if name:
            out[str(title)] = name
    return out
