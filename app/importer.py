"""「看过的动漫列表」导入：解析、规范化、中文名匹配、去重、入库。

除了整齐的「一行一作品」，还兼容用户真实粘贴的清单：
  - 一行多个季标记：``关于我转生变成史莱姆这档事 S1 S2 S3``
  - 独立的季 / 剧场版行依附上一部作品：``怪兽8号`` 后面跟 ``S1（完）`` / ``剧场版（完）``
  - 完结标记、括号集数、冒号后的观感说明、书名号一律视为噪音
  - 标题里自带数字（``魔王2099`` / ``斯巴达300勇士``）不得被当成集数或年份
解析结果仍是一条条 (作品, 季, 集数范围) 记录，入库结构不变。
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from collections import OrderedDict

from . import db, metadata

SEASON_RES = [
    re.compile(r"第\s*(?:(\d{1,2})|([一二三四五六七八九十]))\s*(?:季|期)", re.IGNORECASE),
    re.compile(r"[Ss](?:eason)?\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*(?:季|期)"),
]
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
RANGE_RE = re.compile(r"(\d{1,3})\s*(?:[-~～—至]|到)\s*(\d{1,3})\s*(?:话|集|話)?")
# 单个集数必须有明确的「集/话/话数/ep」单位，避免把标题里的裸数字切掉
EPISODE_RE = re.compile(r"第?\s*(\d{1,3})\s*(?:话|集|話|ep)\b", re.IGNORECASE)
YEAR_RE = re.compile(r"[（(]\s*(19\d{2}|20\d{2})\s*[)）]|\b(19\d{2}|20\d{2})\b")

# ---------- 真实清单的噪音清洗 ----------
_ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
# 完结 / 可选 / 集数 / 年份 等括注，以及空括号
_ASIDE_PAREN_RE = re.compile(
    r"\s*[（(][^）)]*(?:完|可选|集|话|話|年|稍弱|篇|全集|剧场版|完结)[^）)]*[)）]"
)
_ANY_PAREN_RE = re.compile(r"\s*[（(][^）)]*[)）]")
_COMPLETION_RE = re.compile(
    r"(?:\s*[-–—~～]+\s*|\s+|[（(]\s*)(?:完|已看完|看完了|完结)(?:\s*[)）])?\s*$",
    re.IGNORECASE,
)
_BOOK_QUOTE_RE = re.compile(r"[《》〈〉\"“”]")
# 说明性文字：冒号后接中文叙述（不是作品副标题）
_ASIDE_COLON_RE = re.compile(r"[：:]\s*[^，。；、\n]*(?:[，。；、]|$)")
# 纯季行 / 剧场版行 / 明显是分组说明或对话的句子
_SEASON_ONLY_RE = re.compile(
    r"^\s*(?:[Ss](?:eason)?\s*\d{1,3}|第[一二三四五六七八九十\d]{1,3}\s*(?:季|期))\s*$"
)
_MOVIE_WORDS = ("剧场版", "电影版", "OVA", "OAD", "特别篇", "After Story", "序", "破", "Q", "终")
_MOVIE_PREFIX_RE = re.compile(
    r"^\s*(?:剧场版|电影版|OVA|OAD|特别篇|After Story)\b", re.IGNORECASE
)
# 只匹配明显的对话/分组说明，不误伤「请问您今天要来点兔子吗」这类作品名
_NOISE_SENTENCE_RE = re.compile(
    r"(?:拜托您|有哪些新作|能不能帮我|麻烦您|请继续|最后可以再|我导入的|如上所示)"
)
_LEADING_SEASON_RE = re.compile(
    r"^\s*(?:([Ss](?:eason)?\s*\d{1,2})|(第[一二三四五六七八九十\d]{1,3}\s*(?:季|期)))\s*[:：]?\s*"
)
# 中文连写季数：「我独自升级一二季」「S1S2」
_CN_SEASON_CHAIN_RE = re.compile(r"([一二三四五六七八九十]{2,3})季\s*$")


def normalize_title(text: str) -> str:
    title = str(text or "").strip()
    title = re.sub(r"[（(]\s*(19|20)\d{2}\s*[)）]", " ", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" ,，、\t-–—:：。·")
    return re.sub(r"\s+", " ", title).strip()


def lookup_chinese(title: str) -> str | None:
    """兼容封装：查内置 + 本地已学词典（自动匹配统一由 metadata 模块驱动）。"""
    hit = metadata.lookup_local(title)
    return hit["title_cn"] if hit else None


def clean_line(raw: str) -> str:
    """剥掉真实清单里的括注、完结标记、观感说明、书名号，保留可用标题。"""
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = _ZERO_WIDTH_RE.sub("", text).replace("\u3000", " ")
    # 完结标记可能在括注前或后，先去掉裸的「- 完」「（完）」
    text = _COMPLETION_RE.sub("", text)
    text = _ASIDE_PAREN_RE.sub(" ", text)
    text = _ANY_PAREN_RE.sub(" ", text)
    text = _BOOK_QUOTE_RE.sub("", text)
    # 冒号后的说明（「：独立日常篇，完善角色设定，建议先看。」）
    text = _ASIDE_COLON_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return normalize_title(text)


def _season_tokens(text: str) -> tuple[list[int], str]:
    """抽出一行里所有季号，并把季标记从文本中移除；返回 (季号列表, 剩余文本)。"""
    seasons: list[int] = []
    work = text

    m = _CN_SEASON_CHAIN_RE.search(work)
    if m:
        seasons.extend(CN_DIGITS[c] for c in m.group(1) if c in CN_DIGITS)
        work = work[: m.start()] + " " + work[m.end():]

    for pattern in SEASON_RES:
        while True:
            m = pattern.search(work)
            if not m:
                break
            groups = [g for g in m.groups() if g]
            token = groups[0] if groups else "1"
            value = CN_DIGITS.get(token) if not token.isdigit() else int(token)
            if value:
                seasons.append(value)
            work = work[: m.start()] + " " + work[m.end():]

    # 去重但保持顺序
    seasons = list(OrderedDict.fromkeys(seasons))
    return seasons, work


def _looks_like_season_only(text: str) -> bool:
    """纯「S1」「第2季」「剧场版」「序」这类依附上一部作品的行。"""
    if not text:
        return True
    if _SEASON_ONLY_RE.match(text):
        return True
    stripped = re.sub(r"\b(?:2nd|3rd|1st|S\d{1,3})\b", " ", text, flags=re.IGNORECASE).strip()
    if stripped in _MOVIE_WORDS:
        return True
    # 「剧场版 - 侦查任务」「OVA 特别篇」等带副标题的篇章行也是依附行
    if _MOVIE_PREFIX_RE.match(text) and len(text) <= 40:
        return True
    # 「S4 Trouble Darkness 2nd」「S1 完」：季标记开头 + 少量别名
    if re.match(r"^\s*[Ss](?:eason)?\s*\d{1,3}\b", text) and len(text) <= 40:
        return True
    return False


def _movie_note(text: str) -> str:
    """从依附行里提取可读的剧场版/篇章备注。"""
    cleaned = clean_line(text)
    cleaned = re.sub(r"^\s*[Ss](?:eason)?\s*\d{1,3}\b", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned.strip(" -–—:：。")


def parse_line(line: str) -> dict | None:
    """解析单行（规范格式）。真实清单的多行上下文由 ``parse_text`` 处理。"""
    raw = str(line or "").strip()
    if not raw or raw.startswith("#") or raw.startswith("//"):
        return None
    if _NOISE_SENTENCE_RE.search(raw) and not re.search(r"[A-Za-z]", raw):
        return None  # 「最后可以再拜托您一件事吗」「大学生篇有哪些新作」

    # CSV 行：标题,季,起始集,结束集 或 标题,起始集,结束集
    if "," in raw or "\t" in raw:
        delimiter = "\t" if "\t" in raw else ","
        parts = [p.strip() for p in next(csv.reader(io.StringIO(raw), delimiter=delimiter))]
        if parts and parts[0]:
            season = ep_from = ep_to = None
            nums = [p for p in parts[1:] if p.isdigit()]
            if len(nums) >= 3:
                season, ep_from, ep_to = int(nums[0]), int(nums[1]), int(nums[2])
            elif len(nums) == 2:
                ep_from, ep_to = int(nums[0]), int(nums[1])
            elif len(nums) == 1:
                season = int(nums[0])
            return _make_record(raw, clean_line(parts[0]), season, ep_from, ep_to)

    cleaned = clean_line(raw)
    seasons, remainder = _season_tokens(cleaned)

    # 季标记在最前面且后面还有标题（「第一季：《零之使魔》」）时，标题取冒号后的部分
    m = _LEADING_SEASON_RE.match(remainder)
    if m and len(remainder[m.end():].strip()) > 1:
        remainder = remainder[m.end():]

    year_match = YEAR_RE.search(remainder)
    if year_match:
        remainder = remainder[: year_match.start()] + " " + remainder[year_match.end():]

    ep_from = ep_to = None
    m = RANGE_RE.search(remainder)
    if m:
        ep_from, ep_to = int(m.group(1)), int(m.group(2))
        remainder = remainder[: m.start()] + " " + remainder[m.end():]
    else:
        m = EPISODE_RE.search(remainder)
        if m:
            ep_from = ep_to = int(m.group(1))
            remainder = remainder[: m.start()] + " " + remainder[m.end():]

    title = normalize_title(remainder)
    if not title:
        return None
    record = _make_record(raw, title, seasons[0] if seasons else None, ep_from, ep_to)
    record["seasons"] = seasons
    return record


def _make_record(raw: str, title: str, season, ep_from, ep_to) -> dict:
    title = normalize_title(title)
    if ep_from and ep_to and ep_to < ep_from:
        ep_from, ep_to = ep_to, ep_from
    return {
        "raw_text": raw,
        "title": title,
        "title_cn": title,  # 占位，preview / commit 时统一自动匹配
        "match_by": "none",
        "cn_edited": False,
        "season_no": season,
        "episode_from": ep_from,
        "episode_to": ep_to,
        "seasons": [season] if season else [],
        "note": "",
    }


def _key(rec: dict) -> tuple:
    return (
        rec["title"].lower(),
        rec.get("season_no") or 0,
        rec.get("episode_from") or 0,
        rec.get("episode_to") or 0,
    )


def parse_text(text: str) -> list[dict]:
    """解析整份清单：多季展开、纯季行依附上一部作品、跳过说明性文字、去重。"""
    results: list[dict] = []
    seen: set[tuple] = set()
    last_title = ""

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if _NOISE_SENTENCE_RE.search(line):
            continue

        cleaned = clean_line(line)
        seasons, _ = _season_tokens(cleaned)
        is_continuation = _looks_like_season_only(cleaned)

        if is_continuation and last_title:
            # 依附上一部作品：季行补季号，剧场版/篇章行补备注
            base_title = last_title
            note = _movie_note(cleaned) if not seasons else ""
            season_values = seasons or [None]
            lines_to_add = []
            for season in season_values:
                rec = _make_record(line, base_title, season, None, None)
                rec["note"] = note
                lines_to_add.append(rec)
        else:
            record = parse_line(line)
            if not record:
                continue
            base_title = record["title"]
            season_values = record.get("seasons") or [None]
            lines_to_add = []
            for season in season_values:
                rec = dict(record)
                rec["season_no"] = season
                rec["seasons"] = [season] if season else []
                lines_to_add.append(rec)
            if not is_continuation:
                last_title = base_title

        for rec in lines_to_add:
            # note 也要进 key：同一作品的「序 / 破 / Q / 终」是不同条目，不能被误合并
            key = (*_key(rec), rec.get("note") or "")
            if key in seen:
                continue
            seen.add(key)
            results.append(rec)
    return results


def group_watched(rows: list[dict]) -> list[dict]:
    """把看过的列表按作品折叠，供前端树状/分组展示。"""
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows or []:
        key = str(row.get("title_cn") or row.get("title") or "").strip() or "(未命名)"
        group = groups.get(key)
        if group is None:
            group = {
                "key": key, "title": row.get("title") or key, "title_cn": row.get("title_cn") or key,
                "seasons": [], "count": 0, "rows": [], "match_by": row.get("match_by"),
            }
            groups[key] = group
        group["count"] += 1
        group["rows"].append(row)
        season = row.get("season_no")
        if season and season not in group["seasons"]:
            group["seasons"].append(season)
    for group in groups.values():
        group["seasons"].sort()
    return list(groups.values())


def preview(text: str) -> dict:
    """解析 + 即时自动匹配中文名，结果当场返回给界面（不产生任何待办队列）。"""
    db.init_db()
    metadata.ensure_builtin_loaded()
    records = parse_text(text)
    match_result = metadata.match_titles([r["title"] for r in records])
    existing = {
        (r["title"].lower(), r["season_no"] or 0, r["episode_from"] or 0, r["episode_to"] or 0)
        for r in db.rows("SELECT title, season_no, episode_from, episode_to FROM watched_list")
    }
    for rec in records:
        rec["duplicate"] = _key(rec) in existing
        hit = match_result["results"].get(rec["title"])
        if hit and hit.get("matched"):
            rec["title_cn"] = hit["title_cn"]
            rec["match_by"] = hit["by"]
        if rec.get("season_no") and rec["season_no"] > 1:
            season_cn = metadata.lookup_season(rec["title"], rec["season_no"])
            if season_cn:
                rec["title_cn"] = season_cn
        rec["matched_cn"] = bool(rec["title_cn"] and rec["title_cn"] != rec["title"])
    return {
        "ok": True,
        "rows": records,
        "total": len(records),
        "duplicates": sum(1 for r in records if r["duplicate"]),
        "unmatched_cn": sum(1 for r in records if r["match_by"] == "none"),
        "groups": group_watched(records),
        "match": {
            "matched": match_result["matched"],
            "by_builtin": match_result["by_builtin"],
            "by_online": match_result["by_online"],
            "online_available": match_result["online_available"],
            "online_error": match_result["online_error"],
        },
    }


def commit(records: list[dict], source: str = "manual") -> dict:
    """入库；界面里手改过的中文名会被记住，下次导入自动命中。"""
    db.init_db()
    metadata.ensure_builtin_loaded()
    inserted = skipped = learned = 0
    for rec in records:
        key = _key(rec)
        exists = db.scalar(
            "SELECT COUNT(*) FROM watched_list WHERE lower(title) = ? AND "
            "COALESCE(season_no,0) = ? AND COALESCE(episode_from,0) = ? AND COALESCE(episode_to,0) = ?",
            key,
        )
        if exists:
            skipped += 1
            continue
        title = rec.get("title") or ""
        title_cn = (rec.get("title_cn") or title).strip()
        match_by = rec.get("match_by") or "none"
        if rec.get("cn_edited") and title_cn and title_cn != title:
            metadata.remember(title, title_cn, source="user")
            match_by = "user"
            learned += 1
        db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, match_by, season_no, "
            "episode_from, episode_to, source, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.get("raw_text"), title, title_cn, match_by,
                rec.get("season_no"), rec.get("episode_from"), rec.get("episode_to"), source,
                rec.get("note") or "",
            ),
        )
        inserted += 1
    return {"ok": True, "inserted": inserted, "skipped": skipped, "learned": learned}
