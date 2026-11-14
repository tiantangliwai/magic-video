"""目录扫描（P0 基础版）：递归枚举视频文件并入库。

P1 会在这层之上接入完整的文件名解析、中文元数据刮削与封面缓存。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import config, db

BRACKET_RE = re.compile(r"\[[^\]]*\]")
PAREN_RE = re.compile(r"\([^)]*\)")
TAG_RE = re.compile(
    r"\b(?:1080p|720p|2160p|4K|HEVC|x264|x265|H\.?264|H\.?265|AAC|FLAC|10bit|8bit|WEB-?Rip|WEB-?DL|BDRip|BluRay|ASSx?\d?|GB|Big5|简|繁|内嵌|外挂)\b",
    re.IGNORECASE,
)
SEASON_RE = re.compile(r"(?:S|Season|第)\s*(\d{1,2})\s*(?:季|期)?", re.IGNORECASE)
EPISODE_PATTERNS = [
    re.compile(r"[Ss](\d{1,2})[Ee](\d{1,3})"),
    re.compile(r"(?:EP|Episode|第)\s*(\d{1,3})\s*(?:话|集|話)?"),
    re.compile(r"^\s*[-_]?\s*(\d{1,3})\s*(?:v\d)?\s*$"),
    re.compile(r"[-_\s]\s*(\d{2,3})\s*(?:v\d)?\s*[-_\s\[]"),
]
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")


def add_directory(path: str) -> dict:
    p = Path(path.strip().strip('"'))
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": f"目录不存在：{p}"}
    try:
        dir_id = db.execute(
            "INSERT INTO directories(path) VALUES (?) ON CONFLICT(path) DO UPDATE SET enabled = 1",
            (str(p),),
        )
        if not dir_id:
            dir_id = db.scalar("SELECT id FROM directories WHERE path = ?", (str(p),))
        return {"ok": True, "id": dir_id, "path": str(p)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def list_directories() -> list[dict]:
    return db.rows(
        "SELECT d.*, (SELECT COUNT(*) FROM media_files f WHERE f.dir_id = d.id) AS files "
        "FROM directories d ORDER BY d.id"
    )


def remove_directory(dir_id: int) -> dict:
    db.execute("DELETE FROM directories WHERE id = ?", (dir_id,))
    return {"ok": True}


def set_directory_enabled(dir_id: int, enabled: bool) -> dict:
    db.execute("UPDATE directories SET enabled = ? WHERE id = ?", (1 if enabled else 0, dir_id))
    return {"ok": True}


def should_ignore(path: Path) -> bool:
    if any(part in config.IGNORE_DIR_NAMES for part in path.parts):
        return True
    if path.suffix.lower() not in config.VIDEO_EXTS:
        return True
    name = path.name
    return any(tag.lower() in name.lower() for tag in config.IGNORE_NAME_PATTERNS)


def parse_filename(filename: str) -> dict:
    """从文件名提取作品名 / 季 / 集数（P0 基础版，P1 会更精细）。"""
    stem = Path(filename).stem
    season, episode = None, None

    s_ep = EPISODE_PATTERNS[0].search(stem)
    if s_ep:
        season, episode = int(s_ep.group(1)), int(s_ep.group(2))
    else:
        s = SEASON_RE.search(stem)
        if s:
            season = int(s.group(1))
        for pattern in EPISODE_PATTERNS[1:]:
            m = pattern.search(stem)
            if m:
                episode = int(m.group(1))
                break

    year_match = YEAR_RE.search(stem)
    cleaned = BRACKET_RE.sub(" ", stem)
    cleaned = PAREN_RE.sub(" ", cleaned)
    cleaned = TAG_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[_\.]+", " ", cleaned)
    cleaned = re.sub(r"[-–—]\s*\d{1,3}\b.*$", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -_[]()")
    return {
        "title_guess": cleaned or stem,
        "season_no": season,
        "episode_no": episode,
        "year": int(year_match.group(1)) if year_match else None,
    }


def scan(directory_id: int | None = None, progress_cb=None) -> dict:
    """增量扫描：已存在的文件只更新 size/mtime，不重复插入。"""
    db.init_db()
    target = "SELECT * FROM directories WHERE enabled = 1 AND id = ?" if directory_id else \
             "SELECT * FROM directories WHERE enabled = 1"
    params = (directory_id,) if directory_id else ()
    directories = db.rows(target, params)

    added = updated = skipped = 0
    for directory in directories:
        root = Path(directory["path"])
        if not root.exists():
            continue
        count = 0
        for path in root.rglob("*"):
            if not path.is_file() or should_ignore(path):
                continue
            try:
                stat = path.stat()
            except OSError:
                skipped += 1
                continue
            info = parse_filename(path.name)
            existing = db.row("SELECT id, size, mtime FROM media_files WHERE path = ?", (str(path),))
            if existing:
                if existing["size"] != stat.st_size or abs((existing["mtime"] or 0) - stat.st_mtime) > 1:
                    db.execute(
                        "UPDATE media_files SET size = ?, mtime = ?, updated_at = ? WHERE id = ?",
                        (stat.st_size, stat.st_mtime, db.now(), existing["id"]),
                    )
                    updated += 1
            else:
                db.execute(
                    "INSERT INTO media_files(dir_id, path, filename, size, mtime, title_raw, "
                    "title_guess, season_no, episode_no, year) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        directory["id"], str(path), path.name, stat.st_size, stat.st_mtime,
                        path.stem, info["title_guess"], info["season_no"], info["episode_no"], info["year"],
                    ),
                )
                added += 1
            count += 1
            if progress_cb and count % 50 == 0:
                progress_cb(count)
        db.execute(
            "UPDATE directories SET last_scan_at = ?, file_count = ? WHERE id = ?",
            (db.now(), count, directory["id"]),
        )
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped,
            "directories": len(directories)}
