"""M3：详细池代码解析（唯一实现）与锚的确定性单测。"""

from __future__ import annotations

import dataclasses

from dragon_quant.review_account.prep.collectors import PrepContext, _collect_stock_daily
from dragon_quant.review_account.prep.review import CONFIG, derive_anchors, derive_pool_codes


def _assets(**overrides):
    base = {
        "dragons": {"rows": [{"code": "600001", "name": "龙头A"}]},
        "ths_limit_up_pool": {
            "total": 3,
            "rows": [
                {"code": "600001", "name": "龙头A", "high_days": "4天4板"},
                {"code": "600002", "name": "跟风B", "high_days": "2天2板"},
                {"code": "600003", "name": "首板C", "high_days": "1天1板"},
            ],
        },
        "ths_block_top": {
            "rows": [
                {"name": "机器人", "limit_up_num": 5,
                 "stock_list": [{"code": "600001"}, {"code": "600004"}]},
                {"name": "算力", "limit_up_num": 3,
                 "stock_list": [{"code": "600002"}]},
                {"name": "消费", "limit_up_num": 2,
                 "stock_list": [{"code": "600003"}]},
                {"name": "第四", "limit_up_num": 1,
                 "stock_list": [{"code": "600005"}]},
            ]
        },
        "fg_boards": {
            "boards": {
                "昨日涨停": {"status": "ok", "rows": [
                    {"code": "600002", "close": 9.4, "pre_close": 10.0},
                    {"code": "600006", "close": 8.5, "pre_close": 10.0},
                    {"code": "600007", "close": 9.8, "pre_close": 10.0},
                ]},
            }
        },
        "holdings": {"codes": ["600008"]},
    }
    base.update(overrides)
    return base


def test_four_sources_union_and_deterministic():
    assets = _assets()
    first = derive_pool_codes(assets)
    second = derive_pool_codes(assets)
    assert first == second

    codes = first["codes"]
    # dragons / 涨停池 / 前 3 主线成员 / 正负锚 / holdings 全部并入
    for code in ("600001", "600002", "600003", "600004", "600006", "600007", "600008"):
        assert code in codes
    assert "600005" not in codes  # 第 4 个主线不在前 3
    assert first["sources"]["dragons"] == ["600001"]
    assert first["sources"]["mainlines"] == ["600001", "600004", "600002", "600003"]
    assert first["sources"]["holdings"] == ["600008"]
    assert first["truncated"] is False


def test_exclusions_st_new_bse_suspended():
    assets = _assets()
    assets["ths_limit_up_pool"]["rows"].extend([
        {"code": "600100", "name": "ST样例", "high_days": "1天1板"},
        {"code": "600101", "name": "次新", "high_days": "1天1板", "listing_days": 5},
        {"code": "830001", "name": "北交所", "high_days": "1天1板"},
        {"code": "600102", "name": "停牌", "high_days": "1天1板", "is_suspended": True},
    ])
    pool = derive_pool_codes(assets)
    assert "600100" not in pool["codes"]
    assert "600101" not in pool["codes"]
    assert "830001" not in pool["codes"]
    assert "600102" not in pool["codes"]
    assert pool["excluded"] == {"st": 1, "new": 1, "bse": 1, "suspended": 1}


def test_truncation_follows_source_priority_and_sets_flag():
    assets = _assets()
    config = dataclasses.replace(CONFIG, review_max_codes=2)
    pool = derive_pool_codes(assets, config)
    assert pool["truncated"] is True
    # 优先级：dragons → 涨停池 → 主线 → 锚 → 持仓
    assert pool["codes"] == ["600001", "600002"]


def test_anchors_positive_highest_board_and_negative_worst():
    anchors = derive_anchors(_assets())
    assert anchors["positive"] == [{"code": "600001", "reason": "市场最高 4 板"}]
    negative = [item["code"] for item in anchors["negative"]]
    assert negative == ["600006", "600002", "600007"]  # 按跌幅升序，正锚去重
    assert all("昨日涨停今日" in item["reason"] for item in anchors["negative"])


def test_stock_daily_collector_uses_the_same_pool_implementation():
    assets = _assets()
    calls = []

    class _FakeTdx:
        def batch_get_daily_klines(self, codes, days=120, end_date=None):
            calls.append(list(codes))
            return {code: [] for code in codes}

    ctx = PrepContext(
        trade_date="2026-10-19", today="2026-10-19", tdx=_FakeTdx(), ths=None,
        dragons_rows=assets["dragons"]["rows"], dragons_ready=True,
        collected=assets,
    )
    _collect_stock_daily(ctx)
    assert calls == [derive_pool_codes(assets)["codes"]]


def test_pool_mainlines_use_the_same_n_h_c_s_ranking_as_review():
    """涨停家数顺序与 N/H/C/S 顺序不同：池必须跟随 review.mainlines。"""

    from dragon_quant.review_account.prep.review.mainline import compute_mainlines

    assets = {
        "dragons": {"rows": [{"code": "600001"}]},
        "ths_limit_up_pool": {
            "total": 100, "truncated": False,
            "rows": [{"code": "600001", "high_days": "1天1板"}],
        },
        "ths_block_top": {
            "rows": [
                # 涨停家数第一（12），但高度/持续性一般
                {"name": "涨停家数高", "limit_up_num": 12, "high_num": 1,
                 "continuous_plate_num": 0,
                 "stock_list": [{"code": "600040"}]},
                # 涨停家数第二（10）
                {"name": "中位板块", "limit_up_num": 10, "high_num": 1,
                 "continuous_plate_num": 0,
                 "stock_list": [{"code": "600010"}]},
                # 涨停家数最少（1），但高度 6 板 + 连续 3 天 → 主线分进前 3
                {"name": "高度主线", "limit_up_num": 1, "high_num": 6,
                 "continuous_plate_num": 3,
                 "stock_list": [{"code": "600020"}]},
                # 按涨停家数会进前 3，但 N/H/C/S 排名被挤出
                {"name": "低位板块", "limit_up_num": 2, "high_num": 1,
                 "continuous_plate_num": 0,
                 "stock_list": [{"code": "600030"}]},
            ]
        },
        "holdings": {"codes": []},
    }

    mainlines = compute_mainlines(assets)
    # 前 3 主线（按主线分）应包含高度主线、排除低位板块
    top_names = [item["board_name"] for item in mainlines if item["is_mainline"]]
    assert "高度主线" in top_names
    assert "低位板块" not in top_names

    pool = derive_pool_codes(assets)
    assert "600020" in pool["sources"]["mainlines"]
    assert "600030" not in pool["sources"]["mainlines"]
    expected = sorted(
        code
        for item in mainlines if item["is_mainline"]
        for code in item["members_sample"]
    )
    assert sorted(pool["sources"]["mainlines"]) == expected

