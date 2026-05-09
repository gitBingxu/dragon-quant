"""
CLI 入口 — python -m dragon_quant [options]
"""

import argparse
from dragon_quant.orchestrator import run_scan


def main():
    parser = argparse.ArgumentParser(description="🐉 龙头战法量化分析")
    parser.add_argument("--top", type=int, default=25, help="最终候选股数量 (默认25)")
    parser.add_argument("--candidates", type=int, default=5, help="每板块取前N只 (默认5)")
    parser.add_argument("--workers", type=int, default=2, help="并行子进程数 (默认2)")
    args = parser.parse_args()
    run_scan(top_n=args.top, candidates_n=args.candidates, workers=args.workers)


if __name__ == "__main__":
    main()
