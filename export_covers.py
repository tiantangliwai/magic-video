#!/usr/bin/env python3
"""把本地封面缓存导出成仓库资产 ``assets/covers/``，供打包分发与 PR 更新。

为什么要有这一步：封面原本只存在 ``<数据目录>/covers/`` 里，属于「用户自己的
缓存」，换机器或新装就全没了，别人也没法贡献。导出后它变成仓库里受版本控制
的资产：

- 打包时随 ``--add-data assets:assets`` 一起进安装包，新装即有一批封面；
- 运行期 ``covers.local_cover()`` 在本机缓存缺失时自动从资产里补一份（离线，不联网）；
- 其他人想换某部剧的封面，改 ``assets/covers/<文件名>`` 并同步 index 里的
  ``sha256`` / ``bytes``，提 PR 即可。

用法::

    .venv/Scripts/python.exe export_covers.py               # 本机缓存 → assets
    .venv/Scripts/python.exe export_covers.py --dry-run      # 只看会改什么
    .venv/Scripts/python.exe export_covers.py --refresh-index # 只重算哈希与体积
    .venv/Scripts/python.exe export_covers.py --include-manual # 连手工封面一起导出

默认**不导出** ``source='manual'``（用户自己选的图）与**不删除**资产里已有的文件：
前者避免「清除封面」后被资产复活，后者避免把别人 PR 进来的封面误删。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ASSET_DIR = ROOT / "assets" / "covers"
INDEX_PATH = ASSET_DIR / "index.json"
SCHEMA = 1
MIN_BYTES = 512


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_index() -> dict:
    """读现有 index.json；坏文件按空处理，不阻断导出。"""
    if not INDEX_PATH.is_file():
        return {"schema": SCHEMA, "covers": []}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": SCHEMA, "covers": []}
    if not isinstance(data, dict) or not isinstance(data.get("covers"), list):
        return {"schema": SCHEMA, "covers": []}
    return data


def normalise_entries(covers: list[dict]) -> list[dict]:
    """按文件名去重，保留先出现的那条（手工维护的字段不被覆盖）。"""
    seen: dict[str, dict] = {}
    for item in covers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("file") or "").strip()
        if not name:
            continue
        seen.setdefault(name, item)
    return list(seen.values())


def local_covers(include_manual: bool) -> list[dict]:
    """从 ``subjects`` 取本机已落库的封面（标题 + 文件名 + 来源）。"""
    from app import config, db  # 延迟导入：脚本可能在没有 venv 时被 --help 调用

    sql = (
        "SELECT COALESCE(NULLIF(TRIM(title_cn),''), NULLIF(TRIM(title_orig),'')) AS title, "
        "title_orig, cover_local, cover_url, source, updated_at "
        "FROM subjects WHERE COALESCE(cover_local,'') <> ''"
    )
    if not include_manual:
        sql += " AND COALESCE(source,'') <> 'manual'"
    sql += " ORDER BY id"
    out = []
    for row in db.rows(sql):
        title = str(row.get("title") or "").strip()
        name = str(row.get("cover_local") or "").strip()
        if not title or not name:
            continue
        path = config.COVER_DIR / name
        try:
            if not path.is_file() or path.stat().st_size < MIN_BYTES:
                continue
        except OSError:
            continue
        aliases = []
        for alias in (title, str(row.get("title_orig") or "").strip()):
            alias = alias.strip()
            if alias and alias not in aliases:
                aliases.append(alias)
        out.append({
            "file": name,
            "title": title,
            "aliases": aliases,
            "source": str(row.get("source") or ""),
            "cover_url": str(row.get("cover_url") or ""),
            "updated_at": str(row.get("updated_at") or ""),
            "_src": path,
        })
    return out


def sync(include_manual: bool, dry_run: bool) -> dict:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    index = load_index()
    existing = {str(i.get("file")): i for i in normalise_entries(index.get("covers") or [])}
    added = updated = same = 0
    for entry in local_covers(include_manual):
        name = entry["file"]
        dest = ASSET_DIR / name
        src: Path = entry.pop("_src")
        digest = sha256_of(src)
        old = existing.get(name)
        old_digest = (old or {}).get("sha256")
        unchanged = dest.is_file() and old_digest == digest
        if not unchanged:
            if not dry_run:
                shutil.copy2(src, dest)
            if old is None:
                added += 1
            else:
                updated += 1
            entry["sha256"] = digest
            entry["bytes"] = src.stat().st_size
            entry["updated_at"] = entry["updated_at"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        else:
            same += 1
            entry["sha256"] = digest
            entry["bytes"] = src.stat().st_size
            if old:
                entry["updated_at"] = old.get("updated_at") or entry["updated_at"]
        existing[name] = entry

    # 资产里存在、但本机 subjects 不认识的（别人 PR 进来的、或本机没抓过的）：
    # 保留原条目，只在缺 sha256 时补算，绝不删除。
    for name, item in list(existing.items()):
        path = ASSET_DIR / name
        if not path.is_file():
            # 文件不在了：保留条目（可能是别人正在改），只是别写假哈希
            continue
        if not item.get("sha256"):
            item["sha256"] = sha256_of(path)
        if not item.get("bytes"):
            item["bytes"] = path.stat().st_size
        if not item.get("title"):
            item["title"] = path.stem

    covers = sorted(existing.values(), key=lambda i: (str(i.get("title") or ""), str(i.get("file") or "")))
    for item in covers:
        item.pop("_src", None)
    payload = {
        "schema": SCHEMA,
        "note": "封面资产清单：替换图片后请同步本条的 sha256 / bytes / updated_at，可用 export_covers.py --refresh-index 自动重算",
        "count": len(covers),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "covers": covers,
    }
    if not dry_run:
        INDEX_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return {"added": added, "updated": updated, "unchanged": same, "total": len(covers)}


def refresh_index(dry_run: bool) -> dict:
    """只按磁盘上的实际文件重算 sha256 / bytes / count，不改标题与图片。"""
    index = load_index()
    covers = normalise_entries(index.get("covers") or [])
    fixed = 0
    for item in covers:
        path = ASSET_DIR / str(item.get("file") or "")
        if not path.is_file():
            continue
        digest = sha256_of(path)
        size = path.stat().st_size
        if item.get("sha256") != digest or item.get("bytes") != size:
            item["sha256"] = digest
            item["bytes"] = size
            fixed += 1
    payload = dict(index)
    payload["schema"] = SCHEMA
    payload["count"] = len(covers)
    payload["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    payload["covers"] = sorted(covers, key=lambda i: (str(i.get("title") or ""), str(i.get("file") or "")))
    if not dry_run:
        INDEX_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return {"fixed": fixed, "total": len(covers)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出封面到 assets/covers（仓库资产）")
    parser.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    parser.add_argument("--include-manual", action="store_true", help="连 source='manual' 的手工封面一起导出")
    parser.add_argument("--refresh-index", action="store_true", help="只按磁盘文件重算 sha256/bytes")
    args = parser.parse_args(argv)

    if args.refresh_index:
        result = refresh_index(args.dry_run)
        print(f"[index] 重算 {result['fixed']} 条 / 共 {result['total']} 条"
              + ("（dry-run 未写入）" if args.dry_run else ""))
        return 0

    result = sync(args.include_manual, args.dry_run)
    print(f"[covers] 新增 {result['added']} · 更新 {result['updated']} · 未变 {result['unchanged']}"
          f" · 资产共 {result['total']} 条" + ("（dry-run 未写入）" if args.dry_run else ""))
    print(f"清单：{INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
