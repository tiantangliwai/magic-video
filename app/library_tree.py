"""媒体库树状分组（纯逻辑，不访问数据库、不写文件）。

两种视角：
    - ``series``：作品 → 季 → 集。真实下载目录里同一部番往往放在同一个发布文件夹，
      文件名却带字幕组、分辨率、集数等标记，所以归并优先级是：
      **已匹配到的干净中文名 → 发布目录名（去掉技术标记与季标记）→ 文件名解析标题**。
      只有 ≥2 个文件的目录才会作为分组依据，避免把一堆单文件电影错并成一部剧。
    - ``folder``：扫描根目录 → 子目录 → 文件。不在任何扫描根目录下的文件
      按盘符 / 顶层目录兜底归属。

季数优先取扫描时解析出的 ``season_no``，缺失时从文件名 / 目录名（Season 2、S2、
第三季…）推断；没有季标记的文件直接挂在作品组下，不再造「未标注季」这一层，
绝不丢弃。分组只用于展示：不改数据库、不改用户文件名、不重命名磁盘视频。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from functools import lru_cache

from .scanner import parse_filename

_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

# 季标记既用于「归并时抹掉」，也用于「分季时读出来」
_SEASON_MARK_RE = re.compile(
    r"\b(?:season|series)\s*\d{1,2}\b"
    r"|\b\d{1,2}(?:st|nd|rd|th)\s+season\b"
    r"|第\s*[0-9一二三四五六七八九十]{1,3}\s*[季期]"
    r"|(?<![A-Za-z0-9])S\s*\d{1,2}(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SEASON_PATTERNS = (
    re.compile(r"\b(?:season|series)\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+season\b", re.IGNORECASE),
    re.compile(r"第\s*([0-9一二三四五六七八九十]{1,3})\s*[季期]"),
    re.compile(r"(?<![A-Za-z0-9])S(\d{1,2})(?![A-Za-z0-9])", re.IGNORECASE),
)
_EPISODE_BRACKET_RE = re.compile(r"\[(\d{1,3})\]")
_TECH_WORDS = (
    "1080", "720", "2160", "4k", "bdrip", "bluray", "web-dl", "web-rip", "webrip",
    "hevc", "x264", "x265", "h264", "h265", "aac", "flac", "10bit", "8bit", "opus",
    "mkv", "mp4", "hdr", "yts", "zerotv", "zero", "bphdtv", "pthdtv", "bthdtv", "17bt",
    "www", "com", "cn", "net", "bt",
    "全集", "集完结", "中文字幕", "字幕", "简体", "繁体", "外挂", "内嵌", "国配",
    "特典", "映像", "合集", "完结", "简", "繁", "tv", "bd", "dvd", "sp", "pv", "ova",
)
# 站点 / 字幕组名与纯中文噪音词：只用于「目录片段」筛选，不参与标题归一化
_SITE_NOISE_RE = re.compile(r"高清剧集网|剧集网|动漫网|字幕组|字幕社|www\.|\.com|\.cn|bthdtv|17bt", re.IGNORECASE)
_CJK_NOISE_RE = re.compile(
    r"字幕|简体|繁体|内嵌|外挂|全集|完结|合集|特典|映像|剧集网|高清|"
    r"第\s*[0-9一二三四五六七八九十]+\s*[季期话集]|\d+\s*集"
)
# 1.36G / 720P / 4GB 这类体积或分辨率 token 不能当作品名
_SIZE_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?\s*[kmgtp]b?i?t?e?s?$", re.IGNORECASE)
_GENERIC_FOLDERS = {
    "pv", "sp", "sp1", "sp2", "subs", "sub", "cd", "cd1", "cd2", "extras", "extra",
    "ova", "special", "specials", "season", "data", "video", "videos", "movie", "movies",
    "anime", "download", "downloads", "torrents", "temp", "tmp", "new", "1", "2", "3",
    "特典", "映像", "全集", "视频", "电影", "动漫", "剧集", "新建文件夹",
}
_NOISE_WORDS_RE = re.compile(
    r"\b(?:1080[pi]|720[pi]|2160[pi]|4k|bdrip|bluray|web-?dl|web-?rip|hevc|x264|x265|"
    r"h\.?264|h\.?265|aac|flac|10bit|8bit|mkv|mp4|hdr|opus|tv|bd|dvd|zero|zerotv|"
    r"ita|eng|ac3|5\.1|yts\.?ag|www)\b|全\s*\d*\s*集|中文字幕|简体|繁体|内嵌|外挂|"
    r"字幕|特典|映像|合集|完结|第\s*[0-9一二三四五六七八九十]{1,3}\s*[季期]",
    re.IGNORECASE,
)


@lru_cache(maxsize=8192)
def norm_title(text) -> str:
    """标题归一化：全角转半角、大小写统一、去分隔符与残留的季标记。"""
    s = unicodedata.normalize("NFKC", str(text or "")).strip().lower()
    s = _SEASON_MARK_RE.sub(" ", s)
    s = re.sub(r"[\[\]（）()【】{}._\-–—+~:：,，、!！?？'\"]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _int(value) -> int | None:
    try:
        if value is None or value == "":
            return None
        number = int(value)
        return number if number >= 0 else None
    except (TypeError, ValueError):
        return None


def _path_parts(path) -> list[str]:
    """把 Windows / POSIX 路径拆成段，保留开头的 '/' 与盘符作为一段。"""
    text = str(path or "").replace("\\", "/")
    parts = [p for p in text.split("/") if p]
    return (["/"] if text.startswith("/") else []) + parts


def _season_number(token: str) -> int | None:
    digits = re.search(r"\d+", token)
    if digits:
        return int(digits.group())
    chinese = re.search(r"[一二三四五六七八九十]+", token)
    if not chinese:
        return None
    text = chinese.group()
    if text == "十":
        return 10
    if text.startswith("十"):
        return 10 + _CN_DIGITS.get(text[1:], 0)
    if text.endswith("十"):
        return _CN_DIGITS.get(text[0], 0) * 10
    return _CN_DIGITS.get(text, None)


def _season_from_text(text) -> int | None:
    for pattern in _SEASON_PATTERNS:
        match = pattern.search(str(text or ""))
        if match:
            number = _season_number(match.group(1))
            if number is not None:
                return number
    return None


def _is_generic_folder(name: str) -> bool:
    low = str(name or "").strip().lower()
    if not low or low in _GENERIC_FOLDERS:
        return True
    # 纯粹的「Season 2 / S2 / 第三季」只是季目录，不能当作品名
    if _SEASON_MARK_RE.fullmatch(low):
        return True
    return False


# 预编译：旧实现每次调用都 ``re.escape(word)`` 拼新正则，re 的 512 条缓存被冲掉后
# 会反复走 re._compile。剧集墙 2000 个文件 × 40 个技术词 = 8 万次编译，
# 实测占 build_tree 一半以上耗时。词表是常量，编译一次即可。
_NON_WORD_RE = re.compile(r"[\d\W_]+")
_RANGE_RE = re.compile(r"\d{1,3}\s*[-~+]\s*\d{1,3}")
_SHORT_TECH_RES = {
    word: re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")
    for word in _TECH_WORDS if word.isascii() and len(word) <= 3
}


def _is_tech_token(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return True
    if _NON_WORD_RE.fullmatch(low):              # 纯数字 / 标点，如 01-12、2020
        return True
    if _SIZE_TOKEN_RE.match(low):                # 1.36G / 720P 这类体积或分辨率
        return True
    if _RANGE_RE.search(low):
        return True
    for word in _TECH_WORDS:
        short = _SHORT_TECH_RES.get(word)
        if short is not None:
            # 短标记必须独立成词，避免把 Spy×Family / BD 开头的作品名误杀
            if short.search(low):
                return True
        elif word in low:
            return True
    return False


@lru_cache(maxsize=4096)
def _strip_release_noise(text: str) -> str:
    """把发布目录名里的字幕组 / 分辨率 / 集数范围 / 季标记等噪音去掉。"""
    s = unicodedata.normalize("NFKC", str(text or ""))
    # 只吃掉域名本身：旧写法「www. + 任意非空白」会把紧跟在域名后面的
    # 中文片名（WWW.17BT.CN@爱的躯壳）一起洗掉。
    s = re.sub(r"www\.[a-z0-9.\-]+", " ", s, flags=re.IGNORECASE)
    # 保护「2.5 次元」这类标题里的小数点，避免被当成发布标记洗掉
    s = re.sub(r"(?<=\d)\.(?=\d)", "\u0001", s)
    s = _SEASON_MARK_RE.sub(" ", s)
    s = re.sub(r"\((?:19|20)\d{2}\)", " ", s)
    s = re.sub(r"\b(?:19|20)\d{2}\b", " ", s)
    s = re.sub(r"\b\d{1,3}\s*[-~+]\s*\d{1,3}\b", " ", s)
    s = _NOISE_WORDS_RE.sub(" ", s)
    s = re.sub(r"[\[\]（）()【】{}._\-–—+~:：,，、!！?？'\"]+", " ", s)
    s = s.replace("\u0001", ".")
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=4096)
def _folder_label(folder: str) -> tuple[str, str]:
    """从发布目录名提取归并键与展示名；中文片名优先，技术/字幕组噪音逐段剔除。

    真实目录里片名常和站点、分辨率挤在同一段，例如
    ``WWW.17BT.CN@爱的躯壳[韩语中字幕WAF][1.36G]`` 或
    ``【高清剧集网 www.BTHDTV.com】安娜[全6集][简繁英字幕].Anna...``。
    旧实现是「整段只要含一个技术词就丢弃」，会把中文片名连坐洗掉，只剩
    ``1.36G`` 这种体积 token 当作品名。改成按括号 / 分隔符切片逐段判定。
    """
    text = unicodedata.normalize("NFKC", str(folder or ""))
    segments = re.split(r"[\[\]（）()【】{}._\-–—+~:：,，、!！?？'\"@]+", text)
    segments += re.findall(r"\[([^\[\]]*)\]", text)
    kept: list[str] = []
    for seg in segments:
        seg = (seg or "").strip()
        if not seg or _is_tech_token(seg):
            continue
        if _SITE_NOISE_RE.search(seg):
            # 站点名 + 片名混在同一段时，把站点名剔掉再判断剩下的内容
            seg = _SITE_NOISE_RE.sub(" ", seg).strip()
            if not seg:
                continue
        cleaned = _strip_release_noise(seg)
        if len(norm_title(cleaned)) < 2:
            continue
        if not re.search(r"[\u4e00-\u9fff]", cleaned) and not re.search(r"[A-Za-z]{3,}", cleaned):
            continue
        kept.append(cleaned)
    cjk = [t for t in kept
           if re.search(r"[\u4e00-\u9fff]", t) and not _CJK_NOISE_RE.search(t)]
    chosen = cjk if cjk else [t for t in kept if re.search(r"[A-Za-z]{3,}", t)]
    display = " ".join(dict.fromkeys(chosen)).strip()[:60]
    if len(norm_title(display)) < 2:
        return "", ""
    return "folder::" + norm_title(display), display


def _roots(directories: list[dict] | None) -> list[tuple[str, list[str], str]]:
    roots = []
    for d in directories or []:
        path = str(d.get("path") or "").strip()
        if path:
            roots.append((path, [p.lower() for p in _path_parts(path)], str(d.get("id") or "")))
    roots.sort(key=lambda r: len(r[1]), reverse=True)
    return roots


def _relative_parts(path, roots) -> list[str]:
    parts = _path_parts(path)
    for root in roots:
        if len(parts) >= len(root[1]) and [p.lower() for p in parts[:len(root[1])]] == root[1]:
            return parts[len(root[1]):]
    return parts


def folder_key_of(row: dict, roots=None) -> tuple[str, str]:
    """取最靠内的一层「非通用目录」作为发布目录，PV / Season 等目录向上归并。"""
    parent = _relative_parts(row.get("path"), roots or _roots(None))[:-1]
    for i in range(len(parent) - 1, -1, -1):
        if _is_generic_folder(parent[i]):
            continue
        key, label = _folder_label(parent[i])
        # 最内层目录全是技术噪音时继续往外找，别让这一批文件退回「按文件名各自成组」
        if key:
            return key, label
    return "", ""


def clean_cn_key(row: dict) -> str:
    """短且干净的中日英标题才当分组键；native 常把整串文件名回显，按形状过滤。"""
    cn = str(row.get("title_cn") or "").strip()
    if not cn or "[" in cn or "]" in cn or len(cn) > 40:
        return ""
    if re.search(r"\.(mkv|mp4|avi|ts|rmvb|mov|wmv|flv|m2ts)\b", cn, re.IGNORECASE):
        return ""
    key = norm_title(cn)
    return "cn::" + key if key else ""


def season_of(row: dict) -> int | None:
    """季数：数据库解析值 → 文件名 → 目录名（由内向外，内层优先）。"""
    value = _int(row.get("season_no"))
    if value is not None:
        return value
    parsed = parse_filename(str(row.get("filename") or ""))
    if parsed.get("season_no") is not None:
        return _int(parsed["season_no"])
    for part in reversed(_path_parts(row.get("path"))[:-1]):
        value = _season_from_text(part)
        if value is not None:
            return value
    return None


def episode_of(row: dict) -> int | None:
    value = _int(row.get("episode_no"))
    if value is not None:
        return value
    filename = str(row.get("filename") or "")
    value = _int(parse_filename(filename)["episode_no"])
    if value is not None:
        return value
    # 字幕组常见：[DBD-Raws][片名][01][1080P]… 里的 [01]
    match = _EPISODE_BRACKET_RE.search(filename)
    return _int(match.group(1)) if match else None


def _enrich(row: dict) -> dict:
    """给展示用的文件行补上树状视图算出的季 / 集；原始字段一律保留。"""
    item = dict(row)
    item["tree_season"] = season_of(row)
    item["tree_episode"] = episode_of(row)
    return item


def _file_order(item: dict) -> tuple:
    season = item.get("tree_season")
    episode = item.get("tree_episode")
    return (
        season if season else 999,
        1 if episode is None else 0,
        episode or 0,
        str(item.get("filename") or "").lower(),
        int(item.get("id") or 0),
    )


def _completed(item: dict) -> bool:
    return int(item.get("completed") or 0) == 1


def _playing(item: dict) -> bool:
    return not _completed(item) and int(item.get("position_ms") or 0) > 0


def _next_file_id(files: list[dict]) -> int | None:
    """优先回到看了一半的那一集，其次最早未看完的一集，全看完则回到第一集。"""
    if not files:
        return None
    in_progress = [f for f in files if _playing(f)]
    if in_progress:
        return max(in_progress, key=lambda f: str(f.get("last_played_at") or ""))["id"]
    unwatched = [f for f in files if not _completed(f)]
    return (unwatched[0] if unwatched else files[0])["id"]


def _node(node_id: str, kind: str, label: str) -> dict:
    return {
        "id": node_id, "kind": kind, "label": label, "sub": "",
        "file_count": 0, "watched_count": 0, "playing_count": 0, "next_file_id": None,
        "children": [], "files": [], "_all": [], "_hint": "", "_season": 0,
        "_titles": [], "_guesses": [], "_folders": [],
    }


def _finalize(node: dict) -> None:
    """递归算计数 / 续播集 / 副标题，并移除只用于构建的内部字段。"""
    for child in node["children"]:
        _finalize(child)
    files = sorted(node["_all"], key=_file_order)
    node["file_count"] = len(files)
    node["watched_count"] = sum(1 for f in files if _completed(f))
    node["playing_count"] = sum(1 for f in files if _playing(f))
    node["next_file_id"] = _next_file_id(files)
    node["files"] = sorted(node["files"], key=_file_order) if node["files"] else []
    # 「整部剧全部看完」判定只用于界面上是否出现人工删除入口；
    # file_ids 供后端二次校验，避免前端伪造 id 删到别的作品。
    is_series = node["kind"] == "series"
    node["all_done"] = is_series and bool(files) and node["watched_count"] == len(files)
    node["removable_count"] = (
        sum(1 for f in files if _completed(f) and not f.get("deleted_at")) if is_series else 0
    )
    node["file_ids"] = (
        [int(f["id"]) for f in files if f.get("id") is not None] if node["all_done"] else []
    )
    hint = node["_hint"]
    if node["kind"] == "series":
        seasons = len(node["children"])
        node["sub"] = ((hint + " · " if hint else "")
                       + (f"{seasons} 季 · " if seasons else "")
                       + f"{len(files)} 集")
    elif node["kind"] == "folder":
        node["sub"] = f"{len(files)} 个文件" + (f" · {len(node['children'])} 个子目录" if node["children"] else "")
    else:
        node["sub"] = f"{len(files)} 集 · 已看完 {node['watched_count']}"
    # 季节点把季号保留给「按季海报墙」用（内部字段 _season 随后会被清掉）
    if node["kind"] == "season":
        node["season_no"] = node["_season"]
    for key in ("_all", "_hint", "_season", "_titles", "_guesses", "_folders"):
        node.pop(key, None)


def series_tree(rows: list[dict], directories: list[dict] | None = None,
                labels: dict | None = None) -> dict:
    """``labels`` 是用户手工改过的作品名（按分组 id 索引），只影响展示标签。"""
    enriched = [_enrich(r) for r in rows]
    roots = _roots(directories)
    # 第一遍：算出每行的中文名 / 发布目录 / 文件名候选键，并统计目录里的文件数
    candidates = []
    folder_counts: Counter = Counter()
    for item in enriched:
        cn = clean_cn_key(item)
        fk, folder_label = folder_key_of(item, roots)
        guess = norm_title(item.get("title_guess") or item.get("filename") or "")
        candidates.append((item, cn, fk, folder_label, guess))
        if fk:
            folder_counts[fk] += 1

    # 只有 ≥2 个文件的目录才作为分组依据，避免把一堆单文件电影错并成一部剧
    adjusted = []
    for item, cn, fk, folder_label, guess in candidates:
        if not fk or folder_counts[fk] < 2:
            fk, folder_label = "", ""
        adjusted.append((item, cn, fk, folder_label, guess))

    alias: dict[str, str] = {}
    for item, cn, fk, folder_label, guess in adjusted:
        if not cn:
            continue
        alias.setdefault(cn, cn)
        if fk:
            alias.setdefault(fk, cn)
        if guess:
            alias.setdefault(guess, cn)

    groups: dict[str, dict] = {}
    for item, cn, fk, folder_label, guess in adjusted:
        key = (alias.get(fk) or alias.get(guess) or cn or fk
               or ("guess::" + guess if guess else "guess::" + norm_title(item.get("filename"))))
        group = groups.get(key)
        if group is None:
            group = _node("series::" + key, "series", key)
            groups[key] = group
        group["_titles"].append(str(item.get("title_cn") or "").strip()
                                if clean_cn_key(item) else "")
        group["_guesses"].append(str(item.get("title_guess") or "").strip())
        if folder_label:
            group["_folders"].append(folder_label)
        group["_all"].append(item)

        season = item.get("tree_season") or 0
        if season == 0:
            # 剧场版 / 单集 / 未标季：直接挂在作品组下，不再造「未标注季」这一层
            group["files"].append(item)
            continue
        season_id = f"{group['id']}::season::{season}"
        season_node = next((c for c in group["children"] if c["id"] == season_id), None)
        if season_node is None:
            season_node = _node(season_id, "season", f"第 {season} 季")
            season_node["_season"] = season
            group["children"].append(season_node)
        season_node["_all"].append(item)
        season_node["files"].append(item)

    overrides = {str(k): str(v).strip() for k, v in (labels or {}).items() if str(v).strip()}
    result = []
    for key, group in groups.items():
        # 多版本合并：同一 (季, 集) 的多个文件在展示层并成一条
        _apply_version_merge(group)
        cns = [t for t in group["_titles"] if t]
        folders = [t for t in group["_folders"] if t]
        guesses = [g for g in group["_guesses"] if g]
        if cns:
            label = Counter(cns).most_common(1)[0][0]
        elif folders:
            label = Counter(folders).most_common(1)[0][0]
        else:
            label = Counter(guesses).most_common(1)[0][0] if guesses else key.split("::")[-1]
        group["label"] = overrides.get(group["id"], label[:60])
        other = [g for g in guesses if g and norm_title(g) != norm_title(label)]
        group["_hint"] = other[0][:40] if other else ""
        # 有编号的季按 1、2、3… 排，未标注季永远放最后
        group["children"].sort(key=lambda s: (1, 0) if s["_season"] == 0 else (0, s["_season"]))
        _finalize(group)
        result.append(group)
    result.sort(key=lambda g: (norm_title(g["label"]), g["label"]))
    return {"mode": "series", "groups": result, "total": len(rows), "group_count": len(result)}


def directory_tree(rows: list[dict], directories: list[dict] | None = None) -> dict:
    roots = _roots(directories)
    enriched = [_enrich(r) for r in rows]
    top: dict[str, dict] = {}
    index: dict[str, dict] = {}

    for item in enriched:
        parent = _path_parts(item.get("path"))[:-1]
        root = next((r for r in roots
                     if len(parent) >= len(r[1])
                     and [p.lower() for p in parent[:len(r[1])]] == r[1]), None)
        if root:
            chain = [("folder::" + root[0] + "::" + root[2], root[0])]
            rest = parent[len(root[1]):]
            prefix = root[0]
            for seg in rest:
                prefix = prefix + "/" + seg
                chain.append(("folder::" + prefix + "::" + root[2], seg))
        else:
            root_label = parent[0] if parent else "（根目录）"
            rest = parent[1:]
            chain = [("folder::" + root_label, root_label)]
            prefix = root_label
            for seg in rest:
                prefix = prefix + "/" + seg
                chain.append(("folder::" + prefix, seg))

        node = None
        for depth, (node_id, label) in enumerate(chain):
            if depth == 0:
                node = top.get(node_id)
                if node is None:
                    node = _node(node_id, "folder", label)
                    top[node_id] = node
                    index[node_id] = node
            else:
                child = index.get(node_id)
                if child is None:
                    child = _node(node_id, "folder", label)
                    node["children"].append(child)
                    index[node_id] = child
                node = child
            node["_all"].append(item)
        if node is not None:
            node["files"].append(item)

    groups = list(top.values())
    def _sort_children(n):
        n["children"].sort(key=lambda c: c["label"].lower())
        for c in n["children"]:
            _sort_children(c)
    for group in groups:
        _sort_children(group)
        _finalize(group)
    groups.sort(key=lambda g: g["label"].lower())
    return {"mode": "folder", "groups": groups, "total": len(rows), "group_count": len(groups)}


def _version_entry(item: dict) -> dict:
    return {
        "file_id": item.get("id"),
        "filename": item.get("filename") or "",
        "path": item.get("path") or "",
        "size": item.get("size"),
        "completed": int(item.get("completed") or 0),
        "percent": item.get("percent") or 0,
    }


def _collapse_versions(items: list[dict]) -> list[dict]:
    """把同一 (季, 集) 的多个文件合并成一条，其余作为 ``versions`` 挂在主条目上。

    收藏党常同时存 BD / WEB 两个版本，不合并的话一集会在剧集展示里出现两次、
    「12 集」显示成「13 集」。合并只在**展示层**做：数据库里仍是各自独立的行，
    播放进度也各自独立，用户点「版本」下拉自选播哪一个。

    主条目优先取「已看完 > 有进度 > 体积大 > id 小」的那份，这样点播默认落到
    最可能有用的版本上。
    """
    def rank(item: dict) -> tuple:
        return (
            0 if int(item.get("completed") or 0) == 1 else 1,
            0 if int(item.get("position_ms") or 0) > 0 else 1,
            -int(item.get("size") or 0),
            int(item.get("id") or 0),
        )

    buckets: dict[tuple, list[dict]] = {}
    order: list[dict] = []
    for item in items:
        item.pop("versions", None)
        episode = item.get("tree_episode")
        if episode is None:
            # 没集数的（剧场版 / 单集）不参与合并：它们本来就不该互相顶替
            order.append(item)
            continue
        key = (item.get("tree_season") or 0, episode)
        if key not in buckets:
            buckets[key] = [item]
            order.append(item)
        else:
            buckets[key].append(item)

    for bucket in buckets.values():
        if len(bucket) < 2:
            bucket[0]["versions"] = [_version_entry(bucket[0])]
            continue
        primary = sorted(bucket, key=rank)[0]
        primary["versions"] = [_version_entry(x) for x in sorted(bucket, key=rank)]
        primary["version_count"] = len(bucket)
        order[order.index(primary)] = primary
    return order


def _apply_version_merge(group: dict) -> None:
    """把合并结果铺回作品节点与它下面的季节点。"""
    group["_all"] = _collapse_versions(group["_all"])
    by_season: dict[int, list[dict]] = {}
    for item in group["_all"]:
        by_season.setdefault(item.get("tree_season") or 0, []).append(item)
    group["files"] = list(by_season.get(0, []))
    for season_node in group["children"]:
        season_node["_all"] = list(by_season.get(season_node["_season"], []))
        season_node["files"] = list(season_node["_all"])


def build_tree(rows: list[dict], mode: str = "series",
               directories: list[dict] | None = None,
               labels: dict | None = None) -> dict:
    """按视图模式构建树；未知模式一律降级为作品树，绝不把输入拼进 SQL。"""
    wanted = str(mode or "series").strip().lower()
    if wanted in ("folder", "directory", "path"):
        return directory_tree(rows, directories)
    return series_tree(rows, directories, labels)
