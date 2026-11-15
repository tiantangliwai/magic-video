"""媒体库树状分组（纯逻辑，不访问数据库、不写文件）。

两种视角：
    - ``series``：作品 → 季 → 集。真实下载目录里同一部番往往放在同一个发布文件夹，
      文件名却带字幕组、分辨率、集数等标记，所以归并优先级是：
      **已匹配到的干净中文名 → 发布目录名（去掉技术标记与季标记）→ 文件名解析标题**。
      只有 ≥2 个文件的目录才会作为分组依据，避免把一堆单文件电影错并成一部剧。
    - ``folder``：扫描根目录 → 子目录 → 文件。不在任何扫描根目录下的文件
      按盘符 / 顶层目录兜底归属。

季数优先取扫描时解析出的 ``season_no``，缺失时从文件名 / 目录名（Season 2、S2、
第三季…）推断；剧场版、特典等没有集数的文件进「未标注季」，绝不丢弃。
分组只用于展示：不改数据库、不改用户文件名、不重命名磁盘视频。
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter

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
    "mkv", "mp4", "hdr", "yts", "zerotv", "zero", "bphdtv", "pthdtv", "17bt",
    "全集", "集完结", "中文字幕", "字幕", "简体", "繁体", "外挂", "内嵌", "国配",
    "特典", "映像", "合集", "完结", "简", "繁", "tv", "bd", "dvd", "sp", "pv", "ova",
)
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


def _is_tech_token(text: str) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return True
    if re.fullmatch(r"[\d\W_]+", low):          # 纯数字 / 标点，如 01-12、2020
        return True
    if re.search(r"\d{1,3}\s*[-~+]\s*\d{1,3}", low):
        return True
    for word in _TECH_WORDS:
        if word.isascii() and len(word) <= 3:
            # 短标记必须独立成词，避免把 Spy×Family / BD 开头的作品名误杀
            if re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", low):
                return True
        elif word in low:
            return True
    return False


def _strip_release_noise(text: str) -> str:
    """把发布目录名里的字幕组 / 分辨率 / 集数范围 / 季标记等噪音去掉。"""
    s = unicodedata.normalize("NFKC", str(text or ""))
    s = re.sub(r"www\.[^\s]+", " ", s, flags=re.IGNORECASE)
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


def _folder_label(folder: str) -> tuple[str, str]:
    """从发布目录名提取归并键与展示名；技术标记全部剔除，中文标题优先保留。"""
    tokens = re.findall(r"\[([^\[\]]*)\]", folder)
    outside = re.sub(r"\[[^\[\]]*\]", " ", folder)
    candidates = [outside.strip()] + [t.strip() for t in tokens]
    kept = []
    for token in candidates:
        if _is_tech_token(token):
            continue
        cleaned = _strip_release_noise(token)
        if len(norm_title(cleaned)) >= 2:
            kept.append(cleaned)
    cjk = [t for t in kept if re.search(r"[\u4e00-\u9fff]", t)]
    chosen = cjk if cjk else kept
    display = " ".join(dict.fromkeys(chosen)).strip()
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
        return _folder_label(parent[i])
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
    hint = node["_hint"]
    if node["kind"] == "series":
        node["sub"] = (hint + " · " if hint else "") + f"{len(node['children'])} 季 · {len(files)} 集"
    elif node["kind"] == "folder":
        node["sub"] = f"{len(files)} 个文件" + (f" · {len(node['children'])} 个子目录" if node["children"] else "")
    else:
        node["sub"] = f"{len(files)} 集 · 已看完 {node['watched_count']}"
    for key in ("_all", "_hint", "_season", "_titles", "_guesses", "_folders"):
        node.pop(key, None)


def series_tree(rows: list[dict], directories: list[dict] | None = None) -> dict:
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
        season_id = f"{group['id']}::season::{season}"
        season_node = next((c for c in group["children"] if c["id"] == season_id), None)
        if season_node is None:
            season_node = _node(season_id, "season", f"第 {season} 季" if season else "未标注季")
            season_node["_season"] = season
            group["children"].append(season_node)
        season_node["_all"].append(item)
        season_node["files"].append(item)

    result = []
    for key, group in groups.items():
        cns = [t for t in group["_titles"] if t]
        folders = [t for t in group["_folders"] if t]
        guesses = [g for g in group["_guesses"] if g]
        if cns:
            label = Counter(cns).most_common(1)[0][0]
        elif folders:
            label = Counter(folders).most_common(1)[0][0]
        else:
            label = Counter(guesses).most_common(1)[0][0] if guesses else key.split("::")[-1]
        group["label"] = label[:60]
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


def build_tree(rows: list[dict], mode: str = "series",
               directories: list[dict] | None = None) -> dict:
    """按视图模式构建树；未知模式一律降级为作品树，绝不把输入拼进 SQL。"""
    wanted = str(mode or "series").strip().lower()
    if wanted in ("folder", "directory", "path"):
        return directory_tree(rows, directories)
    return series_tree(rows, directories)
