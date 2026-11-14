"""命令行工具：初始化、导入、扫描、中文名匹配、自启动管理。

    python cli.py init
    python cli.py import seen.txt
    python cli.py scan
    python cli.py status
    python cli.py match          # 重新匹配所有未识别标题
    python cli.py dict           # 查看词典统计
    python cli.py import-alias-file
    python cli.py autostart on|off
    python cli.py daemon
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app import autostart, config, db, importer, metadata, potplayer, scanner  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Magic Video 命令行工具")
    parser.add_argument("command", choices=[
        "init", "import", "scan", "status", "match", "dict", "import-alias-file",
        "autostart", "daemon", "player",
    ])
    parser.add_argument("arg", nargs="?", default="")
    args = parser.parse_args()

    db.init_db()
    metadata.ensure_builtin_loaded()

    if args.command == "init":
        table_count = db.scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
        print(f"数据库已就绪：{config.DB_PATH}")
        print(f"表数量：{table_count}")
        print(json.dumps(metadata.stats(), ensure_ascii=False))
    elif args.command == "import":
        path = Path(args.arg)
        if not path.exists():
            print(f"文件不存在：{path}")
            return 1
        preview = importer.preview(path.read_text(encoding="utf-8-sig"))
        result = importer.commit(preview["rows"], source="cli")
        print(f"解析 {preview['total']} 条，其中重复 {preview['duplicates']} 条")
        print(f"导入 {result['inserted']} 条，跳过 {result['skipped']} 条")
        print(f"导入时自动匹配中文名 {preview['match']['matched']} 条"
              f"（离线词典 {preview['match']['by_builtin']} 条 / 在线 {preview['match']['by_online']} 条）")
        if not preview["match"]["online_available"]:
            print("提示：在线源当前不可达，已使用离线词典；可在设置里配置代理后重试。")
    elif args.command == "scan":
        result = scanner.scan()
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "status":
        stats = db.row(
            "SELECT (SELECT COUNT(*) FROM media_files) AS files, "
            "(SELECT COUNT(*) FROM watched_list) AS watched, "
            "(SELECT COUNT(*) FROM watch_sessions) AS sessions"
        )
        print(json.dumps(stats, ensure_ascii=False))
        print(json.dumps(potplayer.read_status(), ensure_ascii=False))
        print(json.dumps(metadata.stats(), ensure_ascii=False))
    elif args.command == "match":
        result = metadata.rematch_all()
        print(json.dumps({k: v for k, v in result.items() if k != "results"}, ensure_ascii=False))
    elif args.command == "dict":
        print(json.dumps(metadata.stats(), ensure_ascii=False, indent=2))
    elif args.command == "import-alias-file":
        print(json.dumps(metadata.import_alias_file(), ensure_ascii=False))
    elif args.command == "autostart":
        result = autostart.enable() if args.arg.lower() in ("on", "1", "true", "yes") else autostart.disable()
        print(json.dumps(result, ensure_ascii=False))
    elif args.command == "player":
        print(json.dumps(potplayer.read_status(), ensure_ascii=False, indent=2))
    elif args.command == "daemon":
        from daemon import main as daemon_main
        daemon_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
