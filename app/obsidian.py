"""把项目的「流程与结果」自动沉淀到本机 Obsidian vault。

设计原则（对应 AGENTS.md 的「不修改用户文件」约束）：

1. 只在 vault 下新建一个独立命名空间文件夹（默认 ``Magic Video/``），
   绝不读写、重命名、删除命名空间之外的任何笔记。
2. 每篇托管笔记都带 ``managed-by`` 标记；遇到同名但没有标记的文件一律跳过，
   避免覆盖用户自己写的内容。
3. 自动内容放在 ``BEGIN AUTO / END AUTO`` 区块内，区块外的「我的批注」
   在每次同步时原样保留 —— 用户可以直接在 Obsidian 里写想法而不怕被冲掉。
4. 开发日志按天追加、按事件指纹去重；历史结果同时落一份到 data/，
   Obsidian 笔记即使被删也能重建。

本模块只依赖标准库 + 本项目的 db/config，不引入第三方包。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from . import __version__, config, db, metadata, potplayer, runtime

DEFAULT_FOLDER = "Magic Video"
MARKER = "<!-- managed-by: Magic Video v1 -->"
BEGIN_AUTO = "<!-- BEGIN AUTO -->"
END_AUTO = "<!-- END AUTO -->"
BEGIN_LOG = "<!-- BEGIN LOG -->"
END_LOG = "<!-- END LOG -->"
HUMAN_SECTION = (
    "\n## 我的批注（同步不会覆盖）\n\n"
    "> 在这里记录想法、决策和待办；下次同步只替换上方 AUTO 区块，本段会原样保留。\n"
)

NOTE_FILES = {
    "overview": "00 项目总览.md",
    "changelog": "01 版本记录.md",
    "roadmap": "02 优化路线图.md",
    "testing": "03 自测报告.md",
    "gaps": "04 功能缺口与决策.md",
    "cheatsheet": "05 使用速查.md",
}
LOG_DIR_NAME = "06 开发日志"

SETTINGS_KEYS = ("obsidian_vault", "obsidian_folder", "obsidian_auto_sync")

# 真实存在的项目决策，供「功能缺口与决策」笔记使用（来源：AGENTS.md / CHANGELOG.md）
DECISIONS = [
    ("界面形态", "原生独立窗口 + 网页界面（pywebview / WebView2），不做浏览器标签页、不用纯 PyQt", "用户选择；兼顾观感与开发量"),
    ("中文名补全", "只在导入 / 扫描 / 手动重匹配时同步完成，内置离线词典优先，无定时任务", "用户明确要求；本机直连 Bangumi 超时"),
    ("进度回读", "ctypes 发 WM_USER 消息读 PotPlayer，不装插件、不改播放器设置、不开 HTTP 服务", "实测可行且零侵入"),
    ("数据存储", "SQLite WAL 单文件，data/ 不入库；短事务 + busy_timeout", "本地优先、易备份"),
    ("用户视频文件", "扫描只读，绝不重命名 / 移动 / 写 .nfo 到视频目录", "避免污染媒体库"),
    ("观看历史", "标记未看 / 重置进度只清续播点，不删 watch_sessions", "对齐 Plex unscrobble 语义"),
]

# 当前已知缺口（真实状态，不是设想）
KNOWN_GAPS = [
    "三阈值完成判定尚未可配置（当前单一 complete_percent）",
    "JSON 备份 / 导出尚未实现，换机换盘缺少一键迁移",
    "路径失效后缺少重定位向导（当前主键仍是文件路径）",
    "清单导入与本地文件的「稳定作品 ID 对齐」尚未实现",
    "海报墙、观看时长统计、按季分组尚未实现",
    "Bangumi 在线源在本机直连超时，需代理才能真正实时补全",
    "GitHub 远程仓库未推送（本机无 gh CLI / 凭证）",
    "外部打开的视频需先把所在目录加入扫描库才能续播",
]


# --------------------------------------------------------------------------- #
# vault 探测与配置
# --------------------------------------------------------------------------- #
def obsidian_config_path() -> Path:
    override = os.environ.get("MAGICVIDEO_OBSIDIAN_CONFIG")
    if override:
        return Path(override)
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "obsidian" / "obsidian.json"


def detect_vaults(config_path: str | Path | None = None) -> list[dict]:
    """从 Obsidian 主配置读出所有已注册 vault；只读，不创建任何东西。"""
    path = Path(config_path) if config_path else obsidian_config_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    result = []
    for vault_id, info in (data.get("vaults") or {}).items():
        if not isinstance(info, dict) or not info.get("path"):
            continue
        raw = str(info["path"])
        result.append({
            "id": vault_id,
            "path": raw,
            "exists": Path(raw).is_dir(),
            "open": bool(info.get("open")),
            "last_open_ts": info.get("ts"),
        })
    return result


def configured_vault() -> str:
    return (db.get_setting("obsidian_vault", "") or "").strip()


def configured_folder() -> str:
    return (db.get_setting("obsidian_folder", DEFAULT_FOLDER) or DEFAULT_FOLDER).strip() or DEFAULT_FOLDER


def auto_sync_enabled() -> bool:
    return str(db.get_setting("obsidian_auto_sync", "0")).strip() in ("1", "true", "on", "yes")


def default_vault() -> str:
    """未显式配置时：只有一个存在的 vault 才自动采用；多个时要求用户点选，避免误写错库。"""
    existing = [v["path"] for v in detect_vaults() if v["exists"]]
    return existing[0] if len(existing) == 1 else ""


def safe_folder(name: str) -> str:
    """文件夹名必须是 vault 内的单层相对路径，禁止绝对路径与 .. 穿越。"""
    value = (name or DEFAULT_FOLDER).strip().replace("\\", "/")
    if not value or value.startswith("/") or re.match(r"^[A-Za-z]:", value):
        raise ValueError("笔记文件夹必须是 vault 内的相对路径")
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts) or not parts:
        raise ValueError("笔记文件夹名不合法")
    return "/".join(parts)


def resolve_vault(explicit: str | Path | None = None, *, folder: str | None = None) -> dict:
    """返回 {ok, vault, notes_root}；不创建目录（由 sync 负责）。"""
    candidate = str(explicit).strip() if explicit else (configured_vault() or default_vault())
    if not candidate:
        return {"ok": False, "error": "检测到多个 Obsidian vault，请先在设置里指定要写入哪一个"}
    vault = Path(candidate).expanduser()
    if not vault.is_dir():
        return {"ok": False, "error": f"vault 不存在或不是目录：{vault}"}
    try:
        rel = safe_folder(folder if folder is not None else configured_folder())
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    notes_root = (vault / rel).resolve()
    # 二次校验：解析后仍必须位于 vault 内
    try:
        notes_root.relative_to(vault.resolve())
    except ValueError:
        return {"ok": False, "error": "笔记目录越出 vault 范围，已拒绝写入"}
    return {"ok": True, "vault": vault, "notes_root": notes_root, "folder": rel}


def set_options(vault: str | None = None, folder: str | None = None,
                auto_sync: bool | int | str | None = None) -> dict:
    db.init_db()
    if vault is not None and str(vault).strip():
        p = Path(str(vault).strip()).expanduser()
        if not p.is_dir():
            return {"ok": False, "error": f"vault 不存在：{p}"}
        db.set_setting("obsidian_vault", str(p))
    if folder is not None and str(folder).strip():
        try:
            rel = safe_folder(str(folder))
        except ValueError as exc:
            return {"ok": False, "error": str(exc)}
        db.set_setting("obsidian_folder", rel)
    if auto_sync is not None:
        db.set_setting("obsidian_auto_sync", 1 if str(auto_sync).lower() in ("1", "true", "on", "yes") else 0)
    return {"ok": True, **status()}


def status() -> dict:
    db.init_db()
    configured = configured_vault()
    vaults = detect_vaults()
    resolved = resolve_vault()
    notes_root = resolved.get("notes_root")
    return {
        "ok": True,
        "version": __version__,
        "configured_vault": configured,
        "auto_detected_vault": default_vault(),
        "vaults": vaults,
        "folder": configured_folder(),
        "auto_sync": auto_sync_enabled(),
        "notes_root": str(notes_root) if notes_root else "",
        "notes_exist": bool(notes_root and notes_root.is_dir()),
        "note_count": len(list(notes_root.glob("*.md"))) if notes_root and notes_root.is_dir() else 0,
        "last_sync": db.get_setting("obsidian_last_sync", "") or "",
        "last_test": load_last_test(),
        "resolvable": bool(resolved.get("ok")),
        "error": resolved.get("error", ""),
    }


# --------------------------------------------------------------------------- #
# 数据快照
# --------------------------------------------------------------------------- #
def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(config.APP_DIR), *args],
            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
        )
        return (out.stdout or "").strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def git_info() -> dict:
    if runtime.is_frozen():
        # 安装包里没有 .git 目录，用版本号代替提交信息，笔记不留空
        return {
            "commit": "", "subject": "打包运行版", "committed_at": "",
            "branch": "release", "tag": f"v{__version__}",
            "recent": [], "dirty": False,
        }
    head = _git("log", "-1", "--format=%h|%s|%ci")
    parts = head.split("|", 2) if head else []
    return {
        "commit": parts[0] if parts else "",
        "subject": parts[1] if len(parts) > 1 else "",
        "committed_at": parts[2] if len(parts) > 2 else "",
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "tag": _git("tag", "--points-at", "HEAD"),
        "recent": [line for line in _git("log", "-5", "--format=%h %s").splitlines() if line],
        "dirty": bool(_git("status", "--porcelain")),
    }


def test_result_path() -> Path:
    return config.DATA_DIR / "test_last.json"


def load_last_test(path: str | Path | None = None) -> dict | None:
    p = Path(path) if path else test_result_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def parse_roadmap(path: str | Path | None = None) -> list[dict]:
    """从 docs/OPTIMIZATION.md 提取 P0–P3 建议，保证路线图与仓库文档单一真源。"""
    p = Path(path) if path else config.APP_DIR / "docs" / "OPTIMIZATION.md"
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    items: list[dict] = []
    priority = ""
    for line in text.splitlines():
        m = re.match(r"^###\s*(P\d)\b", line.strip())
        if m:
            priority = m.group(1)
            continue
        if not priority or not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        item = {
            "priority": priority,
            "id": int(cells[0]),
            "title": cells[1],
            "module": cells[2] if len(cells) >= 5 else "",
            "effort": cells[3] if len(cells) >= 5 else "",
            "value": cells[4] if len(cells) >= 5 else (cells[2] if len(cells) >= 3 else ""),
        }
        items.append(item)
    return items


def _plain(text: str) -> str:
    """把表格里的强调、代码块标记压成 Obsidian 里易读的一行。"""
    return re.sub(r"\s+", " ", str(text).replace("**", "").replace("`", "")).strip()


def collect_snapshot() -> dict:
    db.init_db()
    metadata.ensure_builtin_loaded()
    try:
        stats = db.row(
            "SELECT (SELECT COUNT(*) FROM media_files) AS files, "
            "(SELECT COUNT(*) FROM watched_list) AS watched, "
            "(SELECT COUNT(*) FROM watch_sessions) AS sessions, "
            "(SELECT COUNT(*) FROM watch_progress) AS progress, "
            "(SELECT COUNT(*) FROM watch_progress WHERE completed = 0 AND position_ms > 0) AS in_progress, "
            "(SELECT COUNT(*) FROM watch_progress WHERE completed = 1) AS completed, "
            "(SELECT COUNT(*) FROM directories) AS directories"
        ) or {}
    except Exception:
        stats = {}
    try:
        dictionary = metadata.stats()
    except Exception:
        dictionary = {}
    try:
        player = potplayer.read_status()
    except Exception:
        player = {"available": False}
    try:
        changelog = (config.APP_DIR / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        changelog = ""
    return {
        "version": __version__,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "time": datetime.now().strftime("%H:%M:%S"),
        "app_dir": str(config.APP_DIR),
        "python": sys.version.split()[0],
        "stats": stats,
        "dictionary": dictionary,
        "player": player,
        "git": git_info(),
        "test": load_last_test(),
        "roadmap": parse_roadmap(),
        "changelog": changelog,
        "settings": db.all_settings(),
    }


# --------------------------------------------------------------------------- #
# Markdown 渲染
# --------------------------------------------------------------------------- #
def _link(name: str) -> str:
    return f"[[{Path(name).stem}]]"


def _frontmatter(title: str, tag: str, snap: dict) -> str:
    return (
        "---\n"
        f"title: {title}\n"
        f"tags: [magic-video, {tag}]\n"
        f"version: {snap['version']}\n"
        f"generated_at: \"{snap['generated_at']}\"\n"
        "source: Magic Video\n"
        "managed: true\n"
        "---\n"
    )


def _refresh_frontmatter(text: str, snap: dict) -> str:
    """只更新版本与时间两行，用户新增的 aliases / tags / cssclasses 原样保留。"""
    text = re.sub(r"^version:.*$", f"version: {snap['version']}", text, count=1, flags=re.M)
    text = re.sub(r"^generated_at:.*$", f'generated_at: "{snap["generated_at"]}"',
                  text, count=1, flags=re.M)
    return text


def _write_managed(path: Path, title: str, tag: str, body: str, snap: dict) -> str:
    """创建或更新托管笔记；返回 created / updated / unchanged / skipped_unmanaged。"""
    existed = path.exists()
    if existed:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return "skipped_unreadable"
        if MARKER not in text:
            return "skipped_unmanaged"  # 用户的同名笔记，绝不覆盖
        b, e = text.find(BEGIN_AUTO), text.find(END_AUTO)
        if b < 0 or e < 0 or e < b:
            return "skipped_no_block"
        new_text = _refresh_frontmatter(
            text[: b + len(BEGIN_AUTO)] + "\n" + body.strip() + "\n" + text[e:], snap
        )
        if new_text == text:
            return "unchanged"
    else:
        new_text = (
            _frontmatter(title, tag, snap)
            + f"{MARKER}\n# {title}\n\n"
            + f"{BEGIN_AUTO}\n{body.strip()}\n{END_AUTO}\n"
            + HUMAN_SECTION
        )
    _atomic_write(path, new_text)
    return "updated" if existed else "created"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".mvtmp")
    try:
        tmp.write_text(text, encoding="utf-8", newline="\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()  # 只删自己刚创建的临时文件
            except OSError:
                pass


def _metric_line(snap: dict) -> str:
    s = snap["stats"]
    d = snap["dictionary"]
    t = snap["test"] or {}
    return (
        f"| 指标 | 值 |\n|---|---|\n"
        f"| 版本 | `v{snap['version']}` |\n"
        f"| 生成时间 | {snap['generated_at']} |\n"
        f"| 媒体文件 | {s.get('files', 0)} |\n"
        f"| 看过的清单 | {s.get('watched', 0)} |\n"
        f"| 观看会话 | {s.get('sessions', 0)} |\n"
        f"| 进行中 / 已看完 | {s.get('in_progress', 0)} / {s.get('completed', 0)} |\n"
        f"| 扫描目录 | {s.get('directories', 0)} |\n"
        f"| 中文对照词条 | {d.get('alias_total', 0)}（未匹配 {d.get('unmatched', 0)}） |\n"
        f"| 单元测试 | {t.get('total', '未运行')} 个 · 失败 {t.get('failures', '-')} · 错误 {t.get('errors', '-')} |\n"
        f"| 当前提交 | `{snap['git'].get('commit') or '无'}` {snap['git'].get('subject', '')} |\n"
        f"| PotPlayer | {'运行中' if snap['player'].get('available') else '未运行（不影响记录功能）'} |\n"
    )


def _nav(snap: dict) -> str:
    return (
        "## 笔记索引\n\n"
        + "\n".join(f"- {_link(name)}" for name in NOTE_FILES.values())
        + f"\n- `{LOG_DIR_NAME}/YYYY-MM-DD.md`（开发日志，按天追加）\n"
    )


def render_overview(snap: dict) -> str:
    g = snap["git"]
    recent = "\n".join(f"  - `{line}`" for line in g.get("recent", [])) or "  - （未获取到 git 记录）"
    return (
        f"> 由 `cli.py obsidian sync` / 设置页按钮自动生成，区块外批注永久保留。\n\n"
        f"## 状态快照\n\n{_metric_line(snap)}\n"
        f"## 这是什么\n\n"
        f"纯本地的播放历史与断点续播工具：pywebview 原生窗口 + SQLite + PotPlayer "
        f"`WM_USER` 消息回读。看过的清单、扫描目录、播放进度、中文名匹配全部落库；\n"
        f"本笔记集用于**沉淀开发流程与自测结果**，方便后续优化与补齐功能。\n\n"
        f"## 仓库与流程\n\n- 项目目录：`{snap['app_dir']}`\n"
        f"- 分支 `{g.get('branch') or '未知'}` · 标签 `{g.get('tag') or '无'}`"
        f"{' · 工作区有未提交改动' if g.get('dirty') else ' · 工作区干净'}\n"
        f"- 最近提交：\n{recent}\n\n"
        f"## 下一步（来自 [[02 优化路线图]]）\n\n"
        f"1. 三阈值完成判定可配置\n2. JSON 备份导出\n3. 路径失效重定位向导\n\n"
        f"{_nav(snap)}"
    )


def render_changelog(snap: dict) -> str:
    body = snap["changelog"].strip() or "（未找到 CHANGELOG.md）"
    # 去掉首行标题避免与本笔记标题重复
    body = re.sub(r"^#\s+.*\n", "", body, count=1)
    return (
        f"> 镜像仓库 `CHANGELOG.md`，随每次同步自动更新；tag 以 git 为准。\n\n"
        f"当前版本 `v{snap['version']}` · 当前 tag `{snap['git'].get('tag') or '无'}`\n\n"
        f"---\n\n{body}\n\n---\n\n{_nav(snap)}"
    )


def render_roadmap(snap: dict) -> str:
    items = snap["roadmap"]
    lines = [
        "> 直接来自 `docs/OPTIMIZATION.md`，勾选状态请在 **我的批注** 区记录；"
        "AUTO 区块只反映文档内容，避免手改被覆盖。\n",
        "> 竞品：Jellyfin / Plex / Emby / Kodi / Shoko / tinyMediaManager / FileBot / VLC / MPC-HC。\n",
    ]
    current = ""
    for item in items:
        if item["priority"] != current:
            current = item["priority"]
            lines.append(f"\n### {current}\n")
        tail = ""
        if item["module"]:
            tail += f" · `{_plain(item['module'])}`"
        if item["effort"]:
            tail += f" · 改动量 {_plain(item['effort'])}"
        lines.append(f"- [ ] **#{item['id']} {_plain(item['title'])}**{tail}")
    lines.append(f"\n共 {len(items)} 条建议。完整依据、来源链接与避坑清单见仓库 `docs/OPTIMIZATION.md`。\n")
    lines.append(f"\n{_nav(snap)}")
    return "\n".join(lines)


def history_path() -> Path:
    return config.DATA_DIR / "obsidian_history.json"


def _read_history() -> list[dict]:
    p = history_path()
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("events", []) if isinstance(data, dict) else []
    except (OSError, ValueError):
        return []


def _append_history(snap: dict, reason: str) -> list[dict]:
    t = snap["test"] or {}
    event = {
        "timestamp": snap["generated_at"],
        "version": snap["version"],
        "commit": snap["git"].get("commit", ""),
        "reason": reason,
        "test_total": t.get("total", 0),
        "test_failures": t.get("failures", 0),
        "test_errors": t.get("errors", 0),
        "success": bool(t) and not t.get("failures") and not t.get("errors"),
        "files": snap["stats"].get("files", 0),
        "sessions": snap["stats"].get("sessions", 0),
        "aliases": snap["dictionary"].get("alias_total", 0),
    }
    events = _read_history()
    if events and all(events[-1].get(k) == v for k, v in event.items() if k != "timestamp"):
        return events  # 同一状态重复同步不新增历史行
    events.append(event)
    events = events[-200:]
    p = history_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"events": events}, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return events


def render_testing(snap: dict, history: list[dict]) -> str:
    t = snap["test"] or {}
    if t:
        modules = t.get("modules") or {}
        module_lines = "\n".join(f"| `{k}` | {v} |" for k, v in sorted(modules.items()))
        latest = (
            "## 最近一次单元测试\n\n"
            f"| 项 | 值 |\n|---|---|\n"
            f"| 运行时间 | {t.get('timestamp', '未知')} |\n"
            f"| 版本 | `v{t.get('version', snap['version'])}` |\n"
            f"| 用例总数 | {t.get('total', 0)} |\n"
            f"| 通过 | {t.get('passed', 0)} |\n"
            f"| 失败 / 错误 / 跳过 | {t.get('failures', 0)} / {t.get('errors', 0)} / {t.get('skipped', 0)} |\n"
            f"| 耗时 | {t.get('duration_sec', 0)} 秒 |\n"
            f"| 结论 | {'✅ 全绿' if t.get('success') else '❌ 未全绿'} |\n\n"
            f"<details><summary>各测试模块用例数</summary>\n\n"
            f"| 模块 | 用例数 |\n|---|---|\n{module_lines}\n\n</details>\n"
        )
    else:
        latest = "## 最近一次单元测试\n\n> 尚无 `data/test_last.json`，先跑 `python run_tests.py` 再同步。\n"

    table = ["| 时间 | 版本 | 提交 | 用例 | 失败/错误 | 结论 | 触发 |", "|---|---|---|---|---|---|---|"]
    for e in reversed(history[-15:]):
        table.append(
            f"| {e.get('timestamp', '')} | v{e.get('version', '')} | `{e.get('commit', '')}` | "
            f"{e.get('test_total', 0)} | {e.get('test_failures', 0)}/{e.get('test_errors', 0)} | "
            f"{'✅' if e.get('success') else '❌'} | {e.get('reason', '')} |"
        )
    return (
        f"> 结果来自 `run_tests.py` 写出的 `data/test_last.json` 与本地历史快照，"
        f"不依赖 Obsidian 插件；详细覆盖矩阵见仓库 `docs/TESTING.md`。\n\n"
        f"{latest}\n## 结果时间线（最近 {min(len(history), 15)} 次）\n\n"
        + "\n".join(table)
        + f"\n\n## 手动复核命令\n\n```bash\npython run_tests.py\npython cli.py status\npython cli.py player\npython cli.py obsidian sync\n```\n\n"
        + _nav(snap)
    )


def render_gaps(snap: dict) -> str:
    gaps = "\n".join(f"- [ ] {g}" for g in KNOWN_GAPS)
    decisions = "\n".join(
        f"| **{name}** | {choice} | {why} |" for name, choice, why in DECISIONS
    )
    return (
        "> 本页是「后续优化与功能补齐」的入口：缺口按当前真实状态列出，"
        "决策记录（ADR 轻量版）说明为什么这么做，避免以后反复推翻。\n\n"
        f"## 待补齐清单\n\n{gaps}\n\n"
        f"## 优先级建议\n\n1. 先做 [[02 优化路线图]] 里的 P0 三项：三阈值、备份导出、路径重定位。\n"
        f"2. 再补「输入清单 → 与本地文件 / 播放历史自动对齐」，这是竞品集体空位。\n"
        f"3. 最后才是海报墙、统计、外部站点同步。\n\n"
        f"## 决策记录（ADR）\n\n| 决策 | 选择 | 理由 |\n|---|---|---|\n{decisions}\n\n"
        f"## 已知约束\n\n- 本机直连 `api.bgm.tv` / TMDB 超时，在线刮削必须快速降级。\n"
        f"- 外部打开的视频以窗口标题入库，需把所在目录加入扫描库才能续播。\n"
        f"- 本机无 gh CLI / GitHub 凭证，本地提交与 tag 尚未推送远程。\n\n"
        f"{_nav(snap)}"
    )


def render_cheatsheet(snap: dict) -> str:
    return (
        "> 常用命令速查；路径含空格时用引号包起来。\n\n"
        "## 启动\n\n"
        "- 双击 `start.bat` → 主界面\n"
        "- 双击 `start_daemon.bat` → 托盘常驻记录\n\n"
        "## 命令行（项目目录下）\n\n"
        "```bash\n"
        "python cli.py status                 # 库统计 + 播放器状态 + 词典\n"
        "python cli.py import seen.txt        # 导入看过的清单（当场匹配中文名）\n"
        "python cli.py scan                  # 扫描已添加目录\n"
        "python cli.py progress              # 查看全部断点进度\n"
        "python cli.py player                # 实时读取 PotPlayer\n"
        "python cli.py test                  # 全量单元测试，写 data/test_last.json\n"
        "python cli.py obsidian status       # 查看 vault 与笔记状态\n"
        "python cli.py obsidian sync         # 同步流程与结果到 Obsidian\n"
        "python cli.py obsidian path \"D:\\My Vault\"\n"
        "```\n\n"
        "## 本次同步\n\n"
        f"- 项目目录：`{snap['app_dir']}`\n"
        f"- 数据库：`data/library.db`（不在 git 中）\n"
        f"- 测试结果：`data/test_last.json`\n"
        f"- 历史快照：`data/obsidian_history.json`\n"
        f"- 版本：`v{snap['version']}` · Python `{snap['python']}`\n\n"
        f"{_nav(snap)}"
    )


# --------------------------------------------------------------------------- #
# 开发日志（按天追加 + 事件去重）
# --------------------------------------------------------------------------- #
def _event_key(snap: dict, reason: str, kind: str) -> str:
    t = snap["test"] or {}
    raw = "|".join([
        snap["date"], kind, reason, snap["version"],
        snap["git"].get("commit", ""), str(t.get("total", 0)),
        str(t.get("failures", 0)), str(snap["stats"].get("sessions", 0)),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _log_event_body(snap: dict, reason: str, kind: str, note: str) -> str:
    t = snap["test"] or {}
    g = snap["git"]
    s = snap["stats"]
    label = {
        "test": "单元测试与结果留痕",
        "sync": "流程与状态同步",
        "manual": "手动同步",
        "milestone": "播放里程碑",
    }.get(kind, "状态记录")
    test_line = (
        f"- 单元测试：`{t.get('passed', 0)}/{t.get('total', 0)}` 通过"
        f"（失败 {t.get('failures', 0)} / 错误 {t.get('errors', 0)}）"
        if t else "- 单元测试：本次未运行（可跑 `python cli.py test`）"
    )
    body = [
        f"### {snap['time']} · {label}",
        f"<!-- event:{_event_key(snap, reason, kind)} -->",
        f"- 版本 `v{snap['version']}` · 提交 `{g.get('commit') or '无'}` · 分支 `{g.get('branch') or '未知'}`",
        test_line,
        f"- 数据快照：媒体 {s.get('files', 0)} · 清单 {s.get('watched', 0)} · "
        f"会话 {s.get('sessions', 0)} · 断点 {s.get('progress', 0)} · "
        f"词典 {snap['dictionary'].get('alias_total', 0)}",
        f"- 触发：{reason}",
    ]
    if note and str(note).strip():
        body.append(f"- 备注：{str(note).strip()}")
    body.append(f"- 关联：{_link(NOTE_FILES['overview'])} · {_link(NOTE_FILES['testing'])} · "
                f"{_link(NOTE_FILES['roadmap'])}")
    return "\n".join(body)


def append_log(notes_root: Path, snap: dict, reason: str, kind: str, note: str = "") -> str:
    log_dir = notes_root / LOG_DIR_NAME
    path = log_dir / f"{snap['date']}.md"
    key = _event_key(snap, reason, kind)
    existed = path.exists()
    if existed:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return "skipped_unreadable"
        if MARKER not in text:
            return "skipped_unmanaged"
        if f"event:{key}" in text:
            return "duplicate"
        if END_LOG not in text:
            return "skipped_no_block"
        block = _log_event_body(snap, reason, kind, note)
        new_text = _refresh_frontmatter(
            text.replace(END_LOG, block + "\n\n" + END_LOG, 1), snap
        )
    else:
        header = (
            _frontmatter(f"开发日志 {snap['date']}", "devlog", snap)
            + f"{MARKER}\n# 开发日志 {snap['date']}\n\n"
            + "> 自动追加，同一状态不重复记录；区块外可自由写批注。\n\n"
            + f"{BEGIN_LOG}\n{_log_event_body(snap, reason, kind, note)}\n{END_LOG}\n"
            + HUMAN_SECTION
        )
        new_text = header
    _atomic_write(path, new_text)
    return "appended" if existed else "created"


# --------------------------------------------------------------------------- #
# 对外唯一入口
# --------------------------------------------------------------------------- #
def sync(vault: str | Path | None = None, folder: str | None = None,
         reason: str = "manual", kind: str | None = None, note: str = "") -> dict:
    """生成/更新全部笔记并返回同步报告。任何异常都转成 ok=False，不抛给界面。"""
    try:
        resolved = resolve_vault(vault, folder=folder)
        if not resolved["ok"]:
            return {"ok": False, "error": resolved["error"]}
        root: Path = resolved["notes_root"]
        root.mkdir(parents=True, exist_ok=True)
        snap = collect_snapshot()
        history = _append_history(snap, reason)
        event_kind = kind or ("test" if reason == "cli test" else "sync")

        bodies = {
            "overview": ("00 项目总览", "status", render_overview(snap)),
            "changelog": ("01 版本记录", "changelog", render_changelog(snap)),
            "roadmap": ("02 优化路线图", "roadmap", render_roadmap(snap)),
            "testing": ("03 自测报告", "testing", render_testing(snap, history)),
            "gaps": ("04 功能缺口与决策", "gaps", render_gaps(snap)),
            "cheatsheet": ("05 使用速查", "cheatsheet", render_cheatsheet(snap)),
        }
        written: dict[str, str] = {}
        for key, (title, tag, body) in bodies.items():
            written[NOTE_FILES[key]] = _write_managed(root / NOTE_FILES[key], title, tag, body, snap)
        log_result = append_log(root, snap, reason, event_kind, note)

        db.set_setting("obsidian_last_sync", snap["generated_at"])
        return {
            "ok": True,
            "vault": str(resolved["vault"]),
            "notes_root": str(root),
            "folder": resolved["folder"],
            "version": snap["version"],
            "generated_at": snap["generated_at"],
            "written": written,
            "created": sum(1 for v in written.values() if v == "created"),
            "updated": sum(1 for v in written.values() if v == "updated"),
            "unchanged": sum(1 for v in written.values() if v == "unchanged"),
            "skipped": {k: v for k, v in written.items() if v.startswith("skipped")},
            "log": log_result,
            "roadmap_items": len(snap["roadmap"]),
            "test": snap["test"],
            "commit": snap["git"].get("commit", ""),
        }
    except Exception as exc:  # 界面/CLI 永远不该因为同步失败而崩
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def open_notes_folder(vault: str | Path | None = None, folder: str | None = None) -> dict:
    resolved = resolve_vault(vault, folder=folder)
    if not resolved["ok"]:
        return {"ok": False, "error": resolved["error"]}
    root: Path = resolved["notes_root"]
    if not root.is_dir():
        return {"ok": False, "error": "尚未生成笔记，请先点「同步到 Obsidian」"}
    try:
        os.startfile(str(root))  # noqa: S606 - 仅 Windows，打开文件夹
        return {"ok": True, "path": str(root)}
    except (AttributeError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
