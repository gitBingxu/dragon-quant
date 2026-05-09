"""
analyze — subprocess 入口（子进程单只打分，当前为桩）

用法:
  python -m dragon_quant.analyze <code> --shared-cache <path> [--json]
"""

import json
import sys
import argparse
from pathlib import Path
from dragon_quant.cache.data_cache import DataCache


def load_context(shared_path: str):
    cache = DataCache()
    with open(shared_path) as f:
        data = json.load(f)
    cache.load_snapshot(data)
    return cache


def main():
    parser = argparse.ArgumentParser(description="龙头战法单股分析（子进程入口）")
    parser.add_argument("code", help="股票代码")
    parser.add_argument("--shared-cache", required=True, help="共享缓存文件路径")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    cache = load_context(args.shared_cache)

    # ─── 桩：列出缓存中可用的数据 ───
    snapshot = cache.snapshot()
    kline_keys = [k for k in snapshot if "kline" in k]
    sector_keys = [k for k in snapshot if "sector" in k]
    quote_keys = [k for k in snapshot if "quotes" in k]

    result = {
        "code": args.code,
        "stage": "stub",
        "cache_stats": cache.stats(),
        "available_data": {
            "kline_keys": kline_keys[:10],
            "sector_keys": sector_keys[:10],
            "quote_keys": quote_keys[:10],
        },
        "dimensions": {
            "drive": None,
            "anti_drop": None,
            "leadership": None,
            "absorption": None,
        },
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"🧪 analyze stub: {args.code}")
        print(f"   cache: {cache.stats()}")
        print(f"   四维: 待实现")


if __name__ == "__main__":
    main()
