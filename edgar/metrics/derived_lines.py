"""Derived line items: figures computed from raw XBRL but not themselves tagged.

These exist because:
    1. They feed downstream ratios/margins (EBIT, EBITDA, FCF, etc.)
    2. The vendor catalog exposes them as standalone metric slugs
    3. Issuers sometimes don't tag GrossProfit even though they report it
"""

from __future__ import annotations

from edgar.metrics.registry import (
    NormalizedStatement,
    register,
    safe_add,
    safe_sub,
)


@register(
    slug="revenue",
    description="Net revenue (resolved across us-gaap revenue variants).",
    statements=("IS",),
    unit="USD",
    category="derived_line",
)
def revenue(s: NormalizedStatement) -> dict[str, float | None]:
    return s.get("revenue")


@register(
    slug="cogs",
    description="Cost of goods and services sold.",
    statements=("IS",),
    unit="USD",
    category="derived_line",
)
def cogs(s: NormalizedStatement) -> dict[str, float | None]:
    return s.get("cogs")


@register(
    slug="gross_profit",
    description="Revenue - COGS. Uses tagged GrossProfit if available.",
    statements=("IS",),
    unit="USD",
    category="derived_line",
)
def gross_profit(s: NormalizedStatement) -> dict[str, float | None]:
    tagged = s.get("gross_profit")
    if any(v is not None for v in tagged.values()):
        return tagged
    rev = s.get("revenue")
    c = s.get("cogs")
    return {p: safe_sub(rev[p], c[p]) for p in s.periods}


@register(
    slug="ebit",
    description="Analyst-normalized EBIT: OperatingIncomeLoss + goodwill impairment + asset impairment add-backs (treated as non-operating Unusual Items). Pretax+interest fallback for hybrid-finance issuers (KMX).",
    statements=("IS",),
    unit="USD",
    category="derived_line",
)
def ebit(s: NormalizedStatement) -> dict[str, float | None]:
    # Institutional-analyst convention: Operating Income excludes Unusual
    # Items (goodwill impairment, held-for-sale asset writedowns, M&A
    # restructuring, gains/losses on sales of businesses). Issuers tag
    # impairments inside OperatingIncomeLoss, so we add them back here to
    # recover the normalized base. Empirical reconciliation on US auto
    # dealers shows this closes the EBIT-growth gap by ~10-20pp on years
    # with large divestiture writedowns.
    op = s.get("operating_income")
    gw = s.get("goodwill_impairment")
    ai = s.get("asset_impairment")
    # Some issuers (e.g. KMX) never tag us-gaap:OperatingIncomeLoss because
    # they bundle finance-segment income into below-operating-line tags.
    # Fall back to pretax_income + interest_expense for those periods.
    pre = s.get("pretax_income")
    intx = s.get("interest_expense")
    out: dict[str, float | None] = {}
    for p in s.periods:
        if op[p] is not None:
            v = op[p]
            if gw[p] is not None:
                v += gw[p]
            if ai[p] is not None:
                v += ai[p]
            out[p] = v
        elif pre[p] is not None and intx[p] is not None:
            out[p] = pre[p] + intx[p]
        else:
            out[p] = None
    return out


@register(
    slug="ebitda",
    description="EBIT + D&A. EBIT uses pretax+interest fallback for hybrid-finance issuers (KMX) that don't tag OperatingIncomeLoss.",
    statements=("IS", "CF"),
    unit="USD",
    category="derived_line",
)
def ebitda(s: NormalizedStatement) -> dict[str, float | None]:
    eb = ebit(s)
    da = s.get("depreciation_amortization")
    out: dict[str, float | None] = {}
    for p in s.periods:
        # Require ebit to be present — otherwise safe_add(None, D&A) would
        # silently return just D&A, producing a meaningless margin (KMX bug).
        if eb[p] is None:
            out[p] = None
        else:
            out[p] = safe_add(eb[p], da[p])
    return out


@register(
    slug="ni",
    description="Net income (us-gaap:NetIncomeLoss).",
    statements=("IS",),
    unit="USD",
    category="derived_line",
)
def ni(s: NormalizedStatement) -> dict[str, float | None]:
    return s.get("net_income")


@register(
    slug="fcf",
    description="CFO - CapEx.",
    statements=("CF",),
    unit="USD",
    category="derived_line",
)
def fcf(s: NormalizedStatement) -> dict[str, float | None]:
    cfo = s.get("cfo")
    cx = s.get("capex")
    # CapEx is reported as a positive outflow on most filings; subtract it.
    return {p: safe_sub(cfo[p], cx[p]) for p in s.periods}


@register(
    slug="fcf_unlev",
    description="Unlevered FCF = CFO + InterestExpense*(1 - tax_rate) - CapEx.",
    statements=("IS", "CF"),
    unit="USD",
    category="derived_line",
)
def fcf_unlev(s: NormalizedStatement) -> dict[str, float | None]:
    cfo = s.get("cfo")
    cx = s.get("capex")
    intx = s.get("interest_expense")
    tax_rate = s.get("effective_tax_rate")
    out: dict[str, float | None] = {}
    for p in s.periods:
        if cfo[p] is None or cx[p] is None:
            out[p] = None
            continue
        # Interest add-back only if we have interest and a reasonable tax rate
        add_back = 0.0
        if intx[p] is not None and tax_rate[p] is not None:
            add_back = intx[p] * (1.0 - tax_rate[p])
        out[p] = cfo[p] + add_back - cx[p]
    return out


@register(
    slug="nwc",
    description="Net working capital = current assets - current liabilities.",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def nwc(s: NormalizedStatement) -> dict[str, float | None]:
    ca = s.get("current_assets")
    cl = s.get("current_liabilities")
    return {p: safe_sub(ca[p], cl[p]) for p in s.periods}


@register(
    slug="total_debt",
    description="Short-term + long-term debt plus dealer-specific extension lines (floor plan, non-recourse notes, loaner-vehicle notes).",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def total_debt(s: NormalizedStatement) -> dict[str, float | None]:
    ltd_cur = s.get("long_term_debt_current")
    ltd_nc = s.get("long_term_debt_noncurrent")
    ltd_total = s.get("long_term_debt_total")
    cp = s.get("commercial_paper")
    stb = s.get("short_term_borrowings")
    # Dealer-specific extension concepts (None for non-dealers, no harm done)
    fp = s.get("floor_plan_debt")
    nr = s.get("nonrecourse_debt")
    lv = s.get("loaner_vehicle_debt")
    out: dict[str, float | None] = {}
    for p in s.periods:
        # Prefer the split (current + noncurrent) view when either side
        # resolves; otherwise fall back to the bundled total tag.
        if ltd_cur[p] is not None or ltd_nc[p] is not None:
            ltd = safe_add(ltd_cur[p], ltd_nc[p])
        else:
            ltd = ltd_total[p]
        out[p] = safe_add(ltd, cp[p], stb[p], fp[p], nr[p], lv[p])
    return out


@register(
    slug="operating_lease_liability_total",
    description="Total ASC 842 operating lease liability (current + noncurrent).",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def operating_lease_liability_total(s: NormalizedStatement) -> dict[str, float | None]:
    # Issuers tag either the bundled total or the current/noncurrent split.
    # Prefer the split (so we don't miss the current piece when only the
    # noncurrent tag is used) and fall back to the bundled total.
    bundled = s.get("operating_lease_liability")
    cur = s.get("operating_lease_liability_current")
    nc = s.get("operating_lease_liability_noncurrent")
    out: dict[str, float | None] = {}
    for p in s.periods:
        if cur[p] is not None or nc[p] is not None:
            out[p] = safe_add(cur[p], nc[p])
        else:
            out[p] = bundled[p]
    return out


@register(
    slug="total_debt_incl_leases",
    description="Total debt + ASC 842 operating lease liabilities. Matches the institutional Debt/Capital convention.",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def total_debt_incl_leases(s: NormalizedStatement) -> dict[str, float | None]:
    debt = total_debt(s)
    ol = operating_lease_liability_total(s)
    return {p: safe_add(debt[p], ol[p]) for p in s.periods}


@register(
    slug="invested_capital",
    description="Total equity + total debt (incl. operating lease liabilities).",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def invested_capital(s: NormalizedStatement) -> dict[str, float | None]:
    eq = s.get("total_equity")
    debt = total_debt_incl_leases(s)
    out: dict[str, float | None] = {}
    for p in s.periods:
        out[p] = safe_add(eq[p], debt[p])
    return out


@register(
    slug="cash_and_st_investments",
    description="Cash + short-term marketable securities.",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def cash_and_st_investments(s: NormalizedStatement) -> dict[str, float | None]:
    cash = s.get("cash")
    sti = s.get("short_term_investments")
    return {p: safe_add(cash[p], sti[p]) for p in s.periods}


@register(
    slug="ebitda_less_capex",
    description="EBITDA - CapEx.",
    statements=("IS", "CF"),
    unit="USD",
    category="derived_line",
)
def ebitda_less_capex(s: NormalizedStatement) -> dict[str, float | None]:
    eb = ebitda(s)
    cx = s.get("capex")
    return {p: safe_sub(eb[p], cx[p]) for p in s.periods}


@register(
    slug="tangible_book_value",
    description="Common equity - goodwill - intangible assets (ex. goodwill). Analyst-normalized denominator for P/TangBV.",
    statements=("BS",),
    unit="USD",
    category="derived_line",
)
def tangible_book_value(s: NormalizedStatement) -> dict[str, float | None]:
    eq = s.get("total_equity")
    gw = s.get("goodwill")
    intang = s.get("intangible_assets_ex_goodwill")
    out: dict[str, float | None] = {}
    for p in s.periods:
        # Missing intangibles/goodwill → treat as zero, not None. Issuers
        # without goodwill or distinct intangible tags genuinely have a
        # tangible book equal to total equity.
        e = eq.get(p)
        if e is None:
            out[p] = None
            continue
        out[p] = e - (gw.get(p) or 0.0) - (intang.get(p) or 0.0)
    return out
