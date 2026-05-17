"""Working-capital cycle metrics: DSO, DIO, DPO, CCC.

Day counts:
    - annual periods use 365
    - quarterly / ytd use the period length in days (computed from period_end - prior_end)
"""

from __future__ import annotations

from datetime import date
from typing import Iterable

from edgar.metrics.registry import (
    NormalizedStatement,
    register,
    avg2,
    safe_div,
)


def _days_in_period(periods: Iterable[str], period: str, fallback: int = 365) -> int:
    """Approximate the period length in days using the prior period_end.

    For annual data this is ~365. For quarterly it's ~90-92. For ytd it varies.
    """
    plist = list(periods)
    try:
        idx = plist.index(period)
    except ValueError:
        return fallback
    if idx + 1 >= len(plist):
        return fallback
    try:
        end = date.fromisoformat(period)
        prior = date.fromisoformat(plist[idx + 1])
    except ValueError:
        return fallback
    delta = (end - prior).days
    if delta <= 0:
        return fallback
    return delta


@register(
    slug="days_sales_out",
    description="(Avg AR / Revenue) * days_in_period.",
    statements=("BS", "IS"),
    unit="days",
    category="wc",
    needs_lookback=1,
)
def days_sales_out(s: NormalizedStatement) -> dict[str, float | None]:
    ar = s.get("accounts_receivable")
    rev = s.get("revenue")
    out: dict[str, float | None] = {}
    for p in s.periods:
        prior = s.prior_period(p)
        if prior is None:
            out[p] = None
            continue
        avg_ar = avg2(ar[p], ar[prior])
        days = _days_in_period(s.periods, p)
        ratio = safe_div(avg_ar, rev[p])
        out[p] = ratio * days if ratio is not None else None
    return out


@register(
    slug="days_inventory_out",
    description="(Avg inventory / COGS) * days_in_period.",
    statements=("BS", "IS"),
    unit="days",
    category="wc",
    needs_lookback=1,
)
def days_inventory_out(s: NormalizedStatement) -> dict[str, float | None]:
    inv = s.get("inventory")
    c = s.get("cogs")
    out: dict[str, float | None] = {}
    for p in s.periods:
        prior = s.prior_period(p)
        if prior is None:
            out[p] = None
            continue
        avg_inv = avg2(inv[p], inv[prior])
        days = _days_in_period(s.periods, p)
        ratio = safe_div(avg_inv, c[p])
        out[p] = ratio * days if ratio is not None else None
    return out


@register(
    slug="days_payables_out",
    description="(Avg AP / COGS) * days_in_period.",
    statements=("BS", "IS"),
    unit="days",
    category="wc",
    needs_lookback=1,
)
def days_payables_out(s: NormalizedStatement) -> dict[str, float | None]:
    ap = s.get("accounts_payable")
    c = s.get("cogs")
    out: dict[str, float | None] = {}
    for p in s.periods:
        prior = s.prior_period(p)
        if prior is None:
            out[p] = None
            continue
        avg_ap = avg2(ap[p], ap[prior])
        days = _days_in_period(s.periods, p)
        ratio = safe_div(avg_ap, c[p])
        out[p] = ratio * days if ratio is not None else None
    return out


@register(
    slug="cash_conversion_cycle",
    description="DSO + DIO - DPO. Negative = supplier-financed working capital.",
    statements=("BS", "IS"),
    unit="days",
    category="wc",
    needs_lookback=1,
)
def cash_conversion_cycle(s: NormalizedStatement) -> dict[str, float | None]:
    dso = days_sales_out(s)
    dio = days_inventory_out(s)
    dpo = days_payables_out(s)
    out: dict[str, float | None] = {}
    for p in s.periods:
        if dso[p] is None or dio[p] is None or dpo[p] is None:
            out[p] = None
        else:
            out[p] = dso[p] + dio[p] - dpo[p]
    return out
