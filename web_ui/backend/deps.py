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
    """账户回测入口：Web 一律通过独立 worker 执行用户策略（HTTP 进程不导入用户代码）。"""
    from dragon_quant.review_account.main import run_review_account

    def run(**kwargs):
        return run_review_account(**kwargs, use_worker=True)

    return run


def run_incremental_account_backtest():
    """增量续算的惰性注入 seam（测试用 ``dependency_overrides`` 替换）。"""
    from dragon_quant.review_account.main import run_incremental_review_account

    def run(run_id, **kwargs):
        return run_incremental_review_account(run_id, use_worker=True, **kwargs)

    return run


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


def candidate_loader_factory():
    """实验用龙头池 loader 工厂（source → loader）；测试可 override。"""
    from dragon_quant.review_account.market import candidate_loader

    return candidate_loader


def prep_snapshot_reader():
    """实验用 T-1 prep 快照读取器（默认读活动 assets 仓储）。"""
    from dragon_quant.review_account.market import prep_snapshot_for

    return prep_snapshot_for


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


def bs_runner(request: Request):
    """从 ``app.state`` 取 BS 生成任务状态机（子进程委派，无 I/O）。"""
    return request.app.state.bs_runner


def review_runner(request: Request):
    """从 ``app.state`` 取龙头回测增量作业状态机（子进程委派，无 I/O）。"""
    return request.app.state.review_runner


def run_runner(request: Request):
    """从 ``app.state`` 取 scan / prep / auction 共享单飞作业（无 I/O）。"""
    return request.app.state.run_runner


def trade_calendar_probe(request: Request):
    """从 ``app.state`` 取交易日历探测（首次 ``lookup`` 才联网 + 按日缓存）。"""
    return request.app.state.trade_calendar


def run_done_probe():
    """当日是否已有扫描 / 复盘 / 竞价记录的只读探针（``create=False``）。

    返回 ``probe(job, trade_date) -> bool``；三个仓储读取都是既有只读投影，
    Web 层不新建连接、不写 SQL。
    """
    from dragon_quant.infra.assets import db as assets_db
    from dragon_quant.infra.storage import paths as storage_paths
    from dragon_quant.scan.storage import scans

    def probe(job: str, trade_date: str) -> bool:
        if job == "scan":
            # scans.list_scan_dates 会经 _connect() 建库建表；只读判定不应在页面
            # 轮询里顺手创建 dragon.db，因此库文件不存在时直接按「无记录」处理。
            if not storage_paths.DB_PATH.is_file():
                return False
            return bool(scans.list_scan_dates(trade_date, trade_date))
        if job == "prep":
            return bool(assets_db.list_prep_snapshot_dates(trade_date, trade_date))
        if job == "auction":
            return bool(assets_db.list_auction_snapshot_dates(trade_date, trade_date))
        raise ValueError(f"未注册的完成探针：{job}")

    return probe


def bs_repository():
    """``bs_runs`` / ``bs_signals`` 只读仓储。"""
    from dragon_quant.bs_eval import storage

    return storage


def bs_strategy_doc():
    """四类买点 + 十条卖出规则 + 阈值（纯数据）。"""
    from dragon_quant.bs_eval import strategy_doc

    return strategy_doc


def bs_aggregate():
    """BS 汇总口径（纯函数）。"""
    from dragon_quant.bs_eval import aggregate

    return aggregate


def workspace_module():
    """workspace 公共门面（策略清单/当前选择/持久切换）。"""
    from dragon_quant import workspace

    return workspace


def workspace_writes_enabled(settings) -> bool:
    """Web 是否允许变更策略选择（远程/反代部署应显式关闭）。"""
    return bool(getattr(settings, "workspace_writes_enabled", True))
