"""Margin metrics: line item / revenue."""

from __future__ import annotations

from edgar.metrics.registry import (
    NormalizedStatement,
    register,
    safe_div,
)
from edgar.metrics.derived_lines import (
    ebit,
    ebitda,
    ebitda_less_capex,
    fcf,
    fcf_unlev,
    gross_profit,
    nwc,
)


def _margin_against_revenue(
    s: NormalizedStatement,
    line_values: dict[str, float | None],
) -> dict[str, float | None]:
    rev = s.get("revenue")
    return {p: safe_div(line_values[p], rev[p]) for p in s.periods}


# ── Margins from raw IS lines ──


@register(
    slug="gross_profit_margin",
    description="Gross profit / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def gross_profit_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, gross_profit(s))


@register(
    slug="cogs_margin",
    description="COGS / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def cogs_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("cogs"))


@register(
    slug="rd_margin",
    description="R&D expense / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def rd_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("rd"))


@register(
    slug="sga_margin",
    description="SG&A / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def sga_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("sga"))


@register(
    slug="ebit_margin",
    description="EBIT / revenue. EBIT uses pretax+interest fallback for hybrid-finance issuers.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def ebit_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, ebit(s))


@register(
    slug="income_oper_margin",
    description="EBIT / revenue (alias of ebit_margin).",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def income_oper_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, ebit(s))


@register(
    slug="ebitda_margin",
    description="EBITDA / revenue.",
    statements=("IS", "CF"),
    unit="ratio",
    category="margin",
)
def ebitda_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, ebitda(s))


@register(
    slug="ebitda_less_capex_margin",
    description="(EBITDA - CapEx) / revenue.",
    statements=("IS", "CF"),
    unit="ratio",
    category="margin",
)
def ebitda_less_capex_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, ebitda_less_capex(s))


@register(
    slug="income_pretax_margin",
    description="Pretax income / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def income_pretax_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("pretax_income"))


@register(
    slug="ni_margin",
    description="Net income / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def ni_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("net_income"))


@register(
    slug="tax_exp_margin",
    description="Tax expense / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def tax_exp_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("tax_expense"))


@register(
    slug="interest_exp_margin",
    description="Interest expense / revenue.",
    statements=("IS",),
    unit="ratio",
    category="margin",
)
def interest_exp_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("interest_expense"))


@register(
    slug="da_margin",
    description="Depreciation & amortization / revenue.",
    statements=("IS", "CF"),
    unit="ratio",
    category="margin",
)
def da_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("depreciation_amortization"))


# ── Margins from CF / derived lines ──


@register(
    slug="fcf_margin",
    description="Free cash flow / revenue.",
    statements=("IS", "CF"),
    unit="ratio",
    category="margin",
)
def fcf_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, fcf(s))


@register(
    slug="fcf_unlev_margin",
    description="Unlevered FCF / revenue.",
    statements=("IS", "CF"),
    unit="ratio",
    category="margin",
)
def fcf_unlev_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, fcf_unlev(s))


@register(
    slug="capex_margin",
    description="CapEx / revenue.",
    statements=("IS", "CF"),
    unit="ratio",
    category="margin",
)
def capex_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, s.get("capex"))


@register(
    slug="nwc_margin",
    description="Net working capital / revenue.",
    statements=("BS", "IS"),
    unit="ratio",
    category="margin",
)
def nwc_margin(s: NormalizedStatement) -> dict[str, float | None]:
    return _margin_against_revenue(s, nwc(s))
