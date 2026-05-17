"""Balance-sheet and coverage ratios (no period averaging required)."""

from __future__ import annotations

from edgar.metrics.registry import (
    NormalizedStatement,
    register,
    safe_add,
    safe_div,
)
from edgar.metrics.derived_lines import total_debt, total_debt_incl_leases


@register(
    slug="current_ratio",
    description="Current assets / current liabilities.",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def current_ratio(s: NormalizedStatement) -> dict[str, float | None]:
    ca = s.get("current_assets")
    cl = s.get("current_liabilities")
    return {p: safe_div(ca[p], cl[p]) for p in s.periods}


@register(
    slug="quick_ratio",
    description="(Cash + ST investments + AR) / current liabilities.",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def quick_ratio(s: NormalizedStatement) -> dict[str, float | None]:
    cash = s.get("cash")
    sti = s.get("short_term_investments")
    ar = s.get("accounts_receivable")
    cl = s.get("current_liabilities")
    out: dict[str, float | None] = {}
    for p in s.periods:
        num = safe_add(cash[p], sti[p], ar[p])
        out[p] = safe_div(num, cl[p])
    return out


@register(
    slug="cash_ratio",
    description="Cash and equivalents / current liabilities.",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def cash_ratio(s: NormalizedStatement) -> dict[str, float | None]:
    cash = s.get("cash")
    cl = s.get("current_liabilities")
    return {p: safe_div(cash[p], cl[p]) for p in s.periods}


@register(
    slug="debt_to_equity",
    description="Total debt / total equity.",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def debt_to_equity(s: NormalizedStatement) -> dict[str, float | None]:
    debt = total_debt(s)
    eq = s.get("total_equity")
    return {p: safe_div(debt[p], eq[p]) for p in s.periods}


@register(
    slug="debt_to_capital",
    description="(Total debt + operating lease liabilities) / (debt + leases + total equity). Matches the institutional Debt/Capital convention.",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def debt_to_capital(s: NormalizedStatement) -> dict[str, float | None]:
    # The institutional "LTM Total Debt/Capital %" convention treats ASC
    # 842 operating lease liabilities as debt. Debt/EBITDA (debt_to_ebitda)
    # intentionally stays on bank-debt-only via total_debt.
    debt = total_debt_incl_leases(s)
    eq = s.get("total_equity")
    out: dict[str, float | None] = {}
    for p in s.periods:
        cap = safe_add(debt[p], eq[p])
        out[p] = safe_div(debt[p], cap)
    return out


@register(
    slug="cash_to_capital",
    description="(Cash + ST investments) / (total debt + total equity).",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def cash_to_capital(s: NormalizedStatement) -> dict[str, float | None]:
    cash = s.get("cash")
    sti = s.get("short_term_investments")
    debt = total_debt(s)
    eq = s.get("total_equity")
    out: dict[str, float | None] = {}
    for p in s.periods:
        cash_total = safe_add(cash[p], sti[p])
        cap = safe_add(debt[p], eq[p])
        out[p] = safe_div(cash_total, cap)
    return out


@register(
    slug="financial_leverage",
    description="Total assets / total equity.",
    statements=("BS",),
    unit="ratio",
    category="ratio",
)
def financial_leverage(s: NormalizedStatement) -> dict[str, float | None]:
    a = s.get("total_assets")
    eq = s.get("total_equity")
    return {p: safe_div(a[p], eq[p]) for p in s.periods}


@register(
    slug="interest_coverage",
    description="EBIT / interest expense. Higher = more comfortable debt service.",
    statements=("IS",),
    unit="x",
    category="ratio",
)
def interest_coverage(s: NormalizedStatement) -> dict[str, float | None]:
    op = s.get("operating_income")
    intx = s.get("interest_expense")
    return {p: safe_div(op[p], intx[p]) for p in s.periods}


@register(
    slug="payout_ratio",
    description="Dividends paid / net income.",
    statements=("IS", "CF"),
    unit="ratio",
    category="ratio",
)
def payout_ratio(s: NormalizedStatement) -> dict[str, float | None]:
    div = s.get("dividends_paid")
    ni = s.get("net_income")
    return {p: safe_div(div[p], ni[p]) for p in s.periods}


@register(
    slug="tax_rate_effective",
    description="Tax expense / pretax income.",
    statements=("IS",),
    unit="ratio",
    category="ratio",
)
def tax_rate_effective(s: NormalizedStatement) -> dict[str, float | None]:
    # Prefer the tagged ratio when available, else compute
    tagged = s.get("effective_tax_rate")
    if any(v is not None for v in tagged.values()):
        return tagged
    tx = s.get("tax_expense")
    pretax = s.get("pretax_income")
    return {p: safe_div(tx[p], pretax[p]) for p in s.periods}


@register(
    slug="interest_rate_effective",
    description="Interest expense / total debt (period-end).",
    statements=("IS", "BS"),
    unit="ratio",
    category="ratio",
)
def interest_rate_effective(s: NormalizedStatement) -> dict[str, float | None]:
    intx = s.get("interest_expense")
    debt = total_debt(s)
    return {p: safe_div(intx[p], debt[p]) for p in s.periods}
