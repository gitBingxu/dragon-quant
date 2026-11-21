"""G6 共同压力预算与单笔/主题/未知组门禁。"""

from __future__ import annotations

import math
from typing import Optional

from .account import AccountView
from .config import BSPolicyConfig

def _item_loss(*, qty: int, price: Optional[float],
               pressure_pct: Optional[float]) -> Optional[float]:
    if qty <= 0:
        return 0.0
    if price is None or pressure_pct is None:
        return None
    try:
        pct = float(pressure_pct)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(pct) or pct < 0 or pct > 1:
        return None
    return price * qty * pct


def _pressure_checks(config: BSPolicyConfig, account: Optional[AccountView],
                     proposal: Optional[dict] = None,
                     account_limit: Optional[float] = None
                     ) -> tuple[list[dict], list[str]]:
    checks: list[dict] = []
    reasons: list[str] = []
    if account is None:
        return ([{"check": "pressure", "passed": False,
                  "reason": "account_state_missing"}],
                ["account_state_missing"])
    items: list[dict] = []
    for position in account.positions:
        if position.qty <= 0:
            continue
        items.append({
            "code": position.code, "kind": "position", "qty": position.qty,
            "price": position.mark, "pressure_pct": position.pressure_pct,
            "theme_ids": tuple(position.theme_ids),
        })
    for reservation in account.reservations:
        if reservation.side.lower() != "buy" or reservation.qty <= 0:
            continue
        items.append({
            "code": reservation.code, "kind": "reservation",
            "qty": reservation.qty, "price": reservation.price,
            "pressure_pct": reservation.pressure_pct,
            "theme_ids": tuple(reservation.theme_ids),
        })
    if proposal is not None:
        items.append({**proposal, "kind": "proposed"})
    known_theme_loss: dict[str, float] = {}
    untagged_total = 0.0
    total_known = 0.0
    unknown_items: list[str] = []
    proposed_loss: Optional[float] = 0.0
    proposed_tagged: set[str] = set()
    for item in items:
        loss = _item_loss(qty=int(item["qty"]), price=item["price"],
                          pressure_pct=item["pressure_pct"])
        if loss is None:
            unknown_items.append(str(item["code"]))
            if item["kind"] == "proposed":
                proposed_loss = None
            continue
        if item["kind"] == "proposed":
            proposed_loss = loss
            proposed_tagged = set(item.get("theme_ids") or ())
            continue
        total_known += loss
        themes = tuple(item.get("theme_ids") or ())
        if themes:
            for theme in themes:
                known_theme_loss[theme] = known_theme_loss.get(theme, 0.0) + loss
        else:
            untagged_total += loss

    budgets = config.budgets
    if unknown_items:
        for code in dict.fromkeys(unknown_items):
            reasons.append(f"pressure_unknown:{code}")
        checks.append({"check": "pressure_estimable", "passed": False,
                       "unknown_codes": list(dict.fromkeys(unknown_items))})
    else:
        checks.append({"check": "pressure_estimable", "passed": True})

    untagged_including_proposed = untagged_total + (
        float(proposed_loss or 0.0) if proposal is not None and not proposed_tagged
        else 0.0)
    # 主题全集 = 既有已知主题 ∪ proposal 的新主题；未知风险加到每个不能排除
    # 重叠的主题（含全新主题），不只加到既有 known_theme_loss。
    theme_universe = set(known_theme_loss) | set(proposed_tagged)
    theme_values = {}
    for theme in sorted(theme_universe):
        value = known_theme_loss.get(theme, 0.0) + untagged_including_proposed
        if proposal is not None and theme in proposed_tagged:
            value += float(proposed_loss or 0.0)
        theme_values[theme] = value
    theme_ok = all(value <= budgets.per_theme + 1e-9
                   for value in theme_values.values())
    checks.append({"check": "per_theme", "passed": theme_ok,
                   "values": theme_values, "limit": budgets.per_theme})
    if not theme_ok:
        reasons.append("theme_pressure_budget_exceeded")

    unknown_value = untagged_total + (
        float(proposed_loss or 0.0) if proposal is not None and not proposed_tagged
        else 0.0)
    unknown_ok = unknown_value <= budgets.unknown_group + 1e-9
    checks.append({"check": "unknown_group", "passed": unknown_ok,
                   "value": unknown_value, "limit": budgets.unknown_group})
    if not unknown_ok:
        reasons.append("unknown_pressure_budget_exceeded")

    if proposal is not None:
        loss = proposed_loss
        per_trade_ok = (loss is not None and loss <= budgets.per_trade + 1e-9)
        checks.append({"check": "per_trade", "passed": per_trade_ok,
                       "value": loss, "limit": budgets.per_trade})
        if not per_trade_ok:
            reasons.append("per_trade_budget_exceeded")
        account_value = total_known
        if loss is not None and loss not in (0.0,):
            account_value += loss
        limit = budgets.account
        if account_limit is not None:
            limit = min(limit, float(account_limit))
        account_ok = loss is not None and account_value <= limit + 1e-9
        checks.append({"check": "account_pressure", "passed": account_ok,
                       "value": account_value, "limit": limit})
        if not account_ok:
            reasons.append("account_pressure_budget_exceeded")

    held_stop_loss = 0.0
    held_unestimable = False
    for position in account.positions:
        if position.qty <= 0 or position.mark is None:
            held_unestimable = True
            continue
        held_stop_loss += position.mark * position.qty * abs(
            config.invalidation.price_stop_pct) / 100
    held_ok = (not held_unestimable
               and held_stop_loss <= budgets.held_principal + 1e-9)
    checks.append({"check": "held_principal", "passed": held_ok,
                   "value": held_stop_loss, "limit": budgets.held_principal,
                   "unestimable": held_unestimable})
    if not held_ok:
        reasons.append("held_principal_budget_exceeded")
        if held_unestimable:
            reasons.append("held_valuation_missing")
    return checks, list(dict.fromkeys(reasons))
