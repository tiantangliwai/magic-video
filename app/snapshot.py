"""把「看过 / 未看过」作品与全部封面导出成 Obsidian md 节点 + 带时间戳的 tar.gz 快照。

设计约束（本项目铁律）：
- **绝不写真实数据库**：用只读 URI 连库；剧集墙数据用 ``object.__new__(Api)``
  调纯查询方法，绕过 ``Api.__init__()`` 里的 ``init_db()`` / 词典初始化。
- **只写独立命名空间**：Obsidian 路径来自 ``settings``，校验为 vault 内相对路径，
  只用 ``os.replace`` 原子写自己托管的笔记，不删任何已有文件。
- **md 可持续积累**：每篇托管笔记只在 ``BEGIN AUTO / END AUTO`` 区块内更新，
  区块外的手写批注原样保留；索引的快照列表逐次追加。
- 图片按文件名去重复制，本机缓存优先；包按日期时间命名，只留本地不入库。

用法：::

    python scripts/export_cover_snapshot.py            # 生成节点 + 快照包
    python scripts/export_cover_snapshot.py --dry-run  # 只统计，不写任何文件
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import tarfile
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from . import config

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"
VAULT_FOLDER = "Magic Video"          # vault 内的笔记文件夹（settings.obsidian_folder）
NOTE_FOLDER = "07 封面积累"            # 本导出器的独立命名空间，绝不碰 00~06 号笔记
IMG_FOLDER = "封面图"
MANAGED = "<!-- managed-by: Magic Video snapshot v1 -->"
AUTO_BEGIN = "<!-- BEGIN AUTO -->"
AUTO_END = "<!-- END AUTO -->"
SNAP_BEGIN = "<!-- BEGIN SNAPSHOTS -->"
SNAP_END = "<!-- END SNAPSHOTS -->"


# --------------------------------------------------------------------------
# 纯数据逻辑（单测直接调用，不需要数据库 / vault）
# --------------------------------------------------------------------------
def display_name(row: dict) -> str:
    """看过的列表里用户看到的名字：title 优先，title_cn 兜底。"""
    return str(row.get("title") or row.get("title_cn") or "").strip() or "(未命名)"


def cover_for(names: Iterable[str], cover_map: dict[str, str]) -> str:
    """按候选名依次查封面；合成卡按 title_cn 展示，两种名字都要试。"""
    for name in names:
        key = str(name or "").strip()
        if key and cover_map.get(key):
            return cover_map[key]
    return ""


def rating_for(names: Iterable[str], meta: dict[str, dict]) -> dict:
    """评分 / 标签的 key 与看过的列表、剧集墙各自的实现保持一致。"""
    for name in names:
        key = f"watched::{str(name or '').strip()}"
        if key in meta:
            return meta[key]
    for name in names:
        key = f"series::guess::{str(name or '').strip()}"
        if key in meta:
            return meta[key]
    return {"rating": None, "tags": ""}


def group_watched(rows: list[dict]) -> list[dict]:
    """watched_list 按作品折叠：一部作品一条，多季多条记录合并展示。"""
    groups: OrderedDict[str, dict] = OrderedDict()
    for row in rows:
        name = display_name(row)
        g = groups.setdefault(name, {
            "title": name, "ids": [], "titles": [], "cn": [], "seasons": [],
            "episodes": [], "notes": [], "sources": [], "count": 0,
        })
        g["count"] += 1
        g["ids"].append(int(row.get("id") or 0))
        for key, bucket in (("title", "titles"), ("title_cn", "cn"),
                            ("note", "notes"), ("source", "sources")):
            value = str(row.get(key) or "").strip()
            if value and value not in g[bucket]:
                g[bucket].append(value)
        season = row.get("season_no")
        if season and str(season) not in g["seasons"]:
            g["seasons"].append(str(season))
        lo, hi = row.get("episode_from"), row.get("episode_to")
        if lo:
            span = f"{lo}–{hi}" if hi and str(hi) != str(lo) else str(lo)
            if span not in g["episodes"]:
                g["episodes"].append(span)
    return list(groups.values())


def unseen_cards(cards: list[dict]) -> list[dict]:
    """本地有文件、后端判定为「未看」的剧集墙作品；合成卡不是本地文件，不算。"""
    return [c for c in cards
            if not c.get("readonly") and c.get("watch_state") == "none"]


def frontmatter(title: str, tags: str, generated_at: str) -> str:
    return (f"---\ntitle: {title}\ntags: [{tags}]\n"
            f'generated_at: "{generated_at}"\nsource: Magic Video\nmanaged: true\n---\n')


def auto_block(body: str) -> str:
    return f"{AUTO_BEGIN}\n{body.strip()}\n{AUTO_END}\n"


def img_tag(image: str, alt: str) -> str:
    """Obsidian 表格里用 HTML 限宽，markdown 原图会把表格撑爆。"""
    if not image:
        return "—"
    alt = alt.replace('"', "&quot;")
    return f'<img src="{IMG_FOLDER}/{image}" alt="{alt}" width="90">'


def render_list_note(title: str, tag: str, rows: list[dict], generated_at: str,
                     image_count: int) -> str:
    """生成一份清单笔记；区块外的内容（用户批注）由 merge_note 保留。"""
    with_cover = sum(1 for r in rows if r.get("cover"))
    body = [
        f"- 作品数：**{len(rows)}** · 有封面：**{with_cover}** · 图库总数：**{image_count}**",
        "- 数据来源：本机 `library.db`（只读）+ `D:/magicvideo/covers`，自动抓取与人工设定的封面一并收录。",
        "",
        "| 封面 | 作品 | 季 / 集数 | 评分 | 标签 | 备注 |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        seasons = "、".join(r.get("seasons") or [])
        episodes = "、".join(r.get("episodes") or [])
        meta = []
        if seasons:
            meta.append(f"{seasons} 季")
        if episodes:
            meta.append(f"E{episodes}")
        if r.get("file_count"):
            meta.append(f"本地 {r['file_count']} 集")
        rating = r.get("rating")
        tags = str(r.get("tags") or "").replace("|", "\\|")
        note = str(r.get("note") or "").replace("|", "\\|").replace("\n", " ")
        body.append("| {} | **{}** | {} | {} | {} | {} |".format(
            img_tag(r.get("cover", ""), r.get("title", "")),
            str(r.get("title", "")).replace("|", "\\|"),
            " · ".join(meta) or "—",
            f"★ {rating}" if rating else "—",
            tags or "—",
            note or "—",
        ))
    return frontmatter(title, tag, generated_at) + MANAGED + "\n\n# " + title + "\n\n" + auto_block("\n".join(body))


def append_snapshot(index: str, line: str, generated_at: str) -> str:
    """把本次节点追加到索引的 SNAPSHOTS 区块；历史节点与手写批注都不动。"""
    if SNAP_BEGIN in index and SNAP_END in index:
        head, rest = index.split(SNAP_BEGIN, 1)
        old, tail = rest.split(SNAP_END, 1)
        existing = old.rstrip("\n")
        updated = (existing + "\n" + line).strip("\n")
        return head + SNAP_BEGIN + "\n" + updated + "\n" + SNAP_END + tail
    base = frontmatter("封面积累索引", "magic-video, 封面积累, 索引", generated_at)
    return (base + MANAGED + "\n\n# 封面积累索引\n\n"
            "每次导出留下一个可回溯的节点；点进清单继续手写、补图即可，下次导出不会覆盖批注。\n\n"
            + SNAP_BEGIN + "\n" + line + "\n" + SNAP_END + "\n\n"
            "## 清单\n- [[看过清单]]\n- [[未看过清单]]\n")


def merge_note(existing: str, generated: str) -> str:
    """只替换 AUTO 区块；无托管标记的文件一律不碰（由调用方跳过）。"""
    if not existing.strip():
        return generated
    if (AUTO_BEGIN in existing and AUTO_END in existing
            and AUTO_BEGIN in generated and AUTO_END in generated):
        head, rest = existing.split(AUTO_BEGIN, 1)
        _old, tail = rest.split(AUTO_END, 1)
        new_body = generated.split(AUTO_BEGIN, 1)[1].split(AUTO_END, 1)[0]
        return head + AUTO_BEGIN + new_body + AUTO_END + tail
    return generated


def is_managed(text: str) -> bool:
    return MANAGED in text


# --------------------------------------------------------------------------
# 只读取数与文件写入
# --------------------------------------------------------------------------
def read_settings(vault: str | None, folder: str | None) -> tuple[Path, Path]:
    """从 settings 表只读取 vault 与笔记文件夹；两个都是可覆盖参数。"""
    uri = f"file:{config.DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        saved = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('obsidian_vault','obsidian_folder')")}
    finally:
        conn.close()
    vault_path = Path(str(vault or saved.get("obsidian_vault") or "").strip())
    note_folder = str(folder or saved.get("obsidian_folder") or VAULT_FOLDER).strip()
    if not vault_path.is_dir():
        raise ValueError(f"Obsidian vault 不存在：{vault_path}（可用 --vault 指定）")
    if (not note_folder or Path(note_folder).is_absolute() or ".." in Path(note_folder).parts
            or ":" in note_folder):
        raise ValueError("笔记文件夹必须是 vault 内的相对路径")
    return vault_path, Path(note_folder) / NOTE_FOLDER


def collect() -> dict[str, Any]:
    """绕过 Api.__init__（它会写库），只调用只读查询路径。"""
    from .api import Api                # 延迟导入：api 顶层可能正向导入本模块

    client = object.__new__(Api)
    wall = client.get_poster_wall({"source": "all", "limit": 2000})
    cover_map = wall.get("covers") or {}
    cards = wall.get("cards") or []
    uri = f"file:{config.DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        watched = [dict(r) for r in conn.execute(
            "SELECT id, title, title_cn, season_no, episode_from, episode_to, "
            "source, status, match_by, note, imported_at FROM watched_list "
            "ORDER BY title_cn, title, season_no")]
        meta = {r["work_key"]: {"rating": r["rating"], "tags": r["tags"] or ""}
                for r in conn.execute("SELECT work_key, rating, tags FROM work_meta")}
    finally:
        conn.close()

    watched_rows: list[dict] = []
    for g in group_watched(watched):
        names = [g["title"], *reversed(g["cn"]), *g["titles"]]
        m = rating_for(names, meta)
        watched_rows.append({**g, "cover": cover_for(names, cover_map),
                             "rating": m["rating"], "tags": m["tags"]})

    unseen_rows: list[dict] = []
    for card in unseen_cards(cards):
        m = rating_for([card.get("title", ""), card.get("work_title", "")], meta)
        unseen_rows.append({
            "title": card.get("title", ""), "cover": cover_map.get(card.get("title", ""), ""),
            "seasons": [str(card.get("season_count"))] if card.get("season_count", 0) > 1 else [],
            "episodes": [], "file_count": int(card.get("file_count") or 0),
            "note": str(card.get("search_keyword") or ""),
            "rating": m["rating"], "tags": m["tags"],
        })

    images: list[str] = set()
    if config.COVER_DIR.is_dir():
        images.update(p.name for p in config.COVER_DIR.glob("*.jpg"))
    if config.BUNDLED_COVER_DIR.is_dir():
        images.update(p.name for p in config.BUNDLED_COVER_DIR.glob("*.jpg"))
    return {"watched": watched_rows, "unseen": unseen_rows,
            "images": sorted(images), "meta_count": len(meta),
            "watched_records": len(watched)}


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".mvtmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def copy_images(names: list[str], dest: Path) -> int:
    """本机缓存优先；目标已有同大小文件就跳过，不删除任何已有图片。"""
    dest.mkdir(parents=True, exist_ok=True)
    copied = 0
    for name in names:
        target = dest / name
        if target.is_file() and target.stat().st_size > 0:
            continue
        for source in (config.COVER_DIR / name, config.BUNDLED_COVER_DIR / name):
            if source.is_file():
                shutil.copy2(source, target)
                copied += 1
                break
    return copied


def export(vault: str | None = None, folder: str | None = None,
           dry_run: bool = False) -> dict:
    data = collect()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    if dry_run:
        return {"ok": True, "dry_run": True, "generated_at": generated_at, **{
            k: (len(v) if isinstance(v, list) else v) for k, v in data.items()}}

    vault_path, notes_rel = read_settings(vault, folder)
    notes = vault_path / notes_rel
    images_dir = notes / IMG_FOLDER
    copied = copy_images(data["images"], images_dir)

    watched_note = render_list_note("看过清单", "magic-video, 封面积累, 看过",
                                    data["watched"], generated_at, len(data["images"]))
    unseen_note = render_list_note("未看过清单", "magic-video, 封面积累, 未看",
                                   data["unseen"], generated_at, len(data["images"]))
    for file, content in ((notes / "看过清单.md", watched_note),
                          (notes / "未看过清单.md", unseen_note)):
        old = file.read_text(encoding="utf-8") if file.is_file() else ""
        if old and not is_managed(old):
            continue                         # 绝不覆盖非本工具托管的用户笔记
        atomic_write(file, merge_note(old, content))

    package = DOCS / f"封面积累_{stamp}.tar.gz"
    line = (f"- {generated_at} · 看过 **{len(data['watched'])}** 部 / "
            f"未看 **{len(data['unseen'])}** 部 · 图片 **{len(data['images'])}** 张 · "
            f"快照 `{package.name}` · [[看过清单]] [[未看过清单]]")
    index_path = notes / "00 积累索引.md"
    old_index = index_path.read_text(encoding="utf-8") if index_path.is_file() else ""
    if old_index and not is_managed(old_index):
        raise ValueError(f"索引文件不是本工具托管，拒绝覆盖：{index_path}")
    atomic_write(index_path, append_snapshot(old_index, line, generated_at))

    manifest = {
        "generated_at": generated_at, "stamp": stamp, "package": package.name,
        "source_db": str(config.DB_PATH), "cover_dir": str(config.COVER_DIR),
        "counts": {"watched_works": len(data["watched"]),
                   "watched_records": data["watched_records"],
                   "unseen_works": len(data["unseen"]),
                   "images": len(data["images"]), "work_meta": data["meta_count"]},
        "watched": data["watched"], "unseen": data["unseen"], "images": data["images"],
    }
    atomic_write(notes / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    # docs 下的总索引：图片走相对路径，解压快照包或直接进 vault 都能点开
    docs_md = DOCS / "封面与观看清单.md"
    summary = frontmatter("封面与观看清单", "magic-video, 封面积累, 清单", generated_at)
    summary += MANAGED + "\n\n# 封面与观看清单\n\n" + auto_block(f"""
- 最近积累：**{generated_at}** · 看过 **{len(data['watched'])}** 部 / 未看 **{len(data['unseen'])}** 部 · 图片 **{len(data['images'])}** 张
- 完整清单（Obsidian）：`{notes_rel.as_posix()}/看过清单.md`、`未看过清单.md`
- 图片目录：`{notes_rel.as_posix()}/{IMG_FOLDER}/`（本次新增 {copied} 张）
- 快照包：`{package.name}`（只留本地，不进 git）
- 本次节点：{line.lstrip('- ')}

## 明细
- [[看过清单]]：看过的列表里每部作品一张封面（多季合并），含季数、集数范围、评分、标签与备注。
- [[未看过清单]]：本地有文件但还没有看过记录的剧集墙作品。
- 以后直接在 vault 的这两篇 md 上继续积累；再次运行本脚本只替换自动区块，手写批注保留。
""")
    old_summary = docs_md.read_text(encoding="utf-8") if docs_md.is_file() else ""
    if old_summary and not is_managed(old_summary):
        raise ValueError(f"docs 汇总笔记不是本工具托管，拒绝覆盖：{docs_md}")
    atomic_write(docs_md, merge_note(old_summary, summary))

    # 打包：md + manifest + 全部图片，路径带一层时间戳目录，解压不污染目标
    if package.exists():
        package.unlink()                    # 只删自己刚生成的同名快照
    with tarfile.open(package, "w:gz") as tar:
        for file in (notes / "00 积累索引.md", notes / "看过清单.md",
                     notes / "未看过清单.md", notes / "manifest.json"):
            if file.is_file():
                tar.add(file, arcname=f"{stamp}/{file.name}")
        tar.add(images_dir, arcname=f"{stamp}/{IMG_FOLDER}")
        tar.add(docs_md, arcname=f"{stamp}/docs-封面与观看清单.md")

    return {"ok": True, "generated_at": generated_at, "stamp": stamp,
            "package": str(package), "notes": str(notes), "copied_images": copied, **{
                k: len(v) if isinstance(v, list) else v for k, v in data.items()}}


def status() -> dict[str, Any]:
    """「后台进程」页只读展示：最近一次积累的时间、数量、包与笔记位置。"""
    result: dict[str, Any] = {"ok": True, "total": 0, "latest": None,
                              "generated_at": "", "counts": {}, "notes": "", "vault": ""}
    try:
        packages = sorted(DOCS.glob("封面积累_*.tar.gz"),
                          key=lambda p: p.stat().st_mtime_ns, reverse=True)
        result["total"] = len(packages)
        if packages:
            latest = packages[0]
            stat = latest.stat()
            result["latest"] = {
                "name": latest.name, "size": stat.st_size,
                "saved_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            }
    except OSError:
        pass
    try:
        vault, notes_rel = read_settings(None, None)
        notes = vault / notes_rel
        result["vault"], result["notes"] = str(vault), str(notes)
        manifest_file = notes / "manifest.json"
        if manifest_file.is_file():
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            result["generated_at"] = str(manifest.get("generated_at") or "")
            result["counts"] = manifest.get("counts") or {}
    except Exception as exc:                     # 没配置 vault / 包坏了都不能卡住页面
        result["hint"] = str(exc)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="导出看过的 / 未看过的作品与封面到 Obsidian")
    parser.add_argument("--vault", help="显式指定 Obsidian vault（默认读 settings）")
    parser.add_argument("--folder", help="vault 内笔记文件夹（默认读 settings）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    args = parser.parse_args()
    result = export(args.vault, args.folder, args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
