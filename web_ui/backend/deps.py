"""只读仓储适配层（Ports & Adapters）。

每个 provider 函数内部 **lazy import** 既有仓储模块，导入 ``web_ui`` 不触库。
本模块只允许调用方案 §5.2 白名单 API；不新建连接、不写 SQL。
"""

from __future__ import annotations

import os

from starlette.requests import Request


def dragon_repository():
    from dragon_quant.scan.storage import dragons

    return dragons


def scan_repository():
    from dragon_quant.scan.storage import scans

    return scans


def account_repository():
    from dragon_quant.review_account import storage

    return storage


def evaluation_module():
    from dragon_quant.review_account import evaluation

    return evaluation


def run_account_backtest():
    from dragon_quant.review_account.main import run_review_account

    return run_review_account


def assets_repository():
    from dragon_quant.infra.assets import db as assets_db

    return assets_db


def factor_service():
    from dragon_quant.infra.factor import service

    return service


def factor_storage():
    from dragon_quant.infra.factor import storage

    return storage


def storage_paths():
    from dragon_quant.infra.storage import paths

    return paths


def benchmark_provider():
    """``/api/account/benchmark`` 的雪球 K 线 provider（既有行为）。"""
    from dragon_quant.infra.provider.xueqiu import XueqiuProvider

    return XueqiuProvider()


def kline_provider():
    """``/api/dragons/.../performance`` 的日 K provider（与账户基准解耦）。"""
    from dragon_quant.infra.provider.xueqiu import XueqiuProvider

    return XueqiuProvider()


def clock_module():
    """路由解析缺省 ``as_of`` 的时钟 seam（测试可直接 override）。"""
    from dragon_quant.infra import clock

    return clock


def scan_logs_repository():
    """扫描日志/摘要只读仓储（``scan_logs_v2``）。"""
    from dragon_quant.scan.storage import logs

    return logs


def decision_review_store():
    """决策回测只读快照 store（``prep_snapshots`` / ``auction_snapshots``）。"""
    from dragon_quant.infra.factor import decision_review_store

    return decision_review_store


def local_bars_loader():
    """竞价成效的本地日 K loader（只读 ``prep_assets.stock_daily``，不触网）。"""
    from dragon_quant.infra.assets import db as assets_db

    def load(codes, date_from, date_to):
        return assets_db.list_stock_daily_bars(codes, date_from, date_to)

    return load


def bar_calendar_loader():
    """本地已确认交易日 loader（``stock_daily`` bar 日期并集，只读不建库）。"""
    from dragon_quant.infra.assets import db as assets_db

    def load(date_from=None, date_to=None):
        return assets_db.list_stock_daily_bar_dates(date_from, date_to)

    return load


def review_assets_enabled() -> bool:
    """复盘资产开关（``DQ_REVIEW_ASSETS_ENABLED``，默认 true）。

    只用于日历 ``expected`` 判定，不改 prep 采集行为；不 import
    ``review_account.prep.collectors``（那会把 provider 拉进 Web 进程）。
    """
    raw = os.environ.get("DQ_REVIEW_ASSETS_ENABLED", "")
    return raw.strip().lower() not in ("0", "false", "no", "off")


def dashboard_settings(request: Request):
    """从 ``app.state`` 取 DashboardSettings（测试可直接 override）。"""
    return request.app.state.settings
