"""追剧列表：把「已下载 / 未下载」清单导入、保持顺序、人工调整与本地文件匹配。

设计约束：
- 用户书写顺序即默认顺序（``sort_order``），可上移 / 下移，改状态不会重排。
- 「未下载 → 已下载」只改 ``downloaded`` 标记，绝不复制或删除条目。
- 与 ``media_files`` / ``watch_progress`` **没有外键**：删追剧条目不影响播放记录。
- 本地匹配走 ``textmatch.query_variants()`` 的短关键词，不要求文件名与清单完全一致。
"""
from __future__ import annotations

import re

from . import blacklist, db, textmatch

_SECTION_RE = re.compile(r"^\s*(已下载|未下载)\s*[:：]\s*$")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
MAX_FILES_PER_ITEM = 8          # 界面「固定展示」的条数；match_count 仍是全量


def clean_line(line: str) -> str:
    """清掉零宽字符、行首编号与首尾标点，保留标题主体。"""
    return textmatch.clean_title(line)


def parse_sections(text: str) -> list[dict]:
    """按「已下载：」「未下载：」分段，返回按书写顺序排列的条目。

    每段内按归一化标题去重（同一段重复写两次只留第一条），跨段不去重：
    同一部作品可以同时出现在两份清单里（例如想补下更高清版本）。
    """
    section: int | None = None
    seen: set[tuple[int, str]] = set()
    out: list[dict] = []
    for raw in str(text or "").splitlines():
        line = raw.replace("\xa0", " ")
        head = _SECTION_RE.match(line)
        if head:
            section = 1 if head.group(1) == "已下载" else 0
            continue
        if section is None:
            continue
        title = clean_line(line)
        if not title:
            continue
        key = (section, textmatch.normalize(title))
        if not key[1] or key in seen:
            continue
        seen.add(key)
        out.append({"title": title, "downloaded": section})
    return out


def preview(text: str) -> dict:
    rows = parse_sections(text)
    return {
        "ok": True,
        "total": len(rows),
        "downloaded": sum(1 for r in rows if r["downloaded"]),
        "pending": sum(1 for r in rows if not r["downloaded"]),
        "sample": [r["title"] for r in rows[:5]],
    }


def import_text(text: str, replace: bool = False) -> dict:
    """把清单写库；默认幂等追加（已存在的「分段+标题」跳过）。

    ``replace=True`` 仅在界面二次确认后使用：先清空追剧表再按新顺序重建。
    """
    rows = parse_sections(text)
    if not rows:
        return {"ok": False, "error": "没解析到条目；请确认文本里有「已下载：」和「未下载：」两行"}
    db.init_db()
    if replace:
        db.execute("DELETE FROM watchlist")
        existing: set[tuple[int, str]] = set()
        order = 0
    else:
        existing = {
            (int(r["downloaded"]), textmatch.normalize(r["title"]))
            for r in db.rows("SELECT title, downloaded FROM watchlist")
        }
        order = int(db.scalar("SELECT COALESCE(MAX(sort_order), 0) FROM watchlist") or 0)
    now = db.now()
    added = skipped = 0
    for item in rows:
        key = (item["downloaded"], textmatch.normalize(item["title"]))
        if key in existing:
            skipped += 1
            continue
        existing.add(key)
        order += 1
        db.execute(
            "INSERT INTO watchlist(title, downloaded, sort_order, source, created_at, updated_at) "
            "VALUES (?, ?, ?, 'manual', ?, ?)",
            (item["title"], item["downloaded"], order, now, now),
        )
        added += 1
    return {
        "ok": True, "added": added, "skipped": skipped,
        "total": int(db.scalar("SELECT COUNT(*) FROM watchlist") or 0),
        "downloaded": int(db.scalar("SELECT COUNT(*) FROM watchlist WHERE downloaded=1") or 0),
        "pending": int(db.scalar("SELECT COUNT(*) FROM watchlist WHERE downloaded=0") or 0),
    }


def _media_haystacks() -> list[dict]:
    """一次读全库，构建归一化检索串；避免每个标题都发一轮 SQL。

    黑名单里的视频不参与匹配：追剧清单条目本身保留（那是用户手写的待办），
    但「本地 N 个文件」不应把被隐藏的视频算进去。
    """
    clause, params = blacklist.sql_clause("f")
    sql = (
        "SELECT f.id, f.filename, f.path, f.title_raw, f.title_guess, f.title_cn, "
        "f.season_no, f.episode_no, "
        "COALESCE(f.title_cn, f.title_guess, f.filename) AS display_title, "
        "COALESCE(p.completed, 0) AS completed "
        "FROM media_files f LEFT JOIN watch_progress p ON p.file_id = f.id "
        "WHERE COALESCE(f.deleted_at, '') = ''"
    )
    if clause:
        sql += " AND " + clause
    rows = db.rows(sql, tuple(params))
    for r in rows:
        titles = " ".join(str(r.get(k) or "") for k in ("filename", "title_raw", "title_guess", "title_cn"))
        r["_title_hay"] = textmatch.normalize(titles)
        r["_full_hay"] = textmatch.normalize(titles + " " + str(r.get("path") or ""))
        # 短英文词（Zero / Life / Time）用「独立单词」匹配，避免子串命中一大片无关文件
        r["_tokens"] = {w.lower() for w in re.findall(r"[A-Za-z0-9]{3,}", titles)}
    return rows


def media_index() -> list[dict]:
    """给其它模块复用的媒体检索索引（追剧列表 / 看过的列表共用同一套归一化）。"""
    return _media_haystacks()


def match_files(title: str, media: list[dict]) -> tuple[int, list[dict], str]:
    """按关键词优先级逐个尝试，**第一个命中的关键词即采用**。

    早期把全部关键词 OR 在一起，结果「Zero」「Life」这类短英文词子串匹配到
    上百个无关文件。现在优先用最具体的主标题，短英文词只认文件名里的独立单词。
    """
    hits: list[dict] = []
    keyword = ""
    for variant in textmatch.query_variants(title, limit=6):
        key = textmatch.normalize(variant)
        if len(key) < 2:
            continue
        latin = not _CJK_RE.search(variant)
        weak_latin = latin and len(key) < 7
        found = []
        for row in media:
            if weak_latin:
                ok = key in row["_tokens"]
            elif len(key) >= 3:
                ok = key in row["_full_hay"]
            else:
                ok = key in row["_title_hay"]   # 两字中文词只认标题字段，防目录名误命中
            if ok:
                found.append(row)
        if found:
            hits, keyword = found, variant
            break
    if not hits:
        return 0, [], ""
    hits.sort(key=lambda r: (int(r.get("season_no") or 0), int(r.get("episode_no") or 0),
                             str(r.get("filename") or "")))
    shown = [
        {k: r.get(k) for k in ("id", "filename", "path", "display_title", "episode_no", "completed")}
        for r in hits[:MAX_FILES_PER_ITEM]
    ]
    return len(hits), shown, keyword


def items(keyword: str = "") -> dict:
    """追剧列表全量（含每行的本地匹配结果），按 sort_order 排序。"""
    db.init_db()
    rows = db.rows("SELECT id, title, downloaded, sort_order, note, source, created_at "
                   "FROM watchlist ORDER BY sort_order ASC, id ASC")
    media = _media_haystacks()
    kw = textmatch.normalize(keyword)
    out = []
    for r in rows:
        if kw and kw not in textmatch.normalize(r["title"]):
            continue
        count, files, search_keyword = match_files(r["title"], media)
        out.append({
            **r,
            "downloaded": int(r["downloaded"] or 0),
            "match_count": count,
            "files": files,
            "search_keyword": search_keyword,
        })
    total_d = sum(1 for r in rows if int(r["downloaded"] or 0) == 1)
    return {
        "ok": True, "items": out,
        "total": len(rows), "downloaded": total_d, "pending": len(rows) - total_d,
        "matched": sum(1 for r in out if r["match_count"]),
    }


def set_downloaded(item_id, downloaded: bool) -> dict:
    flag = 1 if downloaded in (True, 1, "1", "true", "on") else 0
    row = db.row("SELECT id FROM watchlist WHERE id = ?", (item_id,))
    if not row:
        return {"ok": False, "error": "条目不存在"}
    db.execute("UPDATE watchlist SET downloaded = ?, updated_at = ? WHERE id = ?",
               (flag, db.now(), row["id"]))
    return {"ok": True, "downloaded": flag}


def move(item_id, delta: int) -> dict:
    """与同栏相邻条目交换 sort_order；到头时静默返回未移动。"""
    row = db.row("SELECT id, downloaded, sort_order FROM watchlist WHERE id = ?", (item_id,))
    if not row:
        return {"ok": False, "error": "条目不存在"}
    step = 1 if int(delta) > 0 else -1
    sql = ("SELECT id, sort_order FROM watchlist WHERE downloaded = ? AND id <> ? "
           "AND sort_order " + ("> ?" if step > 0 else "< ?") +
           " ORDER BY sort_order " + ("ASC" if step > 0 else "DESC") + " LIMIT 1")
    other = db.row(sql, (row["downloaded"], row["id"], row["sort_order"]))
    if not other:
        return {"ok": True, "moved": False}
    now = db.now()
    db.execute("UPDATE watchlist SET sort_order = ?, updated_at = ? WHERE id = ?",
               (other["sort_order"], now, row["id"]))
    db.execute("UPDATE watchlist SET sort_order = ?, updated_at = ? WHERE id = ?",
               (row["sort_order"], now, other["id"]))
    return {"ok": True, "moved": True}


def move_top(item_id) -> dict:
    """置顶：把该条同栏 sort_order 改成「同栏最小 - 1」，一下移到最上面。

    不重排全表、不改其它行，也不跨栏移动（「未下载 / 已下载」各自排序）。
    反复置顶只会让数值继续变小，整数范围完全够用，且 ``ORDER BY sort_order``
    的语义始终正确。
    """
    row = db.row("SELECT id, downloaded, sort_order FROM watchlist WHERE id = ?", (item_id,))
    if not row:
        return {"ok": False, "error": "条目不存在"}
    top = db.scalar(
        "SELECT MIN(sort_order) FROM watchlist WHERE downloaded = ?", (row["downloaded"],)
    )
    if top is None or int(row["sort_order"]) <= int(top):
        return {"ok": True, "moved": False, "already_top": True}
    db.execute(
        "UPDATE watchlist SET sort_order = ?, updated_at = ? WHERE id = ?",
        (int(top) - 1, db.now(), row["id"]),
    )
    return {"ok": True, "moved": True}


def add(title: str, downloaded: bool = False) -> dict:
    text = clean_line(title)
    if not text:
        return {"ok": False, "error": "标题不能为空"}
    db.init_db()
    order = int(db.scalar("SELECT COALESCE(MAX(sort_order), 0) FROM watchlist") or 0) + 1
    flag = 1 if downloaded in (True, 1, "1", "true", "on") else 0
    now = db.now()
    db.execute("INSERT INTO watchlist(title, downloaded, sort_order, source, created_at, updated_at) "
               "VALUES (?, ?, ?, 'manual', ?, ?)", (text, flag, order, now, now))
    return {"ok": True, "order": order}


def rename(item_id, title: str) -> dict:
    text = clean_line(title)
    if not text:
        return {"ok": False, "error": "标题不能为空"}
    row = db.row("SELECT id FROM watchlist WHERE id = ?", (item_id,))
    if not row:
        return {"ok": False, "error": "条目不存在"}
    db.execute("UPDATE watchlist SET title = ?, updated_at = ? WHERE id = ?", (text, db.now(), row["id"]))
    return {"ok": True, "title": text}


def remove(item_id) -> dict:
    row = db.row("SELECT id FROM watchlist WHERE id = ?", (item_id,))
    if not row:
        return {"ok": False, "error": "条目不存在"}
    db.execute("DELETE FROM watchlist WHERE id = ?", (row["id"],))
    return {"ok": True}
