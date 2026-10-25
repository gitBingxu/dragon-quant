"""storage 命令：本地持久化数据的状态、体积与清理。"""

from __future__ import annotations

from dragon_quant.storage.manager import StorageManager


def handle(args, services):
    """存储管理命令。"""
    mgr = StorageManager()

    if args.storage_action == "status":
        s = mgr.status()
        print(f"数据根目录: {s['data_dir']}")
        print(f"{'目录':10s} {'文件数':>6s} {'大小':>8s}")
        print("-" * 28)
        for key in ("cookies", "cache", "logs", "results", "shared"):
            d = s[key]
            if d["exists"]:
                print(f"{key:10s} {d['files']:6d} {d['size']:>8s}")
            else:
                print(f"{key:10s}  (不存在)")
        # 数据库文件（clear --all 不清理，仅展示）
        print(f"{'数据库':10s} {'':>6s} {'大小':>8s}")
        print("-" * 28)
        for key in ("dragon_db", "assets_db"):
            d = s[key]
            print(f"{key:10s} {'':>6s} {d['size']:>8s}" if d["exists"]
                  else f"{key:10s}  (不存在)")
        legacy = s["legacy_prep_db"]
        if legacy["exists"]:
            print(f"legacy_prep_db (历史库，不再读写，确认 assets.db 有数据后可删除) "
                  f"{legacy['size']}")

    elif args.storage_action == "clear":
        if args.all:
            r = mgr.clear_all()
            for k, v in r.items():
                if k == "protected_databases":
                    continue
                print(f"  清理 {k}: {v} 个文件")
            if r.get("protected_databases"):
                print("  保留数据库（不清理）:")
                for path in r["protected_databases"]:
                    note = "（历史库，确认 assets.db 有数据后可删除）" \
                        if path.endswith("prep.db") else ""
                    print(f"    {path}{note}")
        else:
            if args.cache:
                n = mgr.clear_cache()
                print(f"  清理 cache: {n} 个文件")
            if args.results:
                n = mgr.clear_results(days=args.days)
                print(f"  清理 results: {n} 个文件")
                if args.days:
                    print(f"    (保留最近 {args.days} 天)")
            if args.logs:
                n = mgr.clear_logs(days=args.days)
                print(f"  清理 logs: {n} 个文件")
                if args.days:
                    print(f"    (保留最近 {args.days} 天)")

    elif args.storage_action == "size":
        s = mgr.size()
        print(f"总占用: {s['total']}")
        from dragon_quant.storage.manager import _fmt_size
        for k, b in s["by_dir"].items():
            print(f"  {k}: {_fmt_size(b)}")
