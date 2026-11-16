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
# 说明性文字：冒号后接中文叙述（不是作品副标题）。
# 注意：clean_line 先做 NFKC（全角「，；」-> 半角），这里刻意只把全角标点写进
# 排除集，半角逗号/分号因此不会提前终止，整段观感说明能一直删到句号/行尾。
# 若补上半角标点，反而会把「：独立日常篇,完善角色设定,建议先看。」切成残留碎串。
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

# ---------- 存量脏数据清洗（旧版解析器留下的空壳括号 / 完结标记 / 垃圾行） ----------
# 字符类刻意不含任何数字，因此「(2013)」「（24集）」这类合法括注不会被误删。
_DIRTY_SHELL_RE = re.compile(
    r"\s*[（(][\s,，、年集话話完结可选稍弱篇全集剧场版·]*[)）]"
)
_LEADING_COLON_RE = re.compile(r"^\s*[：:]\s*")
_JUNK_TITLE_RE = re.compile(r"^[\s\-–—~～。·、,，]*完?[\s。·]*$")


def clean_watched_title(title: str) -> str:
    """清洗已入库的脏标题；只剥噪音外壳，不动合法年份/集数括注与季集字段。

    与 ``clean_line`` 的关键区别：冒号语义按位置区分，绝不能一刀切。
      - ``《标题》：观感说明，建议先看。`` → 书名号闭合后的冒号是说明，删
      - ``《剧场版：恩底弥翁的奇迹》``   → 书名号内部的冒号是作品名，保留
      - ``第二季：《标题》``             → 冒号在书名号前，只删行首冒号、保留标题
    另外 NFKC 会把「～ / ：」改写成半角，这里还原成作品名原貌。
    """
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = _ZERO_WIDTH_RE.sub("", text).replace("\u3000", " ")
    text = _COMPLETION_RE.sub("", text)            # 行尾「- 完」「（完）」
    text = _DIRTY_SHELL_RE.sub(" ", text)          # 只删空壳「（ 年， ）」「（ ）」
    close = text.find("》")
    if close >= 0:
        head, tail = text[:close + 1], text[close + 1:]
        text = head + _ASIDE_COLON_RE.sub(" ", tail)
    text = _BOOK_QUOTE_RE.sub("", text)             # 书名号
    text = _LEADING_COLON_RE.sub("", text)         # 只删行首残留冒号
    text = text.replace(":", "：")                  # 还原作品名里的中文冒号
    text = re.sub(r"(?<!\d)~(?!\d)", "～", text)   # 还原标题波浪号，保留 1~12 范围
    return normalize_title(text)


def normalize_title(text: str) -> str:
    title = str(text or "").strip()
    title = re.sub(r"[（(]\s*(19|20)\d{2}\s*[)）]", " ", title)
    title = re.sub(r"\s+", " ", title)
    title = title.strip(" ,，、\t-–—:：。·")
    # NFKC 会把作品名里的「～」变成「~」。只还原前后都不是数字的波浪号，
    # 这样「1~12」这类集数范围不受影响，标题与离线词典/存量库保持一致。
    title = re.sub(r"(?<!\d)~(?!\d)", "～", title)
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

    # CSV 行：标题,季,起始集,结束集 或 标题,起始集,结束集。
    # 只有后续列「全是数字/空且至少一个数字」才算 CSV；否则自然语言里的
    # 半角逗号（如「（2007 年, 12 集）」）会把标题截断成残缺串。
    if "," in raw or "\t" in raw:
        delimiter = "\t" if "\t" in raw else ","
        parts = [p.strip() for p in next(csv.reader(io.StringIO(raw), delimiter=delimiter))]
        numeric_tail = (len(parts) >= 2 and parts[0]
                        and any(p.isdigit() for p in parts[1:])
                        and all(p == "" or p.isdigit() for p in parts[1:]))
        if numeric_tail:
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
            if not record and is_continuation and not last_title:
                # 旧清单里「第二季：《作品名》（年，集）。 - 完」单独成行时，
                # clean_line 会把冒号后的标题当成观感说明删掉。改用原文书名号
                # 救回标题；纯「S1 - 完」这类无书名号的垃圾行仍然丢弃。
                book = re.search(r"《([^》]+)》", line)
                if book:
                    fallback = clean_watched_title(book.group(1))
                    if fallback and not _JUNK_TITLE_RE.match(fallback):
                        ep_match = EPISODE_RE.search(line[book.end():])
                        ep = int(ep_match.group(1)) if ep_match else None
                        record = _make_record(
                            line, fallback, seasons[0] if seasons else None, ep, ep
                        )
                        record["seasons"] = seasons
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
    # 「待确认区」：未匹配到中文名的行单独列出来，由用户显式处理
    # （改中文名 / 选择不导入）。不给每行建待办记录，只在本次预览结果里体现。
    pending = [
        {
            "index": idx,
            "title": rec["title"],
            "raw_text": rec.get("raw_text") or "",
            "season_no": rec.get("season_no"),
            "episode_from": rec.get("episode_from"),
            "episode_to": rec.get("episode_to"),
            "note": rec.get("note") or "",
            "duplicate": rec["duplicate"],
        }
        for idx, rec in enumerate(records)
        if rec["match_by"] == "none"
    ]
    return {
        "ok": True,
        "rows": records,
        "total": len(records),
        "duplicates": sum(1 for r in records if r["duplicate"]),
        "unmatched_cn": len(pending),
        "pending": pending,
        "pending_count": len(pending),
        "groups": group_watched(records),
        "match": {
            "matched": match_result["matched"],
            "by_builtin": match_result["by_builtin"],
            "by_online": match_result["by_online"],
            "online_available": match_result["online_available"],
            "online_error": match_result["online_error"],
        },
    }


def commit(records: list[dict], source: str = "manual", skip_unmatched: bool = False) -> dict:
    """入库；界面里手改过的中文名会被记住，下次导入自动命中。

    ``skip_unmatched=True`` 时未匹配中文名的行**不入库**（用户在预览里明确选择
    「只导入匹配上的」）；默认保留，但标记 ``status='pending_review'``，
    界面上显示「待确认」徽章，用户可事后改中文名或删除——
    而不是像旧版那样让外文原名静默混进看过的列表冒充正常记录。
    判重键不变，所以重复导入仍然幂等，不会产生重复行。
    """
    db.init_db()
    metadata.ensure_builtin_loaded()
    inserted = skipped = learned = pending = 0
    for rec in records:
        # 防旧版前端 / 旧缓存把脏串再次塞进库：先剥噪音外壳，再按干净标题判重。
        raw_title = rec.get("title") or ""
        guard = clean_watched_title(raw_title)
        if guard and guard != raw_title:
            rec = dict(rec)
            rec["title"] = guard
            if (rec.get("title_cn") or raw_title).strip() == raw_title:
                rec["title_cn"] = guard
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
        status = "watched"
        if match_by == "none":
            if skip_unmatched:
                skipped += 1
                continue
            status = "pending_review"
            pending += 1
        db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, match_by, season_no, "
            "episode_from, episode_to, source, note, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.get("raw_text"), title, title_cn, match_by,
                rec.get("season_no"), rec.get("episode_from"), rec.get("episode_to"), source,
                rec.get("note") or "", status,
            ),
        )
        inserted += 1
    return {"ok": True, "inserted": inserted, "skipped": skipped,
            "learned": learned, "pending": pending}


def clean_watched_rows(dry_run: bool = False) -> dict:
    """幂等清洗看过的列表里的存量脏标题，并删除「- 完」之类垃圾行。

    只改 ``title / title_cn / match_by``；``raw_text``、季、集数范围、``note``
    一律保留（旧版这几个字段恰好是对的，重洗会丢语义）。用户手改过的中文名
    （``match_by='user'``）不会被覆盖。``dry_run=True`` 只统计不写库。
    """
    db.init_db()
    rows = db.rows("SELECT id, title, title_cn, match_by FROM watched_list")
    updated = deleted = unchanged = 0
    samples: list[dict] = []
    for item in rows:
        original = str(item.get("title") or "")
        clean = clean_watched_title(original)
        rid = int(item["id"])
        if not clean or _JUNK_TITLE_RE.match(clean):
            if not dry_run:
                db.execute("DELETE FROM watched_list WHERE id = ?", (rid,))
            deleted += 1
            if len(samples) < 12:
                samples.append({"action": "delete", "before": original})
            continue
        if clean == original:
            unchanged += 1
            continue
        title_cn = item.get("title_cn")
        match_by = item.get("match_by") or "none"
        if match_by == "user" or (title_cn and title_cn != original):
            new_cn = title_cn  # 手改 / 词典匹配出来的中文名优先保留
        else:
            new_cn = clean
            match_by = "native" if match_by in ("none", "native") else match_by
        if not dry_run:
            db.execute(
                "UPDATE watched_list SET title = ?, title_cn = ?, match_by = ? WHERE id = ?",
                (clean, new_cn, match_by, rid),
            )
        updated += 1
        if len(samples) < 12:
            samples.append({"action": "update", "before": original, "after": clean})
    return {
        "ok": True, "dry_run": bool(dry_run), "scanned": len(rows),
        "updated": updated, "deleted": deleted, "unchanged": unchanged,
        "samples": samples,
    }


def _merge_playback_duplicates() -> int:
    """合并旧版「每集一条」的 playback 记录，返回删除的重复行数（幂等）。

    旧版同步对**每个已看完文件**插一行，一部 12 集番会在看过的列表里刷出
    12 条同作品记录。这里只处理 ``source='playback'``：按 (作品名, 季) 分组，
    组内保留一条（``match_by='user'`` 优先，其次最早写入），区间取并集，
    其余行删除。手动导入行（source != 'playback'）一律不动。
    """
    rows = db.rows(
        "SELECT id, title, season_no, episode_from, episode_to, match_by "
        "FROM watched_list WHERE source = 'playback' "
        "ORDER BY (match_by = 'user') DESC, id ASC"
    )
    groups: dict[tuple[str, int], list[dict]] = {}
    for r in rows:
        key = (str(r.get("title") or "").strip().lower(), int(r.get("season_no") or 0))
        groups.setdefault(key, []).append(r)
    removed = 0
    for items in groups.values():
        if len(items) < 2:
            continue
        keep = items[0]
        starts = [int(i["episode_from"]) for i in items if i.get("episode_from") is not None]
        ends = [int(i["episode_to"]) for i in items if i.get("episode_to") is not None]
        db.execute(
            "UPDATE watched_list SET episode_from = ?, episode_to = ? WHERE id = ?",
            (min(starts) if starts else None, max(ends) if ends else None, keep["id"]),
        )
        for extra in items[1:]:
            db.execute("DELETE FROM watched_list WHERE id = ?", (extra["id"],))
            removed += 1
    return removed


def sync_watched_from_progress() -> dict:
    """手动把「已看完」的播放记录回补进看过的列表；**按作品合并**，幂等。

    播放记录（``watch_progress`` / ``watch_sessions``）与看过的列表原本是两条
    互不相交的数据流，这个函数是它们之间唯一的桥。只在用户点按钮时执行一次，
    **不是定时任务、不随启动运行**。

    2026-11-15 修复：旧版对每个文件插一条 ``episode_from=ep, episode_to=ep``，
    而 ``commit()`` 的判重键含集号，所以整部番会变成「每集一条」。现在先按
    (作品名, 季) 聚合成一条区间行，再与既有行取并集；同时把历史遗留的
    ``source='playback'`` 重复行合并掉，所以第一次点就能把旧重复清干净。
    """
    db.init_db()
    metadata.ensure_builtin_loaded()
    merged = _merge_playback_duplicates()
    completed = db.rows(
        "SELECT f.title_guess, f.title_raw, f.title_cn, f.filename, "
        "f.season_no, f.episode_no "
        "FROM watch_progress p JOIN media_files f ON f.id = p.file_id "
        "WHERE p.completed = 1"
    )
    # 第一遍：按 (作品, 季) 聚合，得到本批的最小/最大集号
    buckets: dict[tuple[str, int], dict] = {}
    skipped = 0
    for item in completed:
        base = (item.get("title_cn") or item.get("title_guess")
                or item.get("title_raw") or item.get("filename") or "")
        title = clean_watched_title(base)
        if not title or _JUNK_TITLE_RE.match(title):
            skipped += 1
            continue
        season = int(item.get("season_no") or 0)
        ep = item.get("episode_no")
        cn = (item.get("title_cn") or "").strip()
        key = (title.lower(), season)
        bucket = buckets.get(key)
        if bucket is None:
            buckets[key] = {
                "title": title, "cn": cn or title, "has_cn": bool(cn),
                "season": season, "ep_min": ep, "ep_max": ep,
            }
            continue
        if cn and not bucket["has_cn"]:
            bucket["cn"], bucket["has_cn"] = cn, True
        if ep is not None:
            bucket["ep_min"] = ep if bucket["ep_min"] is None else min(bucket["ep_min"], ep)
            bucket["ep_max"] = ep if bucket["ep_max"] is None else max(bucket["ep_max"], ep)

    # 第二遍：与既有行合并区间（整部行覆盖一切；部分重叠则扩展，不新增）
    added = updated = covered = 0
    for bucket in buckets.values():
        title, cn, season = bucket["title"], bucket["cn"], bucket["season"]
        lo, hi = bucket["ep_min"], bucket["ep_max"]
        existing = db.rows(
            "SELECT id, episode_from, episode_to FROM watched_list "
            "WHERE (lower(title) = lower(?) OR lower(COALESCE(title_cn,'')) = lower(?)) "
            "AND COALESCE(season_no, 0) = ? ORDER BY id ASC",
            (title, cn, season),
        )
        if existing:
            target = existing[0]
            old_lo, old_hi = target.get("episode_from"), target.get("episode_to")
            if old_lo is None and old_hi is None:      # 整部行已覆盖
                covered += 1
                continue
            new_lo = lo if old_lo is None else (old_lo if lo is None else min(int(old_lo), int(lo)))
            new_hi = hi if old_hi is None else (old_hi if hi is None else max(int(old_hi), int(hi)))
            if new_lo == old_lo and new_hi == old_hi:
                covered += 1
                continue
            db.execute(
                "UPDATE watched_list SET episode_from = ?, episode_to = ? WHERE id = ?",
                (new_lo, new_hi, target["id"]),
            )
            updated += 1
            continue
        db.execute(
            "INSERT INTO watched_list(raw_text, title, title_cn, match_by, season_no, "
            "episode_from, episode_to, source, note) VALUES (NULL, ?, ?, ?, ?, ?, ?, 'playback', '')",
            (title, cn, "native" if bucket["has_cn"] else "none",
             season or None, lo, hi),
        )
        added += 1
    return {
        "ok": True, "scanned": len(completed), "added": added,
        "updated": updated, "merged": merged, "skipped": skipped + covered,
    }
