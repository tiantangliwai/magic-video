"""视频黑名单：名称包含任一关键词的视频在展示位置默认隐藏。

用户约定（2026-11-15）：
- 判定是「名称包含黑名单字段」，**不区分大小写**，不做分词或模糊相似度。
- 有独立开关，默认开启；关闭后所有位置恢复展示全部。
- 只影响「展示」：不删 ``media_files`` 行、不动播放进度 / 观看会话、
  也不从追剧清单里删条目（追剧清单是用户手写的待办，只过滤它的本地匹配结果）。

关键词来源是设置项 ``video_blacklist``，以逗号 / 顿号 / 分号 / 竖线 / 换行分隔。
短英文词（``jul`` / ``ipx`` / ``ure``）按包含匹配可能与正常片名撞车，
这是用户明确要的语义，改词表即可，代码不做二次猜测。
"""
from __future__ import annotations

import re

from . import db

_SPLIT_RE = re.compile(r"[,，、;；|\r\n]+")

# 只判断「名称」字段：路径不参与，否则目录名里的一个词会把整棵子树的正常作品隐藏。
# 注意 SQL 表达式要自己带别名前缀，不能写成 ``f.COALESCE(...)``（别名后不能接函数）。
_ROW_KEYS = ("filename", "title_guess", "title_cn", "display_title", "title", "raw_text")


def _sql_expressions(alias: str) -> tuple[str, ...]:
    return (
        f"{alias}.filename",
        f"COALESCE({alias}.title_guess, '')",
        f"COALESCE({alias}.title_cn, '')",
    )


def raw() -> str:
    # 不传 default：让 db.get_setting 回退到 config.DEFAULTS 里的默认词表，
    # 否则首次运行（settings 表还没这个键）会拿到空串，等于黑名单失效。
    return str(db.get_setting("video_blacklist") or "")


def keywords() -> list[str]:
    """切分、trim、转小写并去重；保留用户书写顺序，便于在设置页对照。"""
    out: list[str] = []
    seen: set[str] = set()
    for part in _SPLIT_RE.split(raw()):
        kw = part.strip().lower()
        if not kw or kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
    return out


def enabled() -> bool:
    # 同上：不传 default，空库时回退 DEFAULTS 的 1（默认开启）。
    value = str(db.get_setting("blacklist_enabled") or "").strip().lower()
    return value not in ("0", "false", "off", "no", "")


def active() -> bool:
    """开关打开**且**词表非空时才真正过滤。"""
    return enabled() and bool(keywords())


def hit(*names: object) -> bool:
    """任一名称包含任一关键词即命中；开关关闭时永远不命中。"""
    if not active():
        return False
    hay = "\n".join(str(n or "").lower() for n in names)
    if not hay.strip():
        return False
    return any(kw in hay for kw in keywords())


def filter_rows(rows, keys: tuple[str, ...] = _ROW_KEYS) -> list[dict]:
    """给已经查出来的行做 Python 侧过滤（列表总条数不大时才用）。"""
    items = list(rows or [])
    if not active():
        return items
    kws = keywords()
    out: list[dict] = []
    for r in items:
        hay = "\n".join(str(r.get(k) or "").lower() for k in keys)
        if not any(kw in hay for kw in kws):
            out.append(r)
    return out


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def sql_clause(alias: str = "f") -> tuple[str, list]:
    """返回 ``(SQL 片段, 参数)``：名称命中任一关键词的行被排除。

    片段形如 ``NOT ((lower(f.filename) LIKE ? ESCAPE '\\' OR ...) OR (...))``，
    直接拼在 ``WHERE`` 后面即可；开关关闭或词表为空时返回 ``("", [])``。
    """
    if not active():
        return "", []
    kws = keywords()
    fields = _sql_expressions(alias)
    parts: list[str] = []
    params: list[str] = []
    for kw in kws:
        like = f"%{_escape_like(kw)}%"
        ors = " OR ".join(f"lower({expr}) LIKE ? ESCAPE '\\'" for expr in fields)
        parts.append(f"({ors})")
        params.extend([like] * len(fields))
    return "NOT (" + " OR ".join(parts) + ")", params


def status() -> dict:
    """供设置页 / 测试读取的只读快照。"""
    kws = keywords()
    return {"enabled": enabled(), "count": len(kws), "keywords": kws}
