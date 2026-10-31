"""共享 pydantic 模型与类型别名（新端点对外契约）。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

DateStr = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
CodeStr = Annotated[str, Field(min_length=1, max_length=32)]


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None


class Dims(BaseModel):
    drive: float | None = None
    leadership: float | None = None
    anti_drop: float | None = None
    liquidity: float | None = None
    absorption: float | None = None


class QuoteFields(BaseModel):
    open: float | None = None
    close: float | None = None
    high: float | None = None
    low: float | None = None
    pct: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None
    market_cap: float | None = None


class ReviewFields(BaseModel):
    buy_date: str | None = None
    buy_price: float | None = None
    max_return_5d: float | None = None
    max_drawdown_5d: float | None = None
    max_return_hold_days: int | None = None
    review_status: str | None = None
