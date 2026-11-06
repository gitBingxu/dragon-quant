"""兼容转发：本地只读账户行情适配已迁至 ``dragon_quant.research.local_account_market``。"""

from dragon_quant.research.local_account_market import LocalAccountMarket  # noqa: F401

__all__ = ["LocalAccountMarket"]
