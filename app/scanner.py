"""目录扫描：递归枚举视频文件、解析文件名、入库并即时匹配中文名。

设计约定：
    - 绝不修改用户磁盘上的任何视频文件或文件名，只读取。
    - 增量扫描：以路径为键，已存在的文件只更新 size / mtime。
    - 文件不可达时只跳过、不删除已有记录，避免移动硬盘离线导致历史丢失。
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import config, db, metadata

# 指纹只读头尾各 1MB：大文件不用整文件读，仍足以区分不同集（同 size + 同 mtime
# 到秒级 + 头尾内容一致，几乎不可能是两个不同文件）。
_HASH_CHUNK = 1024 * 1024


def partial_hash(path: Path) -> str | None:
    """返回 ``size:sha1(头1MB+尾1MB)`` 指纹；读失败返回 None（不中断扫描）。"""
    try:
        size = path.stat().st_size
        h = hashlib.sha1()
        with open(path, "rb") as fh:
            h.update(fh.read(_HASH_CHUNK))
            if size > _HASH_CHUNK * 2:
                fh.seek(-_HASH_CHUNK, os.SEEK_END)
                h.update(fh.read(_HASH_CHUNK))
        return f"{size}:{h.hexdigest()}"
    except OSError:
        return None


def _relink_moved_file(path: Path, stat, dir_id: int) -> bool:
    """同一文件被移动/重命名后，把旧记录重关联到新路径（保留 id 与播放进度）。

    只在「新路径查不到记录」时调用，且满足全部条件才重关联，宁可不做也不误关联：
    * 存在**唯一**一条同 size、mtime 相差 ≤1 秒、路径不同的旧记录；
    * 旧路径当前已不存在（否则是副本，不该合并）；
    * 头尾指纹一致（旧记录没存过哈希时，直接补上并重关联）。
    """
    candidates = db.rows(
        "SELECT id, path, size, mtime, partial_hash FROM media_files "
        "WHERE size = ? AND mtime IS NOT NULL AND ABS(mtime - ?) <= 1 AND path <> ?",
        (stat.st_size, stat.st_mtime, str(path)),
    )
    if len(candidates) != 1:
        return False
    candidate = candidates[0]
    try:
        if Path(candidate["path"]).exists():
            return False
    except OSError:
        return False
    digest = partial_hash(path)
    if not digest:
        return False
    stored = candidate.get("partial_hash")
    if stored and stored != digest:
        return False
    db.execute(
        "UPDATE media_files SET path = ?, filename = ?, dir_id = ?, size = ?, mtime = ?, "
        "partial_hash = ?, deleted_at = NULL, updated_at = ? WHERE id = ?",
        (str(path), path.name, dir_id, stat.st_size, stat.st_mtime, digest,
         db.now(), candidate["id"]),
    )
    return True

BRACKET_RE = re.compile(r"\[[^\]]*\]")
PAREN_RE = re.compile(r"\([^)]*\)")
TAG_RE = re.compile(
    r"\b(?:1080p|720p|2160p|4K|HEVC|x264|x265|H\.?264|H\.?265|AAC|FLAC|10bit|8bit|"
    r"WEB-?Rip|WEB-?DL|BDRip|BluRay|ASSx?\d?|GB|Big5|简|繁|内嵌|外挂)\b",
    re.IGNORECASE,
)
# 标准剧集标记优先于模糊猜测
SXXEYY_RE = re.compile(r"\bS(\d{1,2})E(\d{1,3})(?:-E?(\d{1,3}))?\b", re.IGNORECASE)
SEASON_RE = re.compile(r"(?:Season|S|第)\s*(\d{1,2})\s*(?:季|期)?", re.IGNORECASE)
SEASON_WORD_RE = re.compile(
    r"\b(?:Season|S)\s*\d{1,2}\b|第\s*\d{1,2}\s*(?:季|期)|"
    r"\b(?:2nd|3rd|1st)\s+Season\b",
    re.IGNORECASE,
)
EPISODE_WORD_RE = re.compile(
    r"(?:EP|Episode|第)\s*\d{1,3}\s*(?:话|集|話)?|(?<![A-Za-z0-9])E\d{1,3}(?![A-Za-z0-9])",
    re.IGNORECASE,
)
TRAILING_EP_RE = re.compile(r"[-–—_.\s]\s*(\d{1,3})\s*(?:v\d)?\s*$", re.IGNORECASE)
EPISODE_PATTERNS = [
    re.compile(r"(?:EP|Episode|第)\s*(\d{1,3})\s*(?:话|集|話)?"),
    re.compile(r"^\s*[-_]?\s*(\d{1,3})\s*(?:v\d)?\s*$"),
    # Anna.Directors.Cut.E01 / Show.E08.1080p 这类「无 S 前缀的 E01」也是剧集标记
    re.compile(r"(?<![A-Za-z0-9])E(\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE),
    re.compile(r"[-_\s]\s*(\d{2,3})\s*(?:v\d)?\s*[-_\s\[]"),
]
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# 「SP」这类短标记必须独立成词，Spy / Special 不能被误判为特典
SPECIAL_RE = re.compile(r"(?<![A-Za-z])sp(?=\d|[^A-Za-z]|$)", re.IGNORECASE)


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
    """跳过非视频、系统目录、样片与特典；短标记按词匹配，避免误杀正片。"""
    if any(part in config.IGNORE_DIR_NAMES for part in path.parts):
        return True
    if path.suffix.lower() not in config.VIDEO_EXTS:
        return True
    stem = path.stem.lower()
    for tag in config.IGNORE_NAME_PATTERNS:
        token = tag.lower()
        if token == "sp":
            if SPECIAL_RE.search(stem):
                return True
        elif token in stem:
            return True
    return False


def parse_filename(filename: str) -> dict:
    """从文件名提取作品名 / 季 / 集数 / 年份。

    同时兼容字幕组命名、Plex 风格 SxxEyy、中文「第 N 话」以及以集数结尾的文件名。
    解析结果只用于识别与展示，不会回写或重命名原始文件。
    """
    stem = Path(filename).stem
    season, episode = None, None

    s_ep = SXXEYY_RE.search(stem)
    if s_ep:
        season, episode = int(s_ep.group(1)), int(s_ep.group(2))
    else:
        s = SEASON_RE.search(stem)
        if s:
            season = int(s.group(1))
        for pattern in EPISODE_PATTERNS:
            m = pattern.search(stem)
            if m:
                episode = int(m.group(1))
                break
        if episode is None:
            m = TRAILING_EP_RE.search(stem)
            if m:
                episode = int(m.group(1))

    year_match = YEAR_RE.search(stem)
    cleaned = BRACKET_RE.sub(" ", stem)
    cleaned = PAREN_RE.sub(" ", cleaned)
    cleaned = TAG_RE.sub(" ", cleaned)
    cleaned = SXXEYY_RE.sub(" ", cleaned)
    cleaned = SEASON_WORD_RE.sub(" ", cleaned)
    cleaned = EPISODE_WORD_RE.sub(" ", cleaned)
    cleaned = YEAR_RE.sub(" ", cleaned)
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
    """增量扫描：已存在的文件只更新 size/mtime，不重复插入；结束即刻匹配中文名。"""
    db.init_db()
    target = "SELECT * FROM directories WHERE enabled = 1 AND id = ?" if directory_id else \
             "SELECT * FROM directories WHERE enabled = 1"
    params = (directory_id,) if directory_id else ()
    directories = db.rows(target, params)

    added = updated = skipped = 0
    for directory in directories:
        root = Path(directory["path"])
        if not root.exists():
            # 目录暂时不可达（移动硬盘未插、网络盘掉线）时保留历史，不删记录
            continue
        count = 0
        for path in root.rglob("*"):
            try:
                # is_file() 内部也会 stat；文件被占用或权限不足时不能让整个扫描中断
                if not path.is_file() or should_ignore(path):
                    continue
                stat = path.stat()
            except OSError:
                skipped += 1
                continue
            info = parse_filename(path.name)
            existing = db.row(
                "SELECT id, size, mtime, deleted_at FROM media_files WHERE path = ?", (str(path),)
            )
            if existing:
                changed = (existing["size"] != stat.st_size
                           or abs((existing["mtime"] or 0) - stat.st_mtime) > 1)
                # 文件从回收站恢复 / 重新下载后，清掉「本地文件已移除」标记；
                # 观看进度、历史与看过的列表始终不动。
                if existing["deleted_at"]:
                    db.execute("UPDATE media_files SET deleted_at = NULL WHERE id = ?",
                               (existing["id"],))
                if changed:
                    db.execute(
                        "UPDATE media_files SET size = ?, mtime = ?, updated_at = ? WHERE id = ?",
                        (stat.st_size, stat.st_mtime, db.now(), existing["id"]),
                    )
                    updated += 1
            else:
                # 同一文件被移动/改名时优先重关联（保留 id 与播放进度），否则按新文件入库
                if _relink_moved_file(path, stat, directory["id"]):
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
    # 扫描结束即完成中文名匹配，结果当场写入媒体库，不依赖任何定时任务
    match_result = metadata.auto_match_media()
    return {"ok": True, "added": added, "updated": updated, "skipped": skipped,
            "directories": len(directories),
            "matched_cn": match_result.get("matched", 0),
            "match_total": match_result.get("total", 0),
            "match_online": match_result.get("online_available", False)}
