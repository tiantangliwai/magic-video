"""「看过的动漫列表」导入：解析、规范化、中文名匹配、去重、入库。

支持粘贴文本与 CSV，例如：
    进击的巨人
    更衣人偶坠入爱河 S2 01-12
    葬送的芙莉莲,1,1,28
    # 这是注释
"""
from __future__ import annotations

import csv
import io
import re

from . import db, metadata

SEASON_RES = [
    re.compile(r"第\s*(\d{1,2})\s*季", re.IGNORECASE),
    re.compile(r"[Ss](?:eason)?\s*(\d{1,2})\b", re.IGNORECASE),
    re.compile(r"\b(\d{1,2})\s*季"),
]
RANGE_RE = re.compile(r"(\d{1,3})\s*(?:[-~～—至]|到)\s*(\d{1,3})\s*(?:话|集|話)?")
EPISODE_RE = re.compile(r"第?\s*(\d{1,3})\s*(?:话|集|話|ep)?\b", re.IGNORECASE)
YEAR_RE = re.compile(r"[（(]\s*(19\d{2}|20\d{2})\s*[)）]|\b(19\d{2}|20\d{2})\b")


def normalize_title(text: str) -> str:
    title = text.strip()
    title = re.sub(r"[（(]\s*(19|20)\d{2}\s*[)）]", " ", title)
    title = re.sub(r"\s+", " ", title)
    return title.strip(" ,，、\t")


def lookup_chinese(title: str) -> str | None:
    """兼容封装：查内置 + 本地已学词典（自动匹配统一由 metadata 模块驱动）。"""
    hit = metadata.lookup_local(title)
    return hit["title_cn"] if hit else None


def parse_line(line: str) -> dict | None:
    raw = line.strip()
    if not raw or raw.startswith("#") or raw.startswith("//"):
        return None

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
            return _make_record(raw, parts[0], season, ep_from, ep_to)

    season = None
    for pattern in SEASON_RES:
        m = pattern.search(raw)
        if m:
            season = int(m.group(1))
            raw = raw[: m.start()] + " " + raw[m.end():]
            break

    # 年份要先去掉，否则 "2099" 这类数字会被集数正则误切
    year_match = YEAR_RE.search(raw)
    if year_match:
        raw = raw[: year_match.start()] + " " + raw[year_match.end():]

    ep_from = ep_to = None
    m = RANGE_RE.search(raw)
    if m:
        ep_from, ep_to = int(m.group(1)), int(m.group(2))
        raw = raw[: m.start()] + " " + raw[m.end():]
    else:
        m = EPISODE_RE.search(raw)
        if m:
            ep_from = ep_to = int(m.group(1))
            raw = raw[: m.start()] + " " + raw[m.end():]

    title = normalize_title(raw)
    if not title:
        return None
    return _make_record(line.strip(), title, season, ep_from, ep_to)


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
    }


def _key(rec: dict) -> tuple:
    return (
        rec["title"].lower(),
        rec.get("season_no") or 0,
        rec.get("episode_from") or 0,
        rec.get("episode_to") or 0,
    )


def parse_text(text: str) -> list[dict]:
    results, seen = [], set()
    for line in text.splitlines():
        record = parse_line(line)
        if not record:
            continue
        key = _key(record)
        if key in seen:
            continue
        seen.add(key)
        results.append(record)
    return results


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
            "episode_from, episode_to, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rec.get("raw_text"), title, title_cn, match_by,
                rec.get("season_no"), rec.get("episode_from"), rec.get("episode_to"), source,
            ),
        )
        inserted += 1
    return {"ok": True, "inserted": inserted, "skipped": skipped, "learned": learned}
