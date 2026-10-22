"""板块身份与维度常量。

板块身份 = ``(source, category, code)``：

- ``source``：提供该板块的数据源（``tdx`` / ``ths``）。同一维度在本次运行内固定单一
  source，但不同运行可能回退到另一 source，且两套 code 体系不同构
  （TDX 概念 880xxx，THS 概念 3xxxxx / innerCode 885xxx），因此 source 必须进入身份。
- ``category``：``hy`` 行业 / ``gn`` 概念 / ``dq`` 地区。TDX 的 GN 与 DQ 共用 ``880``
  前缀，仅靠数值区间区分；HY2 / YJ 又复用 ``881xxx``。board code 在协议层并不全局唯一，
  ``get_board_members`` / ``get_stock_kline`` 也不携带 board_type，所以维度必须显式携带。
- ``code``：数据源原生的板块代码。

规范化键同时用于 cache key、评分器入参与候选归属比较，避免跨维度/跨源串号。
"""

from __future__ import annotations

CATEGORY_INDUSTRY = "hy"
CATEGORY_CONCEPT = "gn"
CATEGORY_REGION = "dq"

#: 融合总榜使用的维度顺序（同时用于 code 冲突时的优先级：hy > gn > dq）
SECTOR_CATEGORIES = (CATEGORY_INDUSTRY, CATEGORY_CONCEPT, CATEGORY_REGION)

#: 维度中文名（仅用于控制台/报告展示）
CATEGORY_CN = {
    CATEGORY_INDUSTRY: "行业",
    CATEGORY_CONCEPT: "概念",
    CATEGORY_REGION: "地区",
}

#: 三维融合总榜取前 N 个板块（模块常量，不暴露 CLI 参数）
SECTOR_TOP_N = 8


def sector_key(source: str, category: str, code: str) -> str:
    """构造规范化板块键，如 ``("tdx", "gn", "880683")`` → ``"tdx:gn:880683"``。"""
    return f"{source}:{category}:{code}"


def split_sector_key(key: str) -> tuple[str, str, str]:
    """拆分规范化板块键；格式不合法时返回 ``("", "", key)``。

    评分器在缺省场景下仍可能收到裸 code（历史调用/单测），调用方应把
    ``("", "", raw)`` 视为「无 source/category 的旧式键」并原样使用。
    """
    parts = (key or "").split(":", 2)
    if len(parts) != 3:
        return "", "", key or ""
    return parts[0], parts[1], parts[2]


def category_priority(category: str) -> int:
    """code 冲突时的维度优先级（数字越小越优先；未知维度排最后）。"""
    try:
        return SECTOR_CATEGORIES.index(category)
    except ValueError:
        return len(SECTOR_CATEGORIES)


# ─── 缓存键构造器 ───────────────────────────────────────────────────────────
# orchestrator 与评分器共用，避免各处重复拼字符串导致写入键与读取键不一致。

def sector_cache_key(prefix: str, key: str) -> str:
    """``("kline:1min:sector", "tdx:gn:880683")`` → ``"kline:1min:sector:tdx:gn:880683"``。"""
    return f"{prefix}:{key}"


def components_key(key: str) -> str:
    return sector_cache_key("sector:components", key)


def sector_1min_key(key: str) -> str:
    return sector_cache_key("kline:1min:sector", key)


def sector_5min_key(key: str) -> str:
    return sector_cache_key("kline:5min:sector", key)
