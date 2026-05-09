#!/usr/bin/env python3
"""预加载共享数据，写 /tmp/lsa_YYYYMMDD.json。agent 编排工作流的第一步。"""

import sys
import os
import json
import glob
import time
from datetime import datetime, timedelta

from dragon_quant.eastmoney_api import get_limit_up_list, get_market_index_kline, get_api_calls_and_clear
from dragon_quant.persist_logger import PersistLogger

MAX_AGE_DAYS = 3
OUTPUT_DIR = "/tmp"


def cleanup():
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    for f in glob.glob(os.path.join(OUTPUT_DIR, "lsa_*.json")):
        try:
            if datetime.fromtimestamp(os.path.getmtime(f)) < cutoff:
                os.remove(f)
        except OSError:
            pass


def main():
    t_start = time.time()
    cleanup()

    logger = PersistLogger(log_dir="./logs", max_days=7)
    logger.session_start(command="preload.py", args=sys.argv[1:])

    t_stage = time.time()
    try:
        limit_up_list = get_limit_up_list()
    except Exception as e:
        print(f"lsa: 涨停榜获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
        logger.error_from_exc("get_limit_up_list", e)
        logger.session_end(total_elapsed_ms=(time.time() - t_start) * 1000,
                           success=0, failed=1)
        logger.close()
        sys.exit(1)

    api_stats = get_api_calls_and_clear()
    for s in api_stats:
        logger.api(
            name="get_limit_up_list" if "clist/get" in s["url"] else s["url"],
            elapsed_ms=s["elapsed_ms"], ok=s["ok"],
            attempts=s.get("attempts", 1),
            reason=s.get("reason", ""),
            last_http_status=s.get("last_http_status"),
            last_body_snippet=s.get("last_body_snippet"),
        )

    try:
        market_kline = get_market_index_kline("1.000001", 20)
    except Exception as e:
        print(f"lsa: 大盘K线获取失败 ({type(e).__name__}): {e}", file=sys.stderr)
        logger.error_from_exc("get_market_index_kline", e)
        logger.session_end(total_elapsed_ms=(time.time() - t_start) * 1000,
                           success=0, failed=1)
        logger.close()
        sys.exit(1)

    api_stats = get_api_calls_and_clear()
    for s in api_stats:
        logger.api(
            name="get_market_index_kline",
            elapsed_ms=s["elapsed_ms"], ok=s["ok"],
            attempts=s.get("attempts", 1),
            reason=s.get("reason", ""),
            last_http_status=s.get("last_http_status"),
            last_body_snippet=s.get("last_body_snippet"),
        )

    logger.stage("preload", elapsed_ms=(time.time() - t_stage) * 1000,
                 stocks=len(limit_up_list))

    if not limit_up_list:
        print("lsa: 涨停榜为空（可能非交易日），仍生成共享文件", file=sys.stderr)

    trading_date = datetime.now().strftime("%Y%m%d")
    if limit_up_list:
        td = limit_up_list[0].get("date", "")
        if td:
            trading_date = td

    data = {
        "generated_at": datetime.now().isoformat(),
        "trading_date": trading_date,
        "limit_up_list": limit_up_list,
        "market_kline": market_kline,
    }

    filepath = os.path.join(OUTPUT_DIR, f"lsa_{trading_date}.json")
    with open(filepath, "w") as f:
        json.dump(data, f, ensure_ascii=False, default=str)

    logger.session_end(total_elapsed_ms=(time.time() - t_start) * 1000,
                       success=1, failed=0,
                       top_scores=[])
    logger.close()

    print(f"lsa: 共享数据写入: {filepath}", file=sys.stderr)
    print(filepath)


if __name__ == "__main__":
    main()
