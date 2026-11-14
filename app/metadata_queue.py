"""中文元数据补全队列：软件与 WorkBuddy 联网检索能力之间的对接层。

软件是独立进程，运行时无法直接调用对话中的联网检索，因此采用文件队列：
    1. 软件把识别不出中文名的标题导出到 data/pending_titles.json
    2. WorkBuddy 定时自动化任务读取该文件，联网检索中文名 / 原名 / 季度 / 封面，
       把结果写入 data/alias_import.json
    3. 软件启动与每次扫描后自动 import_aliases()，回写 name_alias 与 subjects 表
这样即使本机直连 Bangumi/TMDB 超时，外文名照样能自动补成中文，且全程无人值守。
"""
from __future__ import annotations

import json
from datetime import datetime

from . import config, db

FORMAT_VERSION = 1


def collect_pending(limit: int = 300) -> list[dict]:
    db.init_db()
    items: list[dict] = []
    seen: set[str] = set()

    sources = [
        db.rows(
            "SELECT DISTINCT COALESCE(title_guess, title_raw) AS t FROM media_files "
            "WHERE subject_id IS NULL AND COALESCE(title_guess, title_raw) IS NOT NULL"
        ),
        db.rows("SELECT DISTINCT title AS t FROM watched_list WHERE title_cn = title"),
    ]
    for group in sources:
        for r in group:
            title = (r.get("t") or "").strip()
            if not title or title.lower() in seen:
                continue
            if any("\u4e00" <= ch <= "\u9fff" for ch in title):
                continue
            known = db.row("SELECT title_cn FROM name_alias WHERE lower(raw_title) = lower(?)", (title,))
            if known:
                continue
            seen.add(title.lower())
            items.append({"raw_title": title, "status": "pending"})
            if len(items) >= limit:
                return items
    return items


def export_queue() -> dict:
    config.ensure_dirs()
    items = collect_pending()
    payload = {
        "version": FORMAT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "instruction": "请联网检索每个 raw_title 对应的中文名，按 alias_import 格式回写到 data/alias_import.json",
        "count": len(items),
        "items": items,
    }
    config.PENDING_TITLES_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"ok": True, "count": len(items), "path": str(config.PENDING_TITLES_FILE)}


def import_aliases() -> dict:
    """导入 WorkBuddy 补全结果。兼容 {items:[...]} 与直接数组两种格式。"""
    path = config.ALIAS_IMPORT_FILE
    if not path.exists():
        return {"ok": True, "imported": 0, "reason": "暂无补全文件"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "error": f"补全文件解析失败：{exc}"}

    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {"ok": False, "error": "补全文件格式不正确"}

    db.init_db()
    imported = skipped = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = (item.get("raw_title") or "").strip()
        cn = (item.get("title_cn") or "").strip()
        if not raw or not cn:
            skipped += 1
            continue
        db.execute(
            "INSERT INTO name_alias(raw_title, title_cn, title_orig, source, verified) "
            "VALUES (?, ?, ?, ?, 1) ON CONFLICT(raw_title) DO UPDATE SET "
            "title_cn = excluded.title_cn, title_orig = excluded.title_orig, "
            "source = excluded.source, verified = 1",
            (raw, cn, item.get("title_orig"), item.get("source") or "workbuddy"),
        )
        if item.get("bgm_id"):
            db.execute(
                "INSERT INTO subjects(bgm_id, title_cn, title_orig, season_no, cover_url, source, confidence, verified) "
                "VALUES (?, ?, ?, ?, ?, 'workbuddy', 0.9, 1)",
                (item["bgm_id"], cn, item.get("title_orig"), item.get("season_no"), item.get("cover_url")),
            )
        db.execute(
            "UPDATE media_files SET title_guess = ? WHERE COALESCE(title_guess, title_raw) = ?",
            (cn, raw),
        )
        db.execute("UPDATE watched_list SET title_cn = ? WHERE title = ?", (cn, raw))
        imported += 1
    return {"ok": True, "imported": imported, "skipped": skipped}


def queue_status() -> dict:
    return {
        "pending": len(collect_pending()),
        "queue_file": str(config.PENDING_TITLES_FILE),
        "queue_exists": config.PENDING_TITLES_FILE.exists(),
        "import_file": str(config.ALIAS_IMPORT_FILE),
        "import_exists": config.ALIAS_IMPORT_FILE.exists(),
    }
