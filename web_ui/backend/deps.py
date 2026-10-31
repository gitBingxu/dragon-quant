"""只读仓储适配层（Ports & Adapters）。

每个 provider 函数内部 **lazy import** 既有仓储模块，导入 ``web_ui`` 不触库。
本模块只允许调用方案 §5.2 白名单 API；不新建连接、不写 SQL。
"""

from __future__ import annotations

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


def dashboard_settings(request: Request):
    """从 ``app.state`` 取 DashboardSettings（测试可直接 override）。"""
    return request.app.state.settings
