"""盘后复盘（prep）包门面：公共入口 + 历史 patch 目标的兼容层。

架构（M5B 最终边界）：
  - ``collectors``：8 个 collector、注册表、分类/健康判定与 collection_report 合并
    （「怎么采、采到什么算健康、report 怎么合并」）。
  - ``service``：日期/时点门禁、增量计划、采集→计算编排与 ``run_prep``
    （「什么时候能跑、这次采什么、采完怎么算」）。
  - ``compute``：情绪 / 主线 / 候选池 / 宏观的纯计算（只读 assets，无 I/O）。
  - ``rebuild``：dragons 五日窗口资产（镜像读取 + payload 构造）与 ``--recompute``
    本地重建（零采集零网络，不构造 provider）。

兼容约定：包拆分前 ``dragon_quant.review_account.prep.<name>`` 是模块属性，运维脚本
与测试通过 monkeypatch 修改它们（provider 类、``run_prep`` 内部入口、
``ASSET_COLLECTORS``、镜像读取函数等）。这些名字由本门面继续持有，子模块在调用期经
``rebuild.legacy`` 反向解析，因此 patch 依旧真实改变运行时行为，而不是留下无效别名。
依赖方向单向：``service → collectors → rebuild → compute``，门面只做绑定，不含逻辑。

公共导入 ``from dragon_quant.review_account.prep import PrepError, run_prep`` 保持不变。
"""

from __future__ import annotations

import time  # noqa: F401 — 旧 patch 目标：dragon_quant.review_account.prep.time.sleep

# 旧 patch 目标（模块属性）：provider 类 / 宏观采集 / 存储门面 / 镜像异常。
# 子模块一律经 rebuild.legacy 在调用期反查这些名字，静态重命名会静默让 patch 失效。
from dragon_quant.providers.macro import (  # noqa: F401
    YahooMacroSource, collect_macro,
)
from dragon_quant.providers.tdx import TdxProvider  # noqa: F401
from dragon_quant.providers.tencent import TencentProvider  # noqa: F401
from dragon_quant.providers.ths import THSProvider  # noqa: F401
from dragon_quant.providers.xueqiu import XueqiuProvider  # noqa: F401
from dragon_quant.storage import assets_db, dragons_mirror  # noqa: F401
from dragon_quant.storage.dragons_mirror import DragonsMirrorStale

from .collectors import (
    ASSET_COLLECTORS, CollectorSpec, PrepContext, _breadth_suspicious,
    _collect_all, _collect_cninfo, _collect_dragons, _collect_fund_flow,
    _collect_macro, _collect_market_breadth, _fund_flow_suspicious,
    _window_degradations, asset_health, collection_health, is_collectable,
    merge_report_entries, plan_repairs,
)
from .compute import (
    SHANGHAI, _lianban_count, compute_candidates, compute_macro,
    compute_main_boards, compute_sentiment, position_advice,
)
from .rebuild import (
    WINDOW_TRADING_DAYS, _read_dragon_day, _stale_dates, build_dragons_payload,
    rebuild_dragons_for_recompute,
)
from .service import (
    PrepError, PrepPlan, _compute_all, _run_collection, _validate_trade_date,
    plan_prep, resolve_window_dates, run_prep,
)

__all__ = [
    # 公共入口
    "PrepError",
    "run_prep",
    # 采集段（collectors；仅保留 M5A 已确立的兼容面，内部 helper 走 prep.collectors）
    "ASSET_COLLECTORS",
    "CollectorSpec",
    "PrepContext",
    "asset_health",
    "collection_health",
    "is_collectable",
    "merge_report_entries",
    "plan_repairs",
    "_breadth_suspicious",
    "_collect_all",
    "_collect_cninfo",
    "_collect_dragons",
    "_collect_fund_flow",
    "_collect_macro",
    "_collect_market_breadth",
    "_fund_flow_suspicious",
    "_window_degradations",
    # 编排段（service）
    "PrepPlan",
    "plan_prep",
    "resolve_window_dates",
    "_compute_all",
    "_run_collection",
    "_validate_trade_date",
    # 纯计算
    "compute_candidates",
    "compute_macro",
    "compute_main_boards",
    "compute_sentiment",
    "position_advice",
    # dragons 窗口资产 / recompute
    "WINDOW_TRADING_DAYS",
    "build_dragons_payload",
    "rebuild_dragons_for_recompute",
    "_read_dragon_day",
    "_stale_dates",
    # 兼容导出（旧模块属性：测试与运维仍直接引用）
    "DragonsMirrorStale",
    "SHANGHAI",
    "_lianban_count",
]
