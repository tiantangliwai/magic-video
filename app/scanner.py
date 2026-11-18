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
# 季数：英文 `Season 2` / `S02`，中文 `第2季` / `第2期`。
# 中文这支**必须要求「季/期」后缀**：旧版后缀可选，导致 `第03话` 被同时读成「第 3 季」，
# 一部 12 集的番会被拆成 12 个季。集数交给 EPISODE_PATTERNS 处理。
SEASON_RE = re.compile(r"(?:Season|S)\s*(\d{1,2})(?!\d)|第\s*(\d{1,2})\s*(?:季|期)", re.IGNORECASE)
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
# `1x01` / `2x13`：美剧常见写法，同时给出季与集。放在 SxxEyy 之后、其它集数规则之前。
XFORMAT_RE = re.compile(r"(?<![\dA-Za-z])(\d{1,2})x(\d{1,3})(?!\d)", re.IGNORECASE)
EPISODE_PATTERNS = [
    # EP12 / Episode 3 / 第03话。中文这支同样要求「话/集/話」后缀，
    # 否则 `第2季` 会被当成第 2 集。
    re.compile(r"(?:EP|Episode)\s*(\d{1,3})|第\s*(\d{1,3})\s*(?:话|集|話)"),
    re.compile(r"^\s*[-_]?\s*(\d{1,3})\s*(?:v\d)?\s*$"),
    # Anna.Directors.Cut.E01 / Show.E08.1080p 这类「无 S 前缀的 E01」也是剧集标记
    re.compile(r"(?<![A-Za-z0-9])E(\d{1,3})(?![A-Za-z0-9])", re.IGNORECASE),
    # `Show - 05 - 1080p` / `Show_05_[1080p]` 这类两侧带分隔符的独立集号。
    # 结尾若是 -/_，其后不能紧跟字母：否则片名里的 `100-nin`（「100个女朋友」）
    # 会被当成第 100 集，把整部番的 01-12 集 + PV 全塞进同一集。空格/[ 照常接受。
    re.compile(r"[-_\s]\s*(\d{2,3})\s*(?:v\d)?\s*(?:[-_](?![A-Za-z])|[\s\[])"),
]
# 只有方括号里是纯数字时才当集数（`[1080p]`、`[2023]` 不算）；歧义大，标低置信度。
BRACKET_EP_RE = re.compile(r"\[\s*(\d{1,3})\s*\]")
# 集数区间 `01-02` / `1~12`：不知道想表达「这一集」还是「整段」，标低置信度。
# 两个数字之间**不允许空格**：`Season 2 - 05` 里的「2 - 05」是季 + 集，不是区间。
RANGE_EP_RE = re.compile(r"(?<!\d)(\d{1,3})[-~–—](\d{1,3})(?!\d)(?!\s*[pPiI])")
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
# 文件名里的作品 ID 标签：{tmdb-123}、[tvdbid-456]、{tvdb=789}。
# 数字允许为空，是为了把 `{tmdb-}` 这类残缺标签也一并剥掉，免得污染标题；
# 但没有数字时不产出 ID（不能凭一个空标签认作品）。
# 必须放在 BRACKET_RE/PAREN_RE 之前处理：`{}` 不在那两个正则的覆盖范围内。
ID_TAG_RE = re.compile(
    r"[\[{(]\s*(tmdb|tvdb)(?:id)?\s*[-_:= ]?\s*(\d{0,9})\s*[\]})]",
    re.IGNORECASE,
)
# 「SP」这类短标记必须独立成词，Spy / Special 不能被误判为特典
SPECIAL_RE = re.compile(r"(?<![A-Za-z])sp(?=\d|[^A-Za-z]|$)", re.IGNORECASE)


def add_directory(path: str) -> dict:
    p = Path(path.strip().strip('"'))
    if not p.exists() or not p.is_dir():
        return {"ok": False, "error": f"目录不存在：{p}"}
    try:
        # RETURNING 一步拿到 id：旧写法在 ``ON CONFLICT DO UPDATE`` 命中时
        # lastrowid 为 0，只能再查一次，中间可能被别的进程删掉/改写。
        dir_id = db.scalar(
            "INSERT INTO directories(path) VALUES (?) "
            "ON CONFLICT(path) DO UPDATE SET enabled = 1 RETURNING id",
            (str(p),),
        )
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
    """从文件名提取作品名 / 季 / 集数 / 年份，并标注集数的**可信度**。

    同时兼容字幕组命名、Plex 风格 SxxEyy、美剧 ``1x01``、中文「第 N 话」以及以
    集数结尾的文件名。解析结果只用于识别与展示，不会回写或重命名原始文件。

    ``episode_confidence`` 三档，用于把「歧义写法」挑出来让用户确认：

    - ``high``：``S01E02`` / ``EP12`` / ``第03话`` / ``E01`` / ``第2季 - 05``
    - ``medium``：``1x01``（格式规范但少见）、纯数字文件名、结尾数字、区间分隔写法
    - ``low``：``[01]`` 方括号集数、``01-02`` 区间——**语义本身有歧义**，进待确认区

    低置信度不是「猜错了」，而是「同一串数字既可能是集数也可能是别的」。与其静默
    猜一个，不如标出来让用户点一下确认（写入后 ``episode_source='user'``）。
    """
    stem = Path(filename).stem
    season, episode = None, None
    ep_source, ep_conf = "", ""

    # 作品 ID 先提取出来：它比标题写法可靠（重名翻拍 / 多罗马字都绕过去了），
    # 而且必须在季集解析与清洗之前剥离，否则 `{tmdb-123}` 会污染 title_guess。
    tmdb_id = tvdb_id = None
    for kind, number in ID_TAG_RE.findall(stem):
        if not number:
            continue
        if kind.lower() == "tmdb":
            tmdb_id = tmdb_id or int(number)
        else:
            tvdb_id = tvdb_id or int(number)
    id_clean = ID_TAG_RE.sub(" ", stem)

    s_ep = SXXEYY_RE.search(id_clean)
    x_fmt = None if s_ep else XFORMAT_RE.search(id_clean)
    if s_ep:
        season, episode = int(s_ep.group(1)), int(s_ep.group(2))
        ep_source, ep_conf = "sxxeyy", "high"
    elif x_fmt:
        # 1x01：季与集都有，格式算规范，但比 SxxEyy 少见一档
        season, episode = int(x_fmt.group(1)), int(x_fmt.group(2))
        ep_source, ep_conf = "xformat", "medium"
    else:
        s = SEASON_RE.search(id_clean)
        if s:
            season = int(s.group(1) or s.group(2))
        for idx, pattern in enumerate(EPISODE_PATTERNS):
            m = pattern.search(id_clean)
            if not m:
                continue
            number = m.group(1) or m.group(2)
            if not number:
                continue
            episode = int(number)
            if idx == 0:
                ep_source = "chinese" if "第" in m.group(0) else "ep"
                ep_conf = "high"
            elif idx == 2:
                ep_source, ep_conf = "ep", "high"
            elif idx == 1:
                ep_source, ep_conf = "bare", "medium"
            else:
                ep_source, ep_conf = "numbered", "medium"
            break
        if episode is None:
            m = TRAILING_EP_RE.search(id_clean)
            if m:
                episode = int(m.group(1))
                ep_source, ep_conf = "trailing", "medium"
        if episode is None:
            m = BRACKET_EP_RE.search(id_clean)
            if m:
                episode = int(m.group(1))
                ep_source, ep_conf = "bracket", "low"

    # 区间写法（01-02 / 1~12）：数字本身可能指「整段」，标低置信度让用户确认。
    # 只在已经靠 trailing/bracket 这类弱规则取到集数时才升级成 low，
    # SxxEyy 自带的 `S01E01-E03` 区间是明确的，不在此列。
    if ep_conf in ("medium", "") and RANGE_EP_RE.search(id_clean):
        ep_source, ep_conf = ep_source or "range", "low"

    year_match = YEAR_RE.search(id_clean)
    cleaned = BRACKET_RE.sub(" ", id_clean)
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
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
        "episode_source": ep_source,
        "episode_confidence": ep_conf,
    }


def _under_prefix(path: str, prefix: str) -> bool:
    """``path`` 是否位于 ``prefix`` 目录之下（Windows 路径，大小写不敏感）。"""
    p = str(path).replace("/", "\\").lower()
    pre = str(prefix).replace("/", "\\").rstrip("\\").lower()
    if not pre:
        return False
    return p == pre or p.startswith(pre + "\\")


def _mark_missing(dir_id: int) -> int:
    """把某个不可达目录下的文件标为「缺失」。

    只写 ``missing_since``：不删行、不动 ``deleted_at``、不碰播放进度与历史。
    移动硬盘没插、网络盘掉线是高频场景，误删历史比暂时看不到严重得多。
    """
    return db.execute(
        "UPDATE media_files SET missing_since = ? WHERE dir_id = ? AND missing_since IS NULL",
        (db.now(), dir_id),
    )


def diagnose_library() -> dict:
    """体检：目录是否可达、有多少文件找不到了。**只读**，不改库。"""
    dirs = []
    total_missing = 0
    for d in db.rows("SELECT * FROM directories ORDER BY id"):
        root = Path(d["path"])
        reachable = root.exists() and root.is_dir()
        stats = db.row(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN missing_since IS NOT NULL THEN 1 ELSE 0 END) AS missing "
            "FROM media_files WHERE dir_id = ?",
            (d["id"],),
        ) or {}
        total = int(stats.get("total") or 0)
        missing = int(stats.get("missing") or 0)
        dirs.append({
            "id": d["id"], "path": d["path"], "enabled": bool(d["enabled"]),
            "reachable": reachable, "files": total, "missing": missing,
            "last_scan_at": d["last_scan_at"],
            # 目录不可达但还没扫过一轮：提示用户「跑一次扫描以标记缺失」
            "needs_scan": (not reachable) and total > 0 and missing < total,
        })
        total_missing += missing
    missing_ids = [
        r["id"] for r in db.rows(
            "SELECT id FROM media_files WHERE missing_since IS NOT NULL ORDER BY id LIMIT 500")
    ]
    return {"ok": True, "directories": dirs, "total_missing": total_missing,
            "missing_ids": missing_ids}


def preview_relocate(old_prefix: str, new_prefix: str) -> dict:
    """重定位预览：算出会改哪些行、有多少冲突。**只计算不写库**。

    整盘换符（D:\\ 变 E:\\）或目录改名后，逐文件依赖指纹重关联会因「候选不唯一」
    失效；这里按路径前缀整体替换，再用指纹校验兜底。
    """
    old = (old_prefix or "").strip().strip('"')
    new = (new_prefix or "").strip().strip('"')
    if not old or not new:
        return {"ok": False, "error": "新旧路径前缀都不能为空"}
    if old.replace("/", "\\").rstrip("\\").lower() == new.replace("/", "\\").rstrip("\\").lower():
        return {"ok": False, "error": "新旧路径前缀相同，无需重定位"}

    existing_paths = {r["path"] for r in db.rows("SELECT path FROM media_files")}
    items = []
    for r in db.rows("SELECT id, path, filename, missing_since FROM media_files"):
        if not _under_prefix(r["path"], old):
            continue
        rel = str(r["path"])[len(old.rstrip("\\/")):].lstrip("\\/")
        target = str(Path(new.rstrip("\\/")) / rel)
        items.append({
            "id": r["id"],
            "old_path": r["path"],
            "new_path": target,
            "filename": r["filename"],
            "was_missing": bool(r["missing_since"]),
            # 目标路径已被别的行占用（重名/重复扫描）时不能直接改，交给指纹匹配
            "conflict": target in existing_paths and target != r["path"],
            "target_exists": Path(target).exists(),
        })
    conflicts = sum(1 for i in items if i["conflict"])
    return {
        "ok": True,
        "old_prefix": old,
        "new_prefix": new,
        "new_prefix_exists": Path(new).is_dir(),
        "affected": len(items),
        "conflicts": conflicts,
        "restorable": sum(1 for i in items if i["target_exists"] and not i["conflict"]),
        "samples": items[:10],
        "items": items,
    }


def apply_relocate(old_prefix: str, new_prefix: str) -> dict:
    """执行前缀替换 + 指纹兜底。**只 UPDATE，不 DELETE**。

    - 目标路径存在且无冲突：直接改路径、清缺失标记、重挂到新目录；
    - 冲突：跳过并报告，交给下一次扫描的指纹重关联（P0-4）处理；
    - 目标文件不存在：仍改写路径（用户可能只是还没插盘），但保留缺失标记。
    """
    preview = preview_relocate(old_prefix, new_prefix)
    if not preview.get("ok"):
        return preview
    items = preview["items"]
    if not items:
        return {"ok": True, "moved": 0, "conflicts": 0, "kept_missing": 0,
                "new_prefix": preview["new_prefix"]}

    new_root = preview["new_prefix"]
    dir_id = None
    if Path(new_root).is_dir():
        for d in db.rows("SELECT id, path FROM directories"):
            if _under_prefix(new_root, d["path"]):
                dir_id = d["id"]
                break
        if dir_id is None:
            added = add_directory(new_root)
            dir_id = added.get("id")

    moved = conflicts = kept_missing = 0
    stamp = db.now()
    with db.session(immediate=True) as conn:
        for item in items:
            if item["conflict"]:
                conflicts += 1
                continue
            clear_missing = "missing_since = NULL" if item["target_exists"] else "missing_since = missing_since"
            if not item["target_exists"]:
                kept_missing += 1
            conn.execute(
                f"UPDATE media_files SET path = ?, filename = ?, "
                f"dir_id = COALESCE(?, dir_id), {clear_missing}, updated_at = ? WHERE id = ?",
                (item["new_path"], Path(item["new_path"]).name, dir_id, stamp, item["id"]),
            )
            moved += 1
    return {"ok": True, "moved": moved, "conflicts": conflicts,
            "kept_missing": kept_missing, "dir_id": dir_id,
            "new_prefix": new_root}


def dismiss_missing(file_ids: list[int] | None = None) -> dict:
    """用户确认后隐藏「缺失」文件：软删（``deleted_at``），不真删行。

    只处理 ``missing_since`` 非空的行，绝不碰播放进度与观看历史：
    硬盘重新插上后扫描到文件会自动清掉这两个标记，记录照旧可用。
    """
    ids = [int(i) for i in (file_ids or []) if str(i).strip().isdigit()]
    if not ids:
        return {"ok": False, "error": "没有选中任何文件"}
    marks = ",".join("?" for _ in ids)
    affected = db.execute(
        f"UPDATE media_files SET deleted_at = COALESCE(deleted_at, ?) "
        f"WHERE missing_since IS NOT NULL AND id IN ({marks})",
        tuple([db.now()] + ids),
    )
    return {"ok": True, "dismissed": int(affected or 0)}


def scan(directory_id: int | None = None, progress_cb=None) -> dict:
    """增量扫描：已存在的文件只更新 size/mtime，不重复插入；结束即刻匹配中文名。"""
    db.init_db()
    target = "SELECT * FROM directories WHERE enabled = 1 AND id = ?" if directory_id else \
             "SELECT * FROM directories WHERE enabled = 1"
    params = (directory_id,) if directory_id else ()
    directories = db.rows(target, params)

    added = updated = skipped = missing_dirs = 0
    for directory in directories:
        root = Path(directory["path"])
        if not root.exists():
            # 目录暂时不可达（移动硬盘未插、网络盘掉线）：只标「缺失」保留历史，
            # 不删记录、不动播放进度；下次能读到文件时自动清除标记。
            _mark_missing(directory["id"])
            missing_dirs += 1
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
                "SELECT id, size, mtime, deleted_at, tmdb_id, tvdb_id, missing_since, "
                "episode_source FROM media_files WHERE path = ?",
                (str(path),)
            )
            if existing:
                changed = (existing["size"] != stat.st_size
                           or abs((existing["mtime"] or 0) - stat.st_mtime) > 1)
                # 文件从回收站恢复 / 重新下载后，清掉「本地文件已移除」标记；
                # 观看进度、历史与看过的列表始终不动。
                if existing["deleted_at"]:
                    db.execute("UPDATE media_files SET deleted_at = NULL WHERE id = ?",
                               (existing["id"],))
                # 文件重新可见（硬盘插回来了 / 路径重定位成功）→ 清掉「缺失」标记
                if existing["missing_since"]:
                    db.execute("UPDATE media_files SET missing_since = NULL WHERE id = ?",
                               (existing["id"],))
                # 老库升级后首次扫描：把文件名里的作品 ID 补上（只在原来为空时补，不改已有值）
                if info["tmdb_id"] and not existing["tmdb_id"]:
                    db.execute("UPDATE media_files SET tmdb_id = ?, tvdb_id = COALESCE(tvdb_id, ?) "
                               "WHERE id = ?",
                               (info["tmdb_id"], info["tvdb_id"], existing["id"]))
                # 老库补「集数来源/可信度」（同样只在原来为空时写）。
                # 用户手工确认过的行 episode_source='user'，这里绝不会覆盖。
                if not existing["episode_source"] and info["episode_source"]:
                    db.execute(
                        "UPDATE media_files SET episode_source = ?, episode_confidence = ? "
                        "WHERE id = ?",
                        (info["episode_source"], info["episode_confidence"], existing["id"]),
                    )
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
                        "title_guess, season_no, episode_no, year, tmdb_id, tvdb_id, "
                        "episode_source, episode_confidence) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            directory["id"], str(path), path.name, stat.st_size, stat.st_mtime,
                            path.stem, info["title_guess"], info["season_no"], info["episode_no"],
                            info["year"], info["tmdb_id"], info["tvdb_id"],
                            info["episode_source"], info["episode_confidence"],
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
            "directories": len(directories), "missing_dirs": missing_dirs,
            "matched_cn": match_result.get("matched", 0),
            "match_total": match_result.get("total", 0),
            "match_online": match_result.get("online_available", False)}
