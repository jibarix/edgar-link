"""Return / turnover metrics. All use 2-point averaging of balance-sheet values.

The first period in any pull cannot be averaged (no prior period), so the
metric returns None for it. Callers who want N usable values should fetch
N+1 periods upstream.
"""

from __future__ import annotations

from edgar.metrics.registry import (
    NormalizedStatement,
    register,
    avg2,
    safe_add,
    safe_div,
)
from edgar.metrics.derived_lines import ebit, invested_capital, total_debt


def _avg_series(s: NormalizedStatement, concept: str) -> dict[str, float | None]:
    """Build a {period: avg(period, prior_period)} series for a concept."""
    raw = s.get(concept)
    out: dict[str, float | None] = {}
    for p in s.periods:
        prior = s.prior_period(p)
        if prior is None:
            out[p] = None
        else:
            out[p] = avg2(raw[p], raw[prior])
    return out


# ── Return on capital ──


@register(
    slug="roa",
    description="Net income / average total assets.",
    statements=("BS", "IS"),
    unit="ratio",
    category="return",
    needs_lookback=1,
)
def roa(s: NormalizedStatement) -> dict[str, float | None]:
    ni = s.get("net_income")
    avg_assets = _avg_series(s, "total_assets")
    return {p: safe_div(ni[p], avg_assets[p]) for p in s.periods}


@register(
    slug="roe",
    description="Net income / average total equity.",
    statements=("BS", "IS"),
    unit="ratio",
    category="return",
    needs_lookback=1,
)
def roe(s: NormalizedStatement) -> dict[str, float | None]:
    ni = s.get("net_income")
    avg_eq = _avg_series(s, "total_equity")
    return {p: safe_div(ni[p], avg_eq[p]) for p in s.periods}


@register(
    slug="roic",
    description="NOPAT / average invested capital. NOPAT = EBIT * (1 - effective tax rate).",
    statements=("BS", "IS"),
    unit="ratio",
    category="return",
    needs_lookback=1,
)
def roic(s: NormalizedStatement) -> dict[str, float | None]:
    ebit_vals = ebit(s)
    tax_rate = s.get("effective_tax_rate")
    # avg invested capital
    eq_curr = s.get("total_equity")
    debt = total_debt(s)
    ic_by_period = {p: safe_add(eq_curr[p], debt[p]) for p in s.periods}

    out: dict[str, float | None] = {}
    for p in s.periods:
        prior = s.prior_period(p)
        if prior is None or ebit_vals[p] is None:
            out[p] = None
            continue
        ic_curr = ic_by_period.get(p)
        ic_prev = ic_by_period.get(prior)
        if ic_curr is None or ic_prev is None:
            out[p] = None
            continue
        avg_ic = (ic_curr + ic_prev) / 2.0
        if avg_ic == 0:
            out[p] = None
            continue
        # If we can't compute a NOPAT adjustment cleanly, fall back to EBIT/IC.
        if tax_rate[p] is None:
            out[p] = ebit_vals[p] / avg_ic
        else:
            out[p] = ebit_vals[p] * (1.0 - tax_rate[p]) / avg_ic
    return out


# ── Turnover ratios ──


@register(
    slug="asset_turnover",
    description="Revenue / average total assets.",
    statements=("BS", "IS"),
    unit="x",
    category="return",
    needs_lookback=1,
)
def asset_turnover(s: NormalizedStatement) -> dict[str, float | None]:
    rev = s.get("revenue")
    avg_assets = _avg_series(s, "total_assets")
    return {p: safe_div(rev[p], avg_assets[p]) for p in s.periods}


@register(
    slug="fixed_asset_turnover",
    description="Revenue / average net PP&E.",
    statements=("BS", "IS"),
    unit="x",
    category="return",
    needs_lookback=1,
)
def fixed_asset_turnover(s: NormalizedStatement) -> dict[str, float | None]:
    rev = s.get("revenue")
    avg_ppe = _avg_series(s, "ppe_net")
    return {p: safe_div(rev[p], avg_ppe[p]) for p in s.periods}


@register(
    slug="inventory_turnover",
    description="COGS / average inventory.",
    statements=("BS", "IS"),
    unit="x",
    category="return",
    needs_lookback=1,
)
def inventory_turnover(s: NormalizedStatement) -> dict[str, float | None]:
    c = s.get("cogs")
    avg_inv = _avg_series(s, "inventory")
    return {p: safe_div(c[p], avg_inv[p]) for p in s.periods}
