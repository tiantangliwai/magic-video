"""外部清单导入：MAL XML / Bangumi Takeout JSON → 统一记录 → 差异报告 → 合并。

产品定位是「输入清单 → 自动对齐本地文件与历史」。三条入口（粘贴纯文本、
MyAnimeList 的 ``animelist.xml``、Bangumi 的 Takeout JSON）字段结构完全不同，
这里统一成 ``importer`` 认识的 record，再交给 ``importer.preview_records`` /
``importer.commit`` ——**只有一个解析出口、一套判重与写入逻辑**，不在导入层各写一份。

合并口径（与项目其余部分保持一致）：
- 进度**只增不减**（max 语义）：清单里的进度低于本地时一律忽略，绝不降级；
- 中文名：本地为空才用清单的补；``match_by='user'`` 的人工修正永不被覆盖；
- 文件对齐必须「文件命中 + 标题校验」双判定，禁止把关键词命中直接当结论
  （``watchlist.match_files()`` 的 count 只是候选数，不能当「本地已有这些集」）。
"""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from . import db, importer, textmatch

FORMATS = ("text", "mal_xml", "bangumi_json")
FORMAT_LABELS = {
    "text": "纯文本清单",
    "mal_xml": "MyAnimeList XML",
    "bangumi_json": "Bangumi Takeout JSON",
}

# MAL 的观看状态 → 中文备注（只写 note，不参与判重）
MAL_STATUS = {
    "watching": "在看",
    "completed": "看完",
    "on-hold": "搁置",
    "dropped": "抛弃",
    "plan to watch": "想看",
}
# Bangumi Takeout 的 status 数字 → 中文
BGM_STATUS = {1: "想看", 2: "在看", 3: "看过", 4: "搁置", 5: "抛弃"}

# 清单文件上限：误选一个几十 MB 的视频/备份文件时不要卡住界面
MAX_FILE_BYTES = 20 * 1024 * 1024


def detect_format(filename: str = "", text: str = "") -> str:
    """按扩展名 + 内容特征判定格式。宁可退回 text，也不乱解析。"""
    name = (filename or "").lower()
    body = (text or "").lstrip()
    if name.endswith(".xml") or body.startswith("<?xml") or body.startswith("<myanimelist"):
        return "mal_xml"
    if name.endswith(".json") or body.startswith("{") or body.startswith("["):
        return "bangumi_json"
    return "text"


def _to_int(value) -> int:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _record(title: str, *, title_cn: str = "", season=None, ep_from=None, ep_to=None,
            note: str = "", external_id=None, ext_status: str = "",
            ext_progress=None, ext_total=None, ext_score=None,
            fmt: str = "text", raw_text: str = "") -> dict:
    """统一记录结构：与 ``importer._make_record`` 完全一致，另加外部清单字段。

    外部字段只用于差异报告与展示，**不进判重键**——判重仍然只看
    (标题, 季, 起始集, 结束集)，保证三条入口的幂等行为一致。
    """
    rec = importer._make_record(raw_text or title, title, season, ep_from, ep_to)
    if title_cn and title_cn != rec["title"]:
        rec["title_cn"] = title_cn
    rec["note"] = note
    rec["external_id"] = external_id
    rec["ext_status"] = ext_status
    rec["ext_progress"] = ext_progress
    rec["ext_total"] = ext_total
    rec["ext_score"] = ext_score
    rec["fmt"] = fmt
    return rec


def parse_mal_xml(text: str) -> dict:
    """解析 MyAnimeList 导出的 ``animelist.xml``。

    只取标题与「已看集数」；用户评分/状态写进备注（本软件不维护评分体系，
    造一套没人看的字段没有意义）。
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return {"ok": False, "records": [], "warnings": [f"XML 解析失败：{exc}"]}

    nodes = root.findall(".//anime")
    if not nodes:
        return {"ok": False, "records": [],
                "warnings": ["没有找到 <anime> 条目，请确认是 MAL 导出的 animelist.xml"]}

    records: list[dict] = []
    warnings: list[str] = []
    for node in nodes:
        def text_of(tag: str) -> str:
            el = node.find(tag)
            return (el.text or "").strip() if el is not None and el.text else ""

        title = text_of("series_title")
        if not title:
            warnings.append("跳过一条没有 series_title 的条目")
            continue
        watched = _to_int(text_of("my_watched_episodes"))
        total = _to_int(text_of("series_episodes"))
        status = MAL_STATUS.get(text_of("my_status").lower(), text_of("my_status"))
        notes = [status] if status else []
        if total:
            notes.append(f"共 {total} 集")
        records.append(_record(
            title,
            season=None,
            ep_from=1 if watched > 0 else None,
            ep_to=watched if watched > 0 else None,
            note=" · ".join(notes),
            external_id=_to_int(text_of("series_animedb_id")) or None,
            ext_status=status,
            ext_progress=watched,
            ext_total=total,
            ext_score=_to_int(text_of("my_score")) or None,
            fmt="mal_xml",
            raw_text=title,
        ))
    return {"ok": True, "records": records, "warnings": warnings}


def _load_json_items(text: str):
    """把「整份 JSON 数组 / 单对象 / JSONL」统一成条目列表。"""
    body = (text or "").strip()
    if not body:
        return None
    data = None
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "items", "list", "collections", "anime"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    # Bangumi Takeout 实际是 JSONL（每行一个对象），也可能是带逗号的数组片段
    items = []
    for line in body.splitlines():
        line = line.strip().rstrip(",")
        if not line or line in ("[", "]"):
            continue
        try:
            items.append(json.loads(line))
        except ValueError:
            continue
    return items or None


def parse_bangumi_json(text: str) -> dict:
    """解析 Bangumi 导出的收藏数据（数组 / 单对象 / JSONL 都支持）。"""
    items = _load_json_items(text)
    if items is None:
        return {"ok": False, "records": [],
                "warnings": ["JSON 解析失败：既不是数组，也不是每行一个对象的 JSONL"]}

    records: list[dict] = []
    warnings: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        subject = item.get("subject") if isinstance(item.get("subject"), dict) else item
        title = str(subject.get("name") or "").strip()
        if not title:
            warnings.append("跳过一条没有作品名的条目")
            continue
        cn = str(subject.get("name_cn") or "").strip()
        total = _to_int(subject.get("eps"))
        progress = _to_int(item.get("ep_status"))
        status = BGM_STATUS.get(_to_int(item.get("status")), "")
        notes = [status] if status else []
        if total:
            notes.append(f"共 {total} 集")
        records.append(_record(
            title,
            title_cn=cn,
            season=None,
            ep_from=1 if progress > 0 else None,
            ep_to=progress if progress > 0 else None,
            note=" · ".join(notes),
            external_id=_to_int(subject.get("id")) or None,
            ext_status=status,
            ext_progress=progress,
            ext_total=total,
            ext_score=_to_int(item.get("rating")) or None,
            fmt="bangumi_json",
            raw_text=title,
        ))
    return {"ok": True, "records": records, "warnings": warnings}


def parse_any(filename: str = "", text: str = "") -> dict:
    """按格式解析。``text`` 纯文本走原有的 ``importer.parse_text``，行为不变。"""
    fmt = detect_format(filename, text)
    if fmt == "mal_xml":
        result = parse_mal_xml(text)
    elif fmt == "bangumi_json":
        result = parse_bangumi_json(text)
    else:
        result = {"ok": True, "records": importer.parse_text(text), "warnings": []}
    result["format"] = fmt
    return result


def read_file(path: str) -> dict:
    """读取用户指定的清单文件（限制 20MB，避免误选巨大文件卡住界面）。"""
    target = Path(str(path or "").strip().strip('"')).expanduser()
    if not target.exists() or not target.is_file():
        return {"ok": False, "error": f"文件不存在：{target}"}
    try:
        size = target.stat().st_size
        if size > MAX_FILE_BYTES:
            return {"ok": False,
                    "error": f"文件太大（{size // 1024 // 1024} MB），请只导入清单本身"}
        return {
            "ok": True,
            "filename": target.name,
            "path": str(target),
            "format": detect_format(target.name, ""),
            "content": target.read_text(encoding="utf-8", errors="replace"),
        }
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _local_map() -> dict:
    """本地「看过的列表」按 (标题, 季) 聚合：进度取最大值，记住对应的行 id。"""
    out: dict[tuple, dict] = {}
    for r in db.rows(
            "SELECT id, title, title_cn, match_by, season_no, episode_from, episode_to "
            "FROM watched_list"):
        key = (str(r["title"]).lower(), r["season_no"] or 0)
        ep_to = int(r["episode_to"] or 0)
        item = out.get(key)
        if item is None:
            out[key] = {
                "row_id": r["id"], "title": r["title"], "title_cn": r["title_cn"] or "",
                "season_no": r["season_no"], "ep_to": ep_to,
                "user_edited": r["match_by"] == "user",
            }
            continue
        if ep_to > item["ep_to"]:
            item["row_id"] = r["id"]
            item["ep_to"] = ep_to
        if r["match_by"] == "user":
            item["user_edited"] = True
        if not item["title_cn"] and r["title_cn"]:
            item["title_cn"] = r["title_cn"]
    return out


def _incoming_map(records: list[dict]) -> dict:
    out: dict[tuple, dict] = {}
    for rec in records or []:
        key = (str(rec.get("title") or "").lower(), rec.get("season_no") or 0)
        ep_to = int(rec.get("episode_to") or 0)
        item = out.get(key)
        if item is None:
            out[key] = {
                "title": rec.get("title") or "", "title_cn": rec.get("title_cn") or "",
                "season_no": rec.get("season_no"), "ep_to": ep_to,
                "ext_status": rec.get("ext_status") or "", "ext_score": rec.get("ext_score"),
            }
        elif ep_to > item["ep_to"]:
            item["ep_to"] = ep_to
    return out


def _title_verifies(candidate: str, keyword: str) -> bool:
    """标题校验（双判定的第二道）：归一化后以关键词开头，或关键词占标题一半以上。"""
    a, k = textmatch.normalize(candidate), textmatch.normalize(keyword)
    if not a or not k:
        return False
    if a == k or a.startswith(k):
        return True
    return k in a and len(k) * 2 >= len(a)


def _align_files(title: str, media_rows: list[dict]) -> dict:
    """文件对齐双判定：先关键词命中，再校验标题确实是同一部作品。

    ``watchlist.match_files()`` 只做关键词子串匹配（「Zero」会命中一堆无关文件），
    它的 count 不能直接当「本地已有这些集」。这里两道都过才算命中。
    """
    variants = textmatch.query_variants(title, limit=6)
    if not variants:
        return {"files": 0, "keyword": "", "samples": []}
    keyword = variants[0]
    key = textmatch.normalize(keyword)
    if not key:
        return {"files": 0, "keyword": "", "samples": []}
    hits = []
    for row in media_rows:
        title_text = str(row.get("title_guess") or "")
        filename = str(row.get("filename") or "")
        hay = textmatch.normalize(f"{title_text} {filename}")
        if not hay or key not in hay:
            continue
        if not _title_verifies(title_text or filename, keyword):
            continue
        hits.append({
            "id": row.get("id"),
            "filename": filename,
            "episode_no": row.get("episode_no"),
            "display_title": row.get("title_cn") or title_text or filename,
        })
    hits.sort(key=lambda r: (int(r.get("episode_no") or 0), r["filename"]))
    return {"files": len(hits), "keyword": keyword, "samples": hits[:5]}


def _load_media() -> list[dict]:
    return db.rows(
        "SELECT id, filename, title_guess, title_cn, season_no, episode_no "
        "FROM media_files WHERE deleted_at IS NULL"
    )


def diff(records: list[dict]) -> dict:
    """导入清单 vs 本地库的差异报告。

    按「作品 + 季」聚合（与 ``watched_list`` 的存储口径一致），给出四类差异：
    只本地有 / 只清单有 / 进度不同 / 中文名冲突；再对「只清单有」的条目做
    双判定文件对齐，指出哪些作品本地其实已经有文件、只是没记录。
    """
    local = _local_map()
    incoming = _incoming_map(records)
    media_rows = _load_media()

    only_local: list[dict] = []
    only_imported: list[dict] = []
    progress_gap: list[dict] = []
    title_conflict: list[dict] = []
    file_ready: list[dict] = []

    for key, loc in local.items():
        if key not in incoming:
            only_local.append({
                "title": loc["title"], "title_cn": loc["title_cn"],
                "season_no": loc["season_no"], "local_to": loc["ep_to"],
            })

    for key, inc in incoming.items():
        loc = local.get(key)
        base = {
            "title": inc["title"], "title_cn": inc["title_cn"],
            "season_no": inc["season_no"], "incoming_to": inc["ep_to"],
            "local_to": int((loc or {}).get("ep_to") or 0),
            "status": inc.get("ext_status") or "",
            "score": inc.get("ext_score"),
        }
        if loc is None:
            align = _align_files(inc["title"], media_rows)
            base["files"] = align["files"]
            only_imported.append(base)
            if align["files"]:
                file_ready.append({**base, "keyword": align["keyword"],
                                   "samples": align["samples"]})
            continue
        if inc["ep_to"] > int(loc.get("ep_to") or 0):
            progress_gap.append({**base, "direction": "import_ahead"})
        elif int(loc.get("ep_to") or 0) > inc["ep_to"]:
            progress_gap.append({**base, "direction": "local_ahead"})
        local_cn = loc.get("title_cn") or ""
        inc_cn = inc.get("title_cn") or ""
        if local_cn and inc_cn and local_cn != inc_cn:
            title_conflict.append({
                **base, "local_cn": local_cn, "incoming_cn": inc_cn,
                # 本地优先：手改永不覆盖，导入只补空
                "kept": local_cn, "user_edited": bool(loc.get("user_edited")),
            })

    return {
        "ok": True,
        "summary": {
            "local": len(local),
            "incoming": len(incoming),
            "only_local": len(only_local),
            "only_imported": len(only_imported),
            "progress_gap": len(progress_gap),
            "title_conflict": len(title_conflict),
            "file_ready": len(file_ready),
        },
        "only_local": only_local,
        "only_imported": only_imported,
        "progress_gap": progress_gap,
        "title_conflict": title_conflict,
        "file_ready": file_ready,
    }


def preview_file(filename: str = "", content: str = "") -> dict:
    """解析清单文件并返回「预览 + 匹配结果 + 差异报告」。"""
    parsed = parse_any(filename, content)
    if not parsed.get("ok", True):
        return {"ok": False, "format": parsed.get("format"),
                "error": "；".join(parsed.get("warnings") or ["清单解析失败"])}
    records = parsed.get("records") or []
    if not records:
        return {"ok": False, "format": parsed.get("format"),
                "error": "清单里没有解析出任何条目"}
    result = importer.preview_records(records)
    result["format"] = parsed.get("format")
    result["format_label"] = FORMAT_LABELS.get(parsed.get("format") or "", "")
    result["warnings"] = parsed.get("warnings") or []
    result["diff"] = diff(records)
    return result


def commit(records: list[dict], *, skip_unmatched: bool = False,
           source: str = "listimport") -> dict:
    """按「只增不减 / 不降级 / 不覆盖手改」把清单合进看过的列表。

    - 本地已有该作品：只在清单进度更高时把 ``episode_to`` 抬高（max）；
      清单更旧时直接跳过，**绝不把本地进度改小**；
    - 本地没有：交给 ``importer.commit`` 插入（共享判重与待确认逻辑）；
    - 中文名：本地为空才补，``match_by='user'`` 的人工修正永不被覆盖。
    """
    db.init_db()
    local = _local_map()
    to_insert: list[dict] = []
    updated = skipped = 0
    stamp = db.now()

    for rec in records or []:
        key = (str(rec.get("title") or "").lower(), rec.get("season_no") or 0)
        loc = local.get(key)
        ep_to = int(rec.get("episode_to") or 0)
        if loc is None:
            to_insert.append(rec)
            continue
        if ep_to > int(loc.get("ep_to") or 0):
            db.execute(
                "UPDATE watched_list SET episode_to = ?, "
                "episode_from = COALESCE(episode_from, 1), imported_at = ? WHERE id = ?",
                (ep_to, stamp, loc["row_id"]),
            )
            loc["ep_to"] = ep_to
            updated += 1
        else:
            skipped += 1
        if (not loc.get("title_cn") and not loc.get("user_edited")
                and rec.get("title_cn") and rec["title_cn"] != rec.get("title")):
            db.execute("UPDATE watched_list SET title_cn = ? WHERE id = ?",
                       (rec["title_cn"], loc["row_id"]))
            loc["title_cn"] = rec["title_cn"]

    if to_insert:
        out = importer.commit(to_insert, source=source, skip_unmatched=skip_unmatched)
    else:
        out = {"ok": True, "inserted": 0, "skipped": 0, "learned": 0, "pending": 0}
    out["updated"] = updated
    out["skipped"] = int(out.get("skipped") or 0) + skipped
    return out
