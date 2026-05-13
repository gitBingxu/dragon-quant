"""
CLI 入口 — python -m dragon_quant [options]
"""

import argparse
import sys

from dragon_quant.orchestrator import run_scan
from dragon_quant.storage.manager import StorageManager


def _cmd_storage(args):
    mgr = StorageManager()

    if args.action == "status":
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

    elif args.action == "clear":
        if args.all:
            r = mgr.clear_all()
            for k, v in r.items():
                print(f"  清理 {k}: {v} 个文件")
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

    elif args.action == "size":
        s = mgr.size()
        print(f"总占用: {s['total']}")
        for k, b in s["by_dir"].items():
            from dragon_quant.storage.manager import _fmt_size
            print(f"  {k}: {_fmt_size(b)}")


def main():
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--top", type=int, default=25, help="最终候选股数量 (默认25)")
    shared.add_argument("--candidates", type=int, default=5, help="每板块取前N只 (默认5)")
    shared.add_argument("--workers", type=int, default=2, help="并发线程数 (默认2)")

    parser = argparse.ArgumentParser(
        parents=[shared],
        description="龙头战法量化分析",
    )
    parser.set_defaults(command="scan")
    sub = parser.add_subparsers(dest="command")

    # scan 子命令
    scan_p = sub.add_parser("scan", help="批量扫描", parents=[shared])

    # storage 子命令
    st_p = sub.add_parser("storage", help="持久化数据管理")
    st_subs = st_p.add_subparsers(dest="action")

    st_subs.add_parser("status", help="查看存储状态")
    st_subs.add_parser("size", help="查看磁盘占用")

    clear_p = st_subs.add_parser("clear", help="清理数据")
    clear_p.add_argument("--all", action="store_true", help="清理全部(cache+results+logs)")
    clear_p.add_argument("--cache", action="store_true", help="清理缓存")
    clear_p.add_argument("--results", action="store_true", help="清理结果")
    clear_p.add_argument("--logs", action="store_true", help="清理日志")
    clear_p.add_argument("--days", type=int, default=None, help="保留最近N天")

    args = parser.parse_args()

    if args.command == "storage":
        _cmd_storage(args)
    else:
        run_scan(top_n=args.top, candidates_n=args.candidates, workers=args.workers)


if __name__ == "__main__":
    main()
