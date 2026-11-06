"""兼容转发：本地日 K 递归分批加载已迁至 ``dragon_quant.research.bars_loader``。"""

from dragon_quant.research.bars_loader import (  # noqa: F401
    BarsTruncated,
    load_bars_resilient,
)

__all__ = ["BarsTruncated", "load_bars_resilient"]
