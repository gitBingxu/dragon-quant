"""
analyze — 子进程入口：加载共享缓存，对全部候选股四维打分

用法:
  python -m dragon_quant.analyze --shared-cache <path> [--json]
"""

import json
import sys
import argparse
from dragon_quant.cache.data_cache import DataCache
from dragon_quant.models.types import Candidate
from dragon_quant.scorers.drive import score as score_drive
from dragon_quant.scorers.anti_drop import score as score_anti_drop
from dragon_quant.scorers.leadership import score as score_leadership
from dragon_quant.scorers.absorption import score as score_absorption


SCORERS = [
    ("drive",      score_drive,      0.35),
    ("anti_drop",  score_anti_drop,  0.15),
    ("leadership", score_leadership, 0.25),
    ("absorption", score_absorption, 0.25),
]


def main():
    parser = argparse.ArgumentParser(description="龙头战法四维评分（子进程入口）")
    parser.add_argument("--shared-cache", required=True, help="共享缓存文件路径")
    parser.add_argument("--json", action="store_true", help="JSON 输出（默认开启）")
    args = parser.parse_args()

    # 加载共享缓存
    cache = DataCache()
    with open(args.shared_cache) as f:
        data = json.load(f)
    cache.load_snapshot(data)

    # 读取元数据
    candidates_raw = cache.get("__meta__:candidates") or []
    all_sector_codes = cache.get("__meta__:sector_codes") or []
    sector_name_map_raw = cache.get("__meta__:sector_name_map") or {}

    if not candidates_raw:
        print("[]")
        return

    # 重建候选股对象（用于 drive 的 peer_pool 构建）
    candidate_pool = [
        Candidate(
            code=c["code"], name=c["name"],
            concepts=c.get("concepts", []),
            primary_sector=c.get("primary_sector", ""),
            board_count=c.get("board_count", 0),
        )
        for c in candidates_raw
    ]

    # ─── 逐只打分 ───
    results = []
    for cand in candidates_raw:
        code = cand["code"]
        name = cand["name"]
        primary_sector = cand.get("primary_sector", "")

        dims = {}
        composite = 0.0

        for dim_name, score_fn, weight in SCORERS:
            try:
                kwargs = {"code": code, "cache": cache}
                if dim_name == "drive":
                    kwargs["candidate_pool"] = candidate_pool
                if dim_name in ("drive", "leadership", "absorption"):
                    kwargs["primary_sector"] = primary_sector
                if dim_name == "absorption":
                    kwargs["all_sector_codes"] = all_sector_codes
                    kwargs["sector_name_map"] = sector_name_map_raw

                sr = score_fn(**kwargs)
                dims[dim_name] = {
                    "score": sr.score,
                    "weight": sr.weight,
                    "details": sr.details,
                }
                composite += sr.score * sr.weight
            except Exception as e:
                print(f"  ⚠️ {dim_name} 打分异常 {code}: {e}", file=sys.stderr)
                dims[dim_name] = {
                    "score": 50.0,
                    "weight": weight,
                    "details": {"error": str(e)},
                }
                composite += 50.0 * weight

        results.append({
            "code": code,
            "name": name,
            "concepts": cand.get("concepts", []),
            "board_count": cand.get("board_count", 0),
            "primary_sector": primary_sector,
            "primary_sector_name": sector_name_map_raw.get(primary_sector, ""),
            "composite_score": round(composite, 2),
            "dimensions": dims,
        })

    # 输出
    output = json.dumps(results, ensure_ascii=False, indent=2)
    print(output)


if __name__ == "__main__":
    main()
