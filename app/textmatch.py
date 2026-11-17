"""从作品标题生成多组搜索关键词，并做宽容的标题相似度打分。

两个调用方共用，避免各写一套：
- ``app/covers.py``：站点搜索**不要用完整长标题**（文件名常带季号、集号、
  括号说明、演员表，全名几乎搜不到），改用核心关键词轮番尝试。
- ``app/watchlist.py``：追剧列表里的写法与本地视频文件名差异很大，
  用短关键词去 ``media_files`` 里找「已下载」的对应剧集。

纯字符串处理，不联网、不查库；所有函数对脏输入必须返回可用结果而不是抛错。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

_ZW_RE = re.compile(r"[\u200b-\u200f\ufeff\xa0]")
# 行首的清单编号 / 项目符号 / 「番名：」「电影名：」这类引导词
_LEAD_RE = re.compile(
    r"^\s*(?:\d+\s*[\.、)]\s+|[-–—•·*]+\s+|"
    r"(?:番名|电影名|漫名|片名|剧名|作品名)\s*[:：]\s*)"
)
_BOOK_RE = re.compile(r"[《<]([^》>]{2,40})[》>]")
_PAREN_RE = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]")
_SEASON_RE = re.compile(
    r"(?:\s*(?:第[一二三四五六七八九十\d]{1,3}\s*[季期]|"
    r"[Ss]\s*\d{1,2}(?:\s*[-~～至]\s*\d{1,2}|\s*[Pp]art\s*\d{1,2})?|"
    r"[Ss]eason\s*\d{1,2}|[Pp]art\s*\d{1,2}|"
    r"\d{1,2}(?:st|nd|rd|th)\s+[Ss]eason|"
    r"第\s*\d{1,3}\s*[集话話]))\s*$", re.IGNORECASE,
)
_EP_TAIL_RE = re.compile(
    r"(?:\s*\[\s*\d{1,3}\s*\]|\s*第\s*\d{1,3}\s*[集话話]|"
    r"\s*EP?\s*\d{1,3}|\s*[-_]?\s*(?<!\d)\d{1,3})\s*$", re.IGNORECASE,
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{3,}")
_NOISE_RE = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
_LATIN_DIGIT_RE = re.compile(r"[a-z0-9]")
_TRAIL_RE = re.compile(r"[\s\-_·:：,，.。!！?？、~～#]+$")
_LEAD_PUNCT_RE = re.compile(r"^[\s\-_·:：,，.。!！?？、~～#]+")


def normalize(text: str) -> str:
    """比较用归一化：去零宽、去标点空格、小写。

    必须**先小写再去标点**：``_NOISE_RE`` 只保留 a-z，先过滤会把大写英文
    标题整段抹掉（曾导致 ``A`` 归一化成空串、清单条目被误判为重复）。
    """
    return _NOISE_RE.sub("", _ZW_RE.sub("", str(text or "")).lower())


def clean_title(text: str) -> str:
    """把清单里的一行清成可展示的标题；不改动文字主体。"""
    s = _ZW_RE.sub("", str(text or "")).replace("\u3000", " ").strip()
    s = _LEAD_RE.sub("", s, count=1)
    s = _LEAD_PUNCT_RE.sub("", s)
    s = _TRAIL_RE.sub("", s)
    return s.strip()


# 发布组 / 压制参数的特征词。命中的字符串是「从文件名抠出来的技术串」，
# 不是作品名，不能当中文名存库、更不能进学习词典。
_RELEASE_RE = re.compile(
    r"(?:\b(?:2160p|1080p|720p|480p|360p)\b"
    r"|\b(?:WEB[-\s.]?(?:DL|Rip)|Blu[-\s.]?Ray|BDRip|HDRip|DVDRip|HDTV)\b"
    r"|\b(?:HEVC|AVC|x26[45]|H\.?26[45]|10bit|8bit|Hi10P)\b"
    r"|\b(?:AAC|FLAC|AC3|EAC3|DTS|OPUS|MP3)\d?(?:[.\s]?\d)?\b"
    r"|\b(?:Ma10p|Chs|Cht|Jpn|Eng)\b"
    r"|\[[^\[\]]{2,40}\]"
    r"|\b[A-Z][A-Za-z0-9]*TV\b|\bKKTV\b|\bCR-WebRip\b)", re.IGNORECASE,
)


def looks_like_release(text: str) -> bool:
    """``text`` 是不是「发布组 / 压制参数」串，而不是真正的作品名。

    典型样本：``My DressUp Darling KKTV AAC2 0 -BlackTV``。这类串是扫描时从
    文件名抠出来的 ``title_guess``，被「改名」流程误当成中文名回写后，会顺着
    学习词典反向污染其它文件。判定口径刻意保守：只要出现分辨率、编码、音轨、
    方括号组名等任一项就算——真正的作品名几乎不会带这些。
    """
    s = str(text or "")
    if not s:
        return False
    if _RELEASE_RE.search(s):
        return True
    # 「名 空格 名 空格 名」且全是英文数字、还带数字：更像文件名拆出来的残片
    words = [w for w in re.split(r"[\s._-]+", s) if w]
    digits = sum(1 for w in words if w.isdigit())
    return len(words) >= 5 and digits >= 1 and not _CJK_RE.search(s)


def _strip_tail(text: str) -> str:
    """先反复剥季 / 部标记，再剥尾部集号；顺序反了会把「s4」吃掉一个 4。"""
    previous = None
    while text and previous != text:
        previous = text
        text = _SEASON_RE.sub("", text).strip()
    previous = None
    while text and previous != text:
        previous = text
        text = _EP_TAIL_RE.sub("", text).strip(" -_·:：")
    return text.strip()


def _usable(text: str) -> bool:
    """太短或全是噪音的词不能拿去搜，否则会匹配到一大堆无关结果。"""
    if not text or len(text) > 34:
        return False
    cn = len(_CJK_RE.findall(text))
    if cn >= 2:
        return True
    return bool(_LATIN_RUN_RE.search(text))


def query_variants(title: str, limit: int = 6) -> list[str]:
    """按「最可能是站点/文件名里出现的写法」顺序返回去重后的关键词。

    顺序刻意**不把完整长标题放第一位**：用户明确要求「不要全名，用文件名称
    中的关键字多试一试」。优先书名号内容、主标题、去季标记、CJK 核心片段，
    完整标题只作为最后兜底。
    """
    base = clean_title(title)
    if not base:
        return []
    candidates: list[str] = []

    # 1) 《…》里的片名优先级最高，能救「电影名《分裂》主演：…」这类行
    for book in _BOOK_RE.findall(base):
        candidates.append(_strip_tail(clean_title(book)))

    # 2) 标签式混排（「Inside...#VinlandSAGA#冰海战记」）拆成独立关键词，中文优先
    if "#" in base or "..." in base or "｜" in base:
        segs = [clean_title(x) for x in re.split(r"[#｜|]+|\.\.\.", base)]
        for seg in sorted((s for s in segs if s and s != base),
                          key=lambda s: 0 if _CJK_RE.search(s) else 1):
            candidates.append(seg)

    # 3) 去掉括号说明、季/集标记后的主标题；冒号前通常是作品名，冒号后是观感说明
    plain = _strip_tail(_PAREN_RE.sub("", base).strip(" -_·:：,，"))
    candidates.append(plain)
    if "：" in plain or ":" in plain or "·" in plain:
        head = re.split(r"[：:]", plain, maxsplit=1)[0]
        candidates.append(_strip_tail(head.strip()))
        # 全角冒号站点搜不到：「Re：Zero」再补一个英文空格写法
        spaced = re.sub(r"[：:·・]+", " ", plain).strip()
        if spaced and spaced != plain:
            candidates.append(spaced)
    # 3) 逗号前的第一段（「链-两个女人的诱惑，女主丁香」）
    if "，" in plain or "," in plain:
        head = re.split(r"[，,]", plain, maxsplit=1)[0]
        candidates.append(_strip_tail(head.strip()))

    # 4) 最长中文片段；只有很长的说明性标题才补前缀短词（正常番名不能截断）
    runs = _CJK_RUN_RE.findall(plain)
    if runs:
        longest = max(runs, key=len)
        candidates.append(longest)
        if len(longest) >= 12 and len(plain) > 16:
            candidates.append(longest[:8])
    # 5) 英文关键词（无中文或中英混排时的主力）；Season/part 这类通用词不算
    for word in _LATIN_RUN_RE.findall(base):
        if word.lower() not in ("season", "part", "episode", "movie", "anime"):
            candidates.append(word)
    # 6) 完整标题最后兜底
    candidates.append(base)

    out: list[str] = []
    seen: set[str] = set()
    for cand in candidates:
        item = clean_title(cand)
        key = normalize(item)
        if not key or key in seen or not _usable(item):
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= max(1, limit):
            break
    return out


LOOSE_MIN_CJK = 4          # 至少 4 个汉字才做词序容错，短标题太容易撞词
LOOSE_BIGRAM_COVER = 0.6   # 二字组覆盖率下限


def _bigrams(text: str) -> set[str]:
    return {text[i:i + 2] for i in range(len(text) - 1)}


def loose_title_hit(variant: str, haystack: str) -> bool:
    """词序不同但用字相同的同一译名：``会长大人是女仆`` ↔ ``会长是女仆大人``。

    这两个写法子串互不包含，纯包含判断永远不命中。但**只比字符集合太松**：
    「nana」的字符集只有 {n,a}、「女朋友的朋友」只有 4 个字，随便一部剧都能
    凑齐，实测会把 762 条清单里的 36 条错标成已下载。所以收紧成四条同时成立：

    1. **只认纯中文**（不含 a-z0-9）且至少 ``LOOSE_MIN_CJK`` 个汉字——
       英文标题的词序问题少，字符集又小，做容错的收益远小于误配风险；
    2. 变体里的汉字**必须全部出现**（不是 90%，少一个字就可能只是撞词）；
    3. 二字组覆盖率 ≥ ``LOOSE_BIGRAM_COVER``，保证大体顺序还对得上；
    4. **首、末两个二字组都必须出现**——词序错位通常发生在中间
       （「会长**大人是**女仆」→「会长**是**女仆**大人**」），首尾还在；
       缺任一边就说明只是撞到了几个常用字（「女朋」在、「的朋」不在）。

    只在常规关键词全部落空后作为兜底使用，口径是「宁漏不错」。
    """
    key = normalize(variant)
    if not haystack:
        return False
    if len(_CJK_RE.findall(key)) < LOOSE_MIN_CJK:
        return False
    if _LATIN_DIGIT_RE.search(key):
        return False
    chars = set(key)
    if not chars <= set(haystack):
        return False
    grams = _bigrams(key)
    if not grams:
        return False
    hit = grams & _bigrams(haystack)
    if len(hit) / len(grams) < LOOSE_BIGRAM_COVER:
        return False
    return key[:2] in hit and key[-2:] in hit


def match_score(candidate: str, want: str) -> float:
    """0~1 的标题相似度；用于站点结果里挑最像的那一条。"""
    a, b = normalize(candidate), normalize(want)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b in a or a in b:
        return 0.85 + 0.15 * (min(len(a), len(b)) / max(len(a), len(b)))
    sa, sb = set(a), set(b)
    dice = 2 * len(sa & sb) / (len(sa) + len(sb)) if (sa and sb) else 0.0
    return max(dice * 0.7, SequenceMatcher(None, a, b).ratio() * 0.8)


def matches(candidate: str, variants: list[str], threshold: float = 0.55) -> bool:
    """候选标题是否命中任意关键词（站点结果用，比子串匹配宽容）。"""
    return any(match_score(candidate, v) >= threshold for v in variants)


def local_hit(haystack: str, variants: list[str]) -> bool:
    """本地文件名匹配：只认「关键词完整出现在归一化文本里」，避免相似度造成误报。"""
    if not haystack:
        return False
    for v in variants:
        key = normalize(v)
        if len(key) >= 3 and key in haystack:
            return True
    return False
