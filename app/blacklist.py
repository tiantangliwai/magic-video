"""视频黑名单：名称包含任一关键词的视频在展示位置默认隐藏。

用户约定（2026-11-15）：
- 判定是「名称包含黑名单字段」，**不区分大小写**，不做分词或模糊相似度。
- 有独立开关，默认开启；关闭后所有位置恢复展示全部。
- 只影响「展示」：不删 ``media_files`` 行、不动播放进度 / 观看会话、
  也不从追剧清单里删条目（追剧清单是用户手写的待办，只过滤它的本地匹配结果）。

关键词来源是设置项 ``video_blacklist``，以逗号 / 顿号 / 分号 / 竖线 / 换行分隔。
短英文词（``jul`` / ``ipx`` / ``ure``）按包含匹配可能与正常片名撞车，
这是用户明确要的语义，改词表即可，代码不做二次猜测。

匹配分两段（2026-11-15 用户反馈「剧集墙还能看到黑名单剧集」后修正）：

1. SQL 粗筛（:func:`sql_clause`）：``lower(字段) LIKE '%词%'``，走数据库一次扫表，
   便宜但**对连接符敏感**——词表里的 ``FC2PPV`` 匹配不到文件名里的 ``FC2-PPV``。
2. Python 精确过滤（:func:`hit` / :func:`filter_rows`）：两侧都去掉
   ``- _ . · 空格`` 再比，能命中 ``FC2-PPV-1288053``、``mvsd.naiad``。
   另外目录名派生的分组标题（剧集墙卡片标题、媒体库树分组名）只能在这一层判断，
   因为文件名里可能根本没这个词。
"""
from __future__ import annotations

import re

from . import config, db

_SPLIT_RE = re.compile(r"[,，、;；|\r\n]+")

# 归一化时要丢掉的连接符：片名里 `FC2-PPV-1288053`、`mvsd.naiad`、`SSIS_001`
# 和用户词表里的 `fc2ppv` / `mvsd.naiad` 写法不统一，只做包含比较会整片漏掉。
_SEP_CHARS = " -_.·　"

# 只判断「名称」字段：路径不参与，否则目录名里的一个词会把整棵子树的正常作品隐藏。
# 注意 SQL 表达式要自己带别名前缀，不能写成 ``f.COALESCE(...)``（别名后不能接函数）。
_ROW_KEYS = ("filename", "title_guess", "title_cn", "display_title", "title", "raw_text")


def normalize_name(text: object) -> str:
    """小写 + 去掉连接符，用于「名称包含」比较（两侧都要过一遍）。"""
    low = str(text or "").lower()
    return "".join(ch for ch in low if ch not in _SEP_CHARS)


def _sql_expressions(alias: str) -> tuple[str, ...]:
    return (
        f"lower({alias}.filename)",
        f"lower(COALESCE({alias}.title_guess, ''))",
        f"lower(COALESCE({alias}.title_cn, ''))",
    )


def raw() -> str:
    # 不传 default：让 db.get_setting 回退到 config.DEFAULTS 里的默认词表，
    # 否则首次运行（settings 表还没这个键）会拿到空串，等于黑名单失效。
    return str(db.get_setting("video_blacklist") or "")


# 词表 / 开关的进程内快照。命中判断会被「每个分组、每一行」反复调用，
# 每次都读一次 settings 等于每次开一条 SQLite 连接，剧集墙 190 个分组要 2 秒。
# 失效条件是 db.write_count()：任何写库（含保存设置）都会让它 +1。
_STATE: dict = {"version": -1, "keywords": [], "norm": [], "enabled": True}


def _state() -> dict:
    # 键里必须带库路径：测试会反复切到临时库，只靠写计数会把上一个库的词表带过来。
    version = (str(config.DB_PATH), db.write_count())
    if _STATE["version"] != version:
        words: list[str] = []
        seen: set[str] = set()
        for part in _SPLIT_RE.split(str(db.get_setting("video_blacklist") or "")):
            word = part.strip().lower()
            if not word:
                continue
            key = normalize_name(word)
            if not key or key in seen:
                continue
            seen.add(key)
            words.append(word)
        flag = str(db.get_setting("blacklist_enabled") or "").strip().lower()
        _STATE.update(
            version=version,
            keywords=words,
            norm=[normalize_name(w) for w in words],
            enabled=flag not in ("0", "false", "off", "no", ""),
        )
    return _STATE


def keywords() -> list[str]:
    """切分、trim、转小写并去重；保留用户书写顺序，便于在设置页对照。

    这里返回的是**原样小写词**（不动连接符），给 SQL 的 LIKE 用；
    归一化后的词由 :func:`norm_keywords` 提供。
    """
    return list(_state()["keywords"])


def norm_keywords() -> list[str]:
    """去掉连接符的关键词，供 :func:`hit` 与 Python 侧过滤使用。"""
    return list(_state()["norm"])


def enabled() -> bool:
    # 空库时回退 DEFAULTS 的 1（默认开启）。
    return bool(_state()["enabled"])


def active() -> bool:
    """开关打开**且**词表非空时才真正过滤。"""
    state = _state()
    return bool(state["enabled"]) and bool(state["keywords"])



def hit(*names: object) -> bool:
    """任一名称包含任一关键词即命中；开关关闭时永远不命中。

    两侧都过 :func:`normalize_name`，所以 `FC2-PPV-1288053` 能被词表里的
    `FC2PPV` 命中——这是用户反馈「剧集墙还能看到黑名单剧集」的直接原因。
    """
    if not active():
        return False
    hay = normalize_name("\n".join(str(n or "") for n in names))
    if not hay.strip():
        return False
    return any(kw in hay for kw in norm_keywords())


def filter_rows(rows, keys: tuple[str, ...] = _ROW_KEYS) -> list[dict]:
    """给已经查出来的行做 Python 侧过滤（列表总条数不大时才用）。"""
    items = list(rows or [])
    if not active():
        return items
    kws = norm_keywords()
    out: list[dict] = []
    for r in items:
        hay = normalize_name("\n".join(str(r.get(k) or "") for k in keys if k in r))
        if not any(kw in hay for kw in kws):
            out.append(r)
    return out


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def sql_clause(alias: str = "f") -> tuple[str, list]:
    """返回 ``(SQL 片段, 参数)``：名称命中任一关键词的行被排除。

    片段形如 ``NOT ((lower(f.filename) LIKE ? ESCAPE '\\' OR ...) OR (...))``，
    直接拼在 ``WHERE`` 后面即可；开关关闭或词表为空时返回 ``("", [])``。

    **这是粗筛**：连接符不同的写法（``FC2-PPV`` vs ``FC2PPV``）会漏，
    调用方拿到行之后还要过一遍 :func:`filter_rows` / :func:`hit`。
    不在这里做 replace 归一化，是因为 38 个词 × 3 个字段的 OR 展开会让
    SQLite 逐词重算 replace 链（实测 2100 行要多花 110ms，媒体库每次搜索都卡）。
    """
    if not active():
        return "", []
    kws = keywords()
    fields = _sql_expressions(alias)
    parts: list[str] = []
    params: list[str] = []
    for kw in kws:
        like = f"%{_escape_like(kw)}%"
        ors = " OR ".join(f"{expr} LIKE ? ESCAPE '\\'" for expr in fields)
        parts.append(f"({ors})")
        params.extend([like] * len(fields))
    return "NOT (" + " OR ".join(parts) + ")", params


def status() -> dict:
    """供设置页 / 测试读取的只读快照。"""
    kws = keywords()
    return {"enabled": enabled(), "count": len(kws), "keywords": kws}


def invalidate() -> None:
    """丢弃词表快照，下一次判断重新读设置（测试与改设置后可用）。"""
    _STATE["version"] = None
