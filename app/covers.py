"""动漫封面：后台异步抓取、本地缓存、入库。

设计约束（与中文名匹配一致）：
- **不是定时任务**。只能由用户在界面点「抓取封面」或 CLI 主动触发一次后台任务。
- 全程在 daemon 线程里跑，绝不阻塞导入 / 扫描 / 播放 / UI。
- 短超时、整批失败快速降级；任何网络异常都只记状态，不抛给调用方。
- 图片只写进本程序自己的 ``COVER_DIR``，绝不修改用户视频目录。

数据源（2026-11-15 实测）：
- 主源 AGE动漫 ``https://www.agedm.io/search?query=<中文名>``：结果块
  ``<div class="card cata_video_item">`` 里 ``<a title="...">`` 为标题，
  ``img.video_thumbs`` 的 ``data-original`` 是高清封面、``src`` 是缩略图。
- 备源 SeedHub(seeduck.cc)：搜索被 Cloudflare 拦，但首页 / 动漫分类页可达，
  ``<a title="作品名" class="image"><img src="...webp">`` 可按标题配对。
- 本机走系统 HTTPS 代理且为 MITM 证书，Python 必须 ``verify=False`` 才能连通
  （curl 用系统 CA 所以能通）；这是本机环境事实，不是放松校验的通用理由。
"""
from __future__ import annotations

import hashlib
import html
import re
import threading
import time
import urllib.parse
from pathlib import Path

from . import config, db

AGEDM_SEARCH = "https://www.agedm.io/search"
SEEDUCK_PAGES = (
    "https://seeduck.cc/",
    "https://seeduck.cc/categories/2/movies/",
)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
TIMEOUT = (2.5, 5.0)          # connect, read
IMAGE_TIMEOUT = (2.5, 6.0)
MAX_BYTES = 3 * 1024 * 1024   # 单张封面上限，防止异常响应撑爆磁盘

_BLOCK_RE = re.compile(r'<div class="card cata_video_item(.*?)(?=<div class="card cata_video_item|</main>)',
                       re.S)
_TITLE_RE = re.compile(r'<a href="[^"]+"[^>]*title="([^"]+)"', re.S)
_ORIG_RE = re.compile(r'data-original="([^"]+)"')
_SRC_RE = re.compile(r'class="video_thumbs[^"]*"\s+src="([^"]+)"')
_SEED_RE = re.compile(
    r'<a[^>]+title="([^"]+)"[^>]*class="image"[^>]*>\s*<img[^>]+src="([^"]+)"',
    re.S,
)

_JOB = {
    "running": False,
    "total": 0,
    "done": 0,
    "ok": 0,
    "failed": 0,
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
    """把展示标题归一成「作品名」：去掉结尾的季标记与集号，作为搜索词 / 缓存键。"""
    original = str(title or "").strip()
    text = _strip_season(original)
    previous = None
    while text and previous != text:
        previous = text
        text = _EP_SUFFIX_RE.sub("", text).strip(" -_·:：")
    return text or original


def _get(url: str, *, timeout=TIMEOUT, stream: bool = False):
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    return requests.get(
        url, headers={"User-Agent": UA, "Referer": "https://www.agedm.io/"},
        timeout=timeout, verify=False, stream=stream,
    )


def _strip_season(title: str) -> str:
    """「XX 第二季」搜不到时用主标题再试一次。"""
    return re.sub(r"\s*(第[一二三四五六七八九十\d]{1,3}\s*[季期]|[Ss]\d{1,2})\s*$", "", str(title or "")).strip()


def _fuzzy_match(candidate: str, want: str) -> bool:
    a, b = _norm_key(candidate), _norm_key(want)
    if not a or not b:
        return False
    return a == b or b in a or a in b or _norm_key(_strip_season(a)) == _norm_key(_strip_season(b))


def parse_agedm(body: str, want: str) -> tuple[list[str], str] | None:
    """从 AGE动漫 搜索结果里挑出与 want 最匹配的 (候选封面URL列表, 命中标题)。

    两轮：先找**标题完全一致**的结果（避免「葬送的芙莉莲」误拿到「第二季」封面），
    没有完全一致时才放宽到包含匹配。
    """
    blocks = []
    for block in _BLOCK_RE.split(body)[1::2]:
        titles = _TITLE_RE.findall(block)
        if not titles:
            continue
        name = html.unescape(titles[0]).strip()
        urls = [html.unescape(u) for u in (_ORIG_RE.findall(block) + _SRC_RE.findall(block))]
        urls = list(dict.fromkeys(u for u in urls if u.startswith("http")))
        if urls:
            blocks.append((name, urls))

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
    """从 SeedHub 首页/分类页里按 title 属性配对封面。"""
    for name, url in _SEED_RE.findall(body):
        clean = html.unescape(name).strip()
        if _fuzzy_match(clean, want) and url.startswith("http"):
            return [html.unescape(url)], clean
    return None


def search_cover(title: str) -> dict:
    """同步查一次封面（供后台线程调用）；返回 {ok,urls,source,hit}。"""
    want = cover_key(title)
    if not want:
        return {"ok": False, "error": "空标题"}

    # 主源：AGE动漫搜索
    try:
        url = AGEDM_SEARCH + "?" + urllib.parse.urlencode({"query": want})
        resp = _get(url)
        if resp.status_code == 200:
            hit = parse_agedm(resp.text, want)
            if hit:
                return {"ok": True, "urls": hit[0], "source": "agedm", "hit": hit[1]}
    except Exception:
        pass

    # 备源：SeedHub 首页 / 动漫分类页
    for page in SEEDUCK_PAGES:
        try:
            resp = _get(page)
            if resp.status_code == 200:
                hit = parse_seeduck(resp.text, want)
                if hit:
                    return {"ok": True, "urls": hit[0], "source": "seeduck", "hit": hit[1]}
        except Exception:
            continue
    return {"ok": False, "error": "两个源都没有匹配到封面"}


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
    """已缓存的封面相对文件名；先按归一化的作品名找，再兼容旧的原标题哈希。"""
    for key in (cover_key(title), str(title or "").strip()):
        if not key:
            continue
        path = cache_path(key)
        try:
            if path.is_file() and path.stat().st_size > 512:
                return path.name
        except OSError:
            continue
    return ""


def save_cover_record(title: str, url: str, source: str, filename: str) -> None:
    """写 subjects 表，供剧集卡片直接读；同标题只保留最新一条。"""
    key = _norm_key(title)
    existing = db.row("SELECT id FROM subjects WHERE lower(COALESCE(title_cn,'')) = ? OR "
                      "lower(COALESCE(title_orig,'')) = ? LIMIT 1", (key, key))
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

    统一用 `cover_key()`（去季 + 去尾集号）做缓存键与入库标题，
    剧集墙里逐集分组的卡片因此能共用同一张封面。
    """
    key_title = cover_key(title)
    dest = cache_path(key_title)
    if not force and dest.is_file() and dest.stat().st_size > 512:
        return {"ok": True, "cached": True, "file": dest.name, "source": "cache"}
    found = search_cover(title)
    if not found.get("ok"):
        return {"ok": False, "error": found.get("error", "未找到")}
    used_url = ""
    for candidate in found.get("urls") or []:
        if download_cover(candidate, dest):
            used_url = candidate
            break
    if not used_url:
        return {"ok": False, "error": "封面下载失败"}
    save_cover_record(key_title, used_url, found["source"], dest.name)
    return {"ok": True, "cached": False, "file": dest.name,
            "source": found["source"], "hit": found.get("hit")}


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
            key = cover_key(str(group.get("label") or ""))
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
        seen.add(key)
        out.append(title)
        if limit and len(out) >= limit:
            break
    return out


def status() -> dict:
    with _job_lock:
        snap = dict(_JOB)
    snap["cached"] = sum(1 for p in config.COVER_DIR.glob("*.jpg")) if config.COVER_DIR.is_dir() else 0
    return snap


def start_job(limit: int = 0, force: bool = False) -> dict:
    """启动**一次性**后台抓取任务（非定时）；重复调用不会起第二个线程。"""
    with _job_lock:
        if _JOB["running"]:
            return {"ok": False, "error": "已有抓取任务在运行", **status()}
        titles = cover_titles(limit)
        _JOB.update(running=True, total=len(titles), done=0, ok=0, failed=0,
                    source_hits={}, started_at=db.now(), finished_at="", error="", cancel=False)

    def work():
        for title in titles:
            with _job_lock:
                if _JOB["cancel"]:
                    break
            try:
                result = fetch_one(title, force=force)
            except Exception as exc:  # 单个作品失败不能拖垮整批
                result = {"ok": False, "error": f"{type(exc).__name__}"}
            with _job_lock:
                _JOB["done"] += 1
                if result.get("ok"):
                    _JOB["ok"] += 1
                    if not result.get("cached"):
                        src = result.get("source") or "unknown"
                        _JOB["source_hits"][src] = _JOB["source_hits"].get(src, 0) + 1
                else:
                    _JOB["failed"] += 1
            time.sleep(0.08)   # 轻微限速，别把站点打疼
        with _job_lock:
            _JOB["running"] = False
            _JOB["finished_at"] = db.now()

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
                "cover_local FROM subjects WHERE COALESCE(cover_local,'') <> ''"
            )
        ]
    except Exception:
        return []


def _cover_file_ok(filename: str) -> bool:
    if not filename:
        return False
    path = config.COVER_DIR / filename
    try:
        return path.is_file() and path.stat().st_size > 512
    except OSError:
        return False


def resolve_cover(title: str, entries: list[tuple[str, str]] | None = None) -> str:
    """把「展示标题」解析成本地封面文件名。

    抓取时的标题（导入清单 / media_files）与剧集墙卡片标题（树分组 label）可能带了
    不同的季标记或后缀，只按哈希查会漏。顺序：
    原哈希 → 去季标记哈希 → subjects 精确匹配 → subjects 去季精确 → subjects 包含匹配。
    """
    text = str(title or "").strip()
    if not text:
        return ""
    name = local_cover(text)
    if not name:
        base = _strip_season(text)
        if base and base != text:
            name = local_cover(base)
    if name:
        return name

    entries = subject_covers() if entries is None else entries
    key, base_key = _norm_key(text), _norm_key(_strip_season(text))
    for rec_title, filename in entries:
        if _norm_key(rec_title) == key or _norm_key(_strip_season(rec_title)) == base_key:
            if _cover_file_ok(filename):
                return filename
    for rec_title, filename in entries:
        if _fuzzy_match(rec_title, text) and _cover_file_ok(filename):
            return filename
    return ""


def cover_map(titles: list[str]) -> dict:
    """给前端剧集卡片用：{展示标题: 本地封面文件名}，无封面则不放进字典。"""
    entries = subject_covers()
    out = {}
    for title in titles or []:
        name = resolve_cover(title, entries)
        if name:
            out[str(title)] = name
    return out
