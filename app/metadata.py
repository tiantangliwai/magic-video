"""导入时中文名自动匹配（无定时任务、无后台回填队列）。

匹配只在三个动作里发生：
    1. importer.preview()  —— 导入「看过的列表」
    2. scanner.scan()      —— 扫描目录、新文件入库
    3. rematch_all()       —— 用户在界面手动点「重新匹配」

链路：内置离线词典 → 本地已学词典 → 在线 Bangumi（可配代理、短超时、快速降级）。
命中结果当场写入 name_alias / subjects / watched_list / media_files，并立即返回给界面，
不依赖任何外部定时任务或后台进程。
"""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

from . import __version__, config, db

_LOCK = threading.RLock()
_INDEX: dict[str, dict] = {}
_INDEX_DIRTY = True
_BUILTIN_READY = False
_ONLINE_BLOCKED = False
_ONLINE_ERROR = ""
_CFG_CACHE: dict | None = None
_CFG_AT = 0.0

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
NOISE_RE = re.compile(
    r"\b(?:1080p|720p|2160p|4k|hevc|x264|x265|h264|h265|aac|flac|10bit|8bit|"
    r"web-?rip|web-?dl|bdrip|bluray|ass|简|繁|内嵌|外挂|v\d)\b",
    re.IGNORECASE,
)
NON_ALNUM_RE = re.compile(r"[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+", re.UNICODE)
SEASON_RE = re.compile(
    r"\bS(\d{1,2})\b|\bSeason\s*(\d{1,2})\b|第\s*(\d{1,2})\s*(?:季|期)|第\s*([一二三四五六七八九十])\s*(?:季|期)",
    re.IGNORECASE,
)
CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
             "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def normalize(title: str) -> str:
    """归一化标题：全半角、大小写、括号内容、年份、画质标签、标点。"""
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", str(title)).lower().strip()
    s = re.sub(r"[\[\(（【][^\]\)）】]*[\]\)）】]", " ", s)
    s = re.sub(r"\b(?:19|20)\d{2}\b", " ", s)
    s = NOISE_RE.sub(" ", s)
    s = re.sub(r"[_\.]+", " ", s)
    s = NON_ALNUM_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def strip_season(title: str) -> str:
    """去掉季数后缀，让 "Title S2" 与 "Title" 能命中同一部作品。"""
    if not title:
        return ""
    s = unicodedata.normalize("NFKC", str(title))
    s = SEASON_RE.sub(" ", s)
    s = re.sub(r"\b(?:2nd|3rd|1st)\s+season\b", " ", s, flags=re.IGNORECASE)
    return normalize(s)


def season_hint(text: str) -> int | None:
    m = SEASON_RE.search(unicodedata.normalize("NFKC", str(text or "")))
    if not m:
        return None
    for group in m.groups():
        if group:
            return CN_DIGITS.get(group, None) if not group.isdigit() else int(group)
    return None


def _entry(cn: str, orig: str = "", aliases=(), bgm=None, season=None, source="builtin") -> dict:
    return {
        "title_cn": cn, "title_orig": orig or "", "bgm_id": bgm,
        "season_no": season, "source": source, "aliases": list(aliases or ()),
    }


def builtin_items() -> list[dict]:
    """读取随程序自带的离线词典。数据来自 Bangumi 公开条目，不联网也可用。"""
    path = config.BUILTIN_ALIAS_FILE
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    out = []
    for it in items:
        if not isinstance(it, dict):
            continue
        cn = str(it.get("cn") or "").strip()
        if cn:
            out.append(_entry(cn, str(it.get("orig") or ""), it.get("aliases") or [],
                              it.get("bgm"), it.get("season"), "builtin"))
    return out


def ensure_builtin_loaded() -> int:
    """首次启动把内置词典写入 name_alias；不覆盖用户修正，单连接批量写入。"""
    global _BUILTIN_READY
    db.init_db()
    if _BUILTIN_READY:
        return 0
    inserted = 0
    with db.session() as conn:
        for entry in builtin_items():
            for key in (entry["title_orig"], entry["title_cn"], *entry["aliases"]):
                key = str(key or "").strip()
                if not key:
                    continue
                if conn.execute(
                    "SELECT 1 FROM name_alias WHERE lower(raw_title) = lower(?)", (key,)
                ).fetchone():
                    continue
                conn.execute(
                    "INSERT OR IGNORE INTO name_alias(raw_title, title_cn, title_orig, source, verified) "
                    "VALUES (?, ?, ?, 'builtin', 1)",
                    (key, entry["title_cn"], entry["title_orig"] or None),
                )
                inserted += 1
            if entry["bgm_id"] and not conn.execute(
                "SELECT 1 FROM subjects WHERE bgm_id = ?", (entry["bgm_id"],)
            ).fetchone():
                conn.execute(
                    "INSERT INTO subjects(bgm_id, title_cn, title_orig, season_no, source, confidence, verified) "
                    "VALUES (?, ?, ?, ?, 'builtin', 0.9, 1)",
                    (entry["bgm_id"], entry["title_cn"], entry["title_orig"] or None, entry["season_no"]),
                )
    global _INDEX_DIRTY
    with _LOCK:
        _INDEX_DIRTY = True
        _BUILTIN_READY = True
    return inserted


def _add_index(entry: dict, keys, *, overwrite: bool = False) -> None:
    for key in keys:
        if not key:
            continue
        n = normalize(str(key))
        if len(n) >= 3:
            if overwrite:
                _INDEX[n] = entry
            else:
                _INDEX.setdefault(n, entry)
        base = strip_season(str(key))
        if len(base) >= 4:
            if overwrite:
                _INDEX[base] = entry
            else:
                _INDEX.setdefault(base, entry)


def rebuild_index() -> None:
    """重建内存索引；用户手工修正放最后写入，优先级高于内置词典与在线结果。"""
    global _INDEX, _INDEX_DIRTY
    with _LOCK:
        _INDEX = {}
        for entry in builtin_items():
            _add_index(entry, (entry["title_orig"], entry["title_cn"], *entry["aliases"]))
        learned = db.rows("SELECT raw_title, title_cn, title_orig, source FROM name_alias")
        for r in learned:
            if (r["source"] or "") in ("user", "manual"):
                continue
            _add_index(_entry(r["title_cn"], r["title_orig"] or "", (r["raw_title"],),
                              source=r["source"] or "local"), (r["raw_title"],))
        for r in db.rows("SELECT title_cn, title_orig, aliases, bgm_id, season_no FROM subjects"):
            keys = [r["title_cn"], r["title_orig"]]
            if r["aliases"]:
                keys += [a.strip() for a in re.split(r"[,|;]", r["aliases"]) if a.strip()]
            _add_index(_entry(r["title_cn"], r["title_orig"] or "", (), r["bgm_id"],
                              r["season_no"], "local"), keys)
        for r in learned:
            if (r["source"] or "") not in ("user", "manual"):
                continue
            _add_index(_entry(r["title_cn"], r["title_orig"] or "", (r["raw_title"],),
                              source="user"), (r["raw_title"],), overwrite=True)
        _INDEX_DIRTY = False


def lookup_local(title: str, *, exact_only: bool = False) -> dict | None:
    """纯本地匹配：精确 → 去季后缀 → 谨慎的包含匹配。"""
    if not title:
        return None
    with _LOCK:
        if _INDEX_DIRTY or not _INDEX:
            rebuild_index()
        query = normalize(title)
        if not query:
            return None
        wanted = season_hint(title)
        hit = _INDEX.get(query)
        if not hit and not exact_only:
            base_hit = _INDEX.get(strip_season(title))
            # 带季数的标题不要硬套成第一季，避免 "S2" 被匹配成第一季
            if not (wanted and base_hit and base_hit.get("season_no")
                    and base_hit["season_no"] != wanted):
                hit = base_hit
        if not hit and not exact_only and len(query) >= 7:
            for key, entry in _INDEX.items():
                if len(key) >= 7 and (key in query or query in key) and abs(len(key) - len(query)) <= 10:
                    # 模糊匹配同样不能把带季数的标题套成另一季
                    if wanted and entry.get("season_no") and entry["season_no"] != wanted:
                        continue
                    hit = entry
                    break
        return dict(hit) if hit else None


def lookup_season(base_title: str, season: int) -> str | None:
    """导入时把「标题 + 解析出的季数」映射到该季的中文名（如 S2 → 第二季）。"""
    if not base_title or not season or season <= 1:
        return None
    for suffix in (f" S{season}", f" Season {season}", f" 第{season}季", f" 第{season}期"):
        hit = lookup_local(base_title + suffix, exact_only=True)
        if hit and hit["title_cn"] and hit["title_cn"] != base_title:
            return hit["title_cn"]
    return None


def _online_config() -> dict:
    """设置短缓存，避免批量匹配时每条标题都开一次数据库连接。"""
    global _CFG_CACHE, _CFG_AT
    with _LOCK:
        if _CFG_CACHE is not None and time.monotonic() - _CFG_AT < 3.0:
            return _CFG_CACHE
    s = db.all_settings()
    cfg = {
        "mode": s.get("metadata_source") or "cache_first",
        "base": (s.get("bangumi_base") or "https://api.bgm.tv").rstrip("/"),
        "proxy": (s.get("http_proxy") or "").strip(),
    }
    with _LOCK:
        _CFG_CACHE, _CFG_AT = cfg, time.monotonic()
    return cfg


def reset_online() -> None:
    """设置变更后重新允许在线探测，并立即刷新配置缓存。"""
    global _ONLINE_BLOCKED, _ONLINE_ERROR, _CFG_CACHE
    with _LOCK:
        _ONLINE_BLOCKED = False
        _ONLINE_ERROR = ""
        _CFG_CACHE = None


def _online_usable() -> bool:
    """在线通道是否已启用且未被熔断。

    注意：这是「通道状态」而非「网络连通性」结论；真正可达与否在请求时实测，
    失败会置 _ONLINE_BLOCKED。界面文案据此写「已启用 / 已降级」，不宣称「可用」。
    """
    return not _ONLINE_BLOCKED and _online_config()["mode"] != "local_only"


def search_online(title: str) -> dict | None:
    """导入时同步尝试 Bangumi 搜索；失败即整批降级，不阻塞后续条目。"""
    global _ONLINE_BLOCKED, _ONLINE_ERROR
    if _ONLINE_BLOCKED:
        return None
    cfg = _online_config()
    if cfg["mode"] == "local_only":
        return None
    try:
        import requests
        url = f"{cfg['base']}/search/subject/{quote(title, safe='')}"
        proxies = {"http": cfg["proxy"], "https": cfg["proxy"]} if cfg["proxy"] else None
        resp = requests.get(
            url,
            params={"type": 2, "max_results": 6, "responseGroup": "small"},
            headers={"User-Agent": f"MagicVideo/{__version__} (local personal tool)"},
            timeout=(1.5, 3.0),
            proxies=proxies,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        with _LOCK:
            _ONLINE_BLOCKED = True
            _ONLINE_ERROR = f"{type(exc).__name__}: {exc}"[:180]
        return None

    if isinstance(data, dict):
        items = data.get("list") or data.get("subjects") or data.get("data") or []
    else:
        items = data
    if not isinstance(items, list):
        return None

    wanted = season_hint(title)
    for it in items:
        if not isinstance(it, dict):
            continue
        cn = str(it.get("name_cn") or "").strip()
        if not cn or not CJK_RE.search(cn):
            continue
        orig = str(it.get("name") or "").strip()
        season = season_hint(cn) or season_hint(orig) or (1 if not wanted else wanted)
        if wanted and season != wanted:
            continue
        return _entry(cn, orig, (title,), it.get("id"), season, "online")
    return None


def remember(raw_title: str, title_cn: str, *, title_orig: str = "", bgm_id=None,
             season_no=None, source: str = "user", verified: int = 1) -> None:
    """记住一条对照（用户手动修正优先，不会被内置词典或在线结果覆盖）。"""
    raw_title = (raw_title or "").strip()
    title_cn = (title_cn or "").strip()
    if not raw_title or not title_cn:
        return
    db.execute(
        "INSERT INTO name_alias(raw_title, title_cn, title_orig, source, verified) "
        "VALUES (?, ?, ?, ?, ?) ON CONFLICT(raw_title) DO UPDATE SET "
        "title_cn = excluded.title_cn, title_orig = excluded.title_orig, "
        "source = excluded.source, verified = excluded.verified "
        "WHERE name_alias.source NOT IN ('user', 'manual') OR excluded.source IN ('user', 'manual')",
        (raw_title, title_cn, title_orig or None, source, verified),
    )
    if bgm_id:
        db.execute(
            "INSERT INTO subjects(bgm_id, title_cn, title_orig, season_no, source, confidence, verified) "
            "SELECT ?, ?, ?, ?, ?, 0.8, ? WHERE NOT EXISTS (SELECT 1 FROM subjects WHERE bgm_id = ?)",
            (bgm_id, title_cn, title_orig or None, season_no, source, verified, bgm_id),
        )
    global _INDEX_DIRTY
    with _LOCK:
        _INDEX_DIRTY = True


def match_one(title: str, *, allow_online: bool = True) -> dict:
    title = (title or "").strip()
    if not title:
        return {"matched": False, "title_cn": "", "by": "none"}
    mode = _online_config()["mode"]
    is_foreign = not CJK_RE.search(title)

    # online_first：导入时先联网校准，本地词典作为兜底
    if allow_online and is_foreign and mode == "online_first" and not _ONLINE_BLOCKED:
        first = search_online(title)
        if first:
            remember(title, first["title_cn"], title_orig=first["title_orig"],
                     bgm_id=first["bgm_id"], season_no=first["season_no"],
                     source="online", verified=0)
            return {"matched": True, "title_cn": first["title_cn"],
                    "title_orig": first["title_orig"], "bgm_id": first["bgm_id"], "by": "online"}

    local = lookup_local(title)
    if local and local["title_cn"] and local["title_cn"] != title:
        return {
            "matched": True, "title_cn": local["title_cn"], "title_orig": local["title_orig"],
            "bgm_id": local["bgm_id"], "by": "builtin" if local["source"] == "builtin" else "local",
        }
    # 带季数但词典没有收录该季：给出主标题中文名，不编造「第 N 季」名称，
    # 界面标记为 base，用户可在预览里手动改（改动会被记住）。
    wanted = season_hint(title)
    if wanted and wanted > 1:
        base = lookup_local(strip_season(title), exact_only=True)
        if base and base["title_cn"] and base["title_cn"] != title:
            return {"matched": True, "title_cn": base["title_cn"],
                    "title_orig": base["title_orig"], "bgm_id": base["bgm_id"],
                    "by": "base", "season_no": wanted}
    if not is_foreign:
        # 本身已是中文名，无需翻译
        return {"matched": True, "title_cn": title, "by": "native"}
    if not allow_online or _ONLINE_BLOCKED or mode == "local_only":
        return {"matched": False, "title_cn": title, "by": "none", "reason": "no-match"}
    online = search_online(title)
    if online:
        remember(title, online["title_cn"], title_orig=online["title_orig"],
                 bgm_id=online["bgm_id"], season_no=online["season_no"],
                 source="online", verified=0)
        return {
            "matched": True, "title_cn": online["title_cn"], "title_orig": online["title_orig"],
            "bgm_id": online["bgm_id"], "by": "online",
        }
    return {"matched": False, "title_cn": title, "by": "none", "reason": _ONLINE_ERROR or "no-match"}


def match_titles(titles, *, allow_online: bool = True) -> dict:
    """批量匹配。先本地（瞬时），未命中的再限量并发在线；在线不通时整批快速降级。

    大小写/空格不同的同一标题会合并成一次查询，但仍为每个原始写法保留结果映射，
    方便调用方按原始标题取值。
    """
    raw = [t.strip() for t in titles if t and t.strip()]
    groups: dict[str, list[str]] = {}
    for title in raw:
        groups.setdefault(normalize(title) or title.lower(), []).append(title)
    representatives = {key: variants[0] for key, variants in groups.items()}
    unique = list(representatives.values())

    results: dict[str, dict] = {}
    pending = []
    for title in unique:
        r = match_one(title, allow_online=False)
        if r["matched"] or not _online_usable():
            results[title] = r
        else:
            pending.append(title)

    if pending and allow_online:
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="mv-match") as pool:
            for title, r in zip(pending, pool.map(lambda t: match_one(t, allow_online=True), pending)):
                results[title] = r
    for title in pending:
        results.setdefault(title, {"matched": False, "title_cn": title, "by": "none", "reason": "offline"})

    # 同一归一化标题的所有写法共享同一结果
    for key, variants in groups.items():
        rep = representatives[key]
        for title in variants:
            if title not in results:
                results[title] = dict(results[rep])

    representative_values = [results[rep] for rep in unique if rep in results]
    return {
        "ok": True,
        "results": results,
        "total": len(unique),
        "matched": sum(1 for r in representative_values if r["matched"]),
        "unmatched": sum(1 for r in representative_values if not r["matched"]),
        "by_builtin": sum(1 for r in representative_values if r["by"] == "builtin"),
        "by_online": sum(1 for r in representative_values if r["by"] == "online"),
        "by_base": sum(1 for r in representative_values if r["by"] == "base"),
        "online_available": _online_usable(),
        "online_error": _ONLINE_ERROR,
    }


def apply_to_media(results: dict) -> int:
    """把匹配结果写回媒体库：保留 title_guess 原文，中文名单独存 title_cn。

    返回值是真正被 UPDATE 的行数，而不是匹配成功的条目数。
    """
    changed = 0
    with db.session() as conn:
        for title, r in results.items():
            if not r.get("matched"):
                continue
            cur = conn.execute(
                "UPDATE media_files SET title_cn = ?, match_by = ? "
                "WHERE title_cn IS NULL AND (lower(title_guess) = lower(?) OR lower(title_raw) = lower(?))",
                (r["title_cn"], r["by"], title, title),
            )
            changed += max(0, cur.rowcount)
    return changed


def apply_to_watched(results: dict) -> int:
    changed = 0
    with db.session() as conn:
        for title, r in results.items():
            if not r.get("matched"):
                continue
            cur = conn.execute(
                "UPDATE watched_list SET title_cn = ?, match_by = ? "
                "WHERE lower(title) = lower(?) AND (title_cn IS NULL OR title_cn = '' OR title_cn = title)",
                (r["title_cn"], r["by"], title),
            )
            changed += max(0, cur.rowcount)
    return changed


def auto_match_media() -> dict:
    """扫描完成后调用：只处理还没匹配过的新标题。"""
    rows = db.rows(
        "SELECT DISTINCT COALESCE(title_guess, title_raw) AS t FROM media_files "
        "WHERE title_cn IS NULL AND COALESCE(title_guess, title_raw) IS NOT NULL LIMIT 500"
    )
    if not rows:
        return {"ok": True, "total": 0, "matched": 0, "online_available": _online_usable()}
    out = match_titles([r["t"] for r in rows])
    out["applied"] = apply_to_media(out["results"])
    return out


def rematch_all() -> dict:
    """手动重新匹配所有未识别标题（用户改了代理或词典后可点）。"""
    reset_online()
    rebuild_index()
    titles = [
        r["t"] for r in db.rows(
            "SELECT DISTINCT COALESCE(title_guess, title_raw) AS t FROM media_files "
            "WHERE title_cn IS NULL AND COALESCE(title_guess, title_raw) IS NOT NULL"
        )
    ]
    titles += [
        r["t"] for r in db.rows(
            "SELECT DISTINCT title AS t FROM watched_list WHERE title_cn IS NULL OR title_cn = '' OR title_cn = title"
        )
    ]
    out = match_titles(titles)
    out["applied_media"] = apply_to_media(out["results"])
    out["applied_watched"] = apply_to_watched(out["results"])
    return out


def stats() -> dict:
    alias_total = db.scalar("SELECT COUNT(*) FROM name_alias") or 0
    builtin = db.scalar("SELECT COUNT(*) FROM name_alias WHERE source = 'builtin'") or 0
    unmatched_media = db.scalar(
        "SELECT COUNT(*) FROM media_files WHERE title_cn IS NULL AND COALESCE(title_guess, title_raw) IS NOT NULL"
    ) or 0
    unmatched_watched = db.scalar(
        "SELECT COUNT(*) FROM watched_list WHERE title_cn IS NULL OR title_cn = ''"
    ) or 0
    return {
        "builtin_terms": len(builtin_items()),
        "alias_total": alias_total,
        "builtin": builtin,
        "learned": alias_total - builtin,
        "unmatched": unmatched_media + unmatched_watched,
        "unmatched_media": unmatched_media,
        "unmatched_watched": unmatched_watched,
        "online_available": _online_usable(),
        "online_error": _ONLINE_ERROR,
        "mode": _online_config()["mode"],
    }


def import_alias_file() -> dict:
    """可选的手动批量补充：从 data/alias_import.json 一次性导入对照表。"""
    path = config.ALIAS_IMPORT_FILE
    if not path.exists():
        return {"ok": True, "imported": 0, "reason": "暂无补充文件"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"文件解析失败：{exc}"}
    items = data.get("items", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        return {"ok": False, "error": "文件格式不正确"}
    imported = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("raw_title") or "").strip()
        cn = str(item.get("title_cn") or "").strip()
        if not raw or not cn:
            continue
        remember(raw, cn, title_orig=str(item.get("title_orig") or ""),
                 bgm_id=item.get("bgm_id"), season_no=item.get("season_no"), source="manual")
        imported += 1
    out = rematch_all()
    return {"ok": True, "imported": imported, "rematched": out.get("matched", 0)}
