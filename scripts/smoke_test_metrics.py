"""Smoke test: compute a handful of metrics for AAPL and reconcile against
the hand-verified numbers from the FY2025 / FY2024 / FY2023 review.

Expected (FY2025):
    revenue              = 416,161 M
    gross_profit         = 195,201 M
    gross_profit_margin  = 0.469
    ebit (op income)     = 133,050 M
    ebit_margin          = 0.320
    ni                   = 112,010 M
    ni_margin            = 0.269
    fcf                  = 98,767 M  (CFO 111,482 - CapEx 12,715)
    fcf_margin           = 0.237
    current_ratio        = 0.893     (147,957 / 165,631)
    debt_to_equity       = ~1.23     (total_debt ~90,657 / equity 73,733)
"""
from __future__ import annotations

import sys

from edgar.company_lookup import format_cik, search_company
from edgar.filing_retrieval import FilingRetrieval
from edgar.xbrl_parser import XBRLParser
from edgar import metrics as edgar_metrics


METRICS_TO_TEST = [
    "revenue",
    "gross_profit",
    "gross_profit_margin",
    "ebit",
    "ebit_margin",
    "ebitda",
    "ebitda_margin",
    "ni",
    "ni_margin",
    "fcf",
    "fcf_margin",
    "current_ratio",
    "quick_ratio",
    "debt_to_equity",
    "debt_to_capital",
    "financial_leverage",
    "interest_coverage",
    "roa",
    "roe",
    "roic",
    "asset_turnover",
    "inventory_turnover",
    "days_sales_out",
    "days_inventory_out",
    "days_payables_out",
    "cash_conversion_cycle",
    "tax_rate_effective",
    "payout_ratio",
    "revenue_growth",
    "ni_growth",
    "fcf_growth",
    "revenue_cagr_3y",
]


def fmt(unit: str, v):
    if v is None:
        return "None"
    if unit == "USD":
        return f"{v / 1e6:>14,.1f} M"
    if unit == "ratio":
        return f"{v * 100:>14.2f}%"
    if unit == "days":
        return f"{v:>14.1f} d"
    if unit == "x":
        return f"{v:>14.2f}x"
    return f"{v:>14.4f}"


def main():
    print(f"Registry size: {len(edgar_metrics.REGISTRY)} metrics")
    print()

    matches = search_company("AAPL")
    if not matches:
        print("Could not resolve AAPL", file=sys.stderr)
        sys.exit(1)
    cik = format_cik(matches[0]["cik"])
    print(f"AAPL CIK: {cik}")

    filings = FilingRetrieval()
    parser = XBRLParser()
    facts = filings.get_company_facts(cik)

    # Fetch enough periods to satisfy max lookback we need (3 for CAGR-3y)
    NUM_PERIODS = 4
    LOOKBACK = 4  # extra for cagr_3y on top of 3 visible
    normalized = parser.parse_company_facts(
        facts, statement_type="ALL", period_type="annual",
        num_periods=NUM_PERIODS + LOOKBACK,
    )
    stmt = edgar_metrics.NormalizedStatement(normalized)
    print(f"Periods loaded: {stmt.periods}")
    print()

    header_periods = stmt.periods[:NUM_PERIODS]
    print(f"{'slug':<28} | {'unit':<8} | " + " | ".join(f"{p:>16}" for p in header_periods))
    print("-" * (28 + 8 + 18 * len(header_periods) + 6))

    for slug in METRICS_TO_TEST:
        spec = edgar_metrics.REGISTRY.get(slug)
        if spec is None:
            print(f"{slug:<28} | <NOT REGISTERED>")
            continue
        series = spec.fn(stmt)
        row = f"{slug:<28} | {spec.unit:<8} | "
        row += " | ".join(fmt(spec.unit, series.get(p)).rjust(16) for p in header_periods)
        print(row)


if __name__ == "__main__":
    main()
