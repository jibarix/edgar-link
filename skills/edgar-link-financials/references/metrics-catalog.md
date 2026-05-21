# Metric catalog

`list_metrics(category)` is the authoritative source — slugs are registered at
import time and this list can drift. Use this file for orientation and to pick a
category; confirm an exact slug with `list_metrics` before calling
`compute_metric`. Categories below match the `category` argument.

## `derived_line` — normalized statement lines

`revenue`, `cogs`, `gross_profit`, `ebit`, `ebitda`, `ni`, `fcf`, `fcf_unlev`,
`nwc`, `total_debt`, `operating_lease_liability_total`,
`total_debt_incl_leases`, `invested_capital`, `cash_and_st_investments`, …

These are composed from normalized inputs, not single XBRL tags:

- `ebit` = `OperatingIncomeLoss` + goodwill impairment + asset impairment, with
  a pretax-plus-interest fallback for some hybrid-finance issuers. **Analyst-
  normalized — will not match the face of the filing for names with unusual
  impairments inside reported operating income.**
- `ebitda`, `fcf`, `total_debt` are likewise built up from inputs.

## `margin` — line ÷ revenue

`gross_profit_margin`, `cogs_margin`, `rd_margin`, `sga_margin`, `ebit_margin`,
`income_oper_margin`, `ebitda_margin`, `ebitda_less_capex_margin`,
`income_pretax_margin`, `ni_margin`, `tax_exp_margin`, `interest_exp_margin`,
`da_margin`, `fcf_margin`, `fcf_unlev_margin`, `capex_margin`, `nwc_margin`.

Note: gross margin's slug is `gross_profit_margin` (not `gross_margin`).

## `return` — capital efficiency

`roa`, `roe`, `roic`, `asset_turnover`, `fixed_asset_turnover`,
`inventory_turnover`. ROE/ROA use average balances, so they pull an extra period
of lookback automatically.

## `ratio` — leverage / liquidity / coverage

`current_ratio`, `quick_ratio`, `cash_ratio`, `debt_to_equity`,
`debt_to_capital`, `cash_to_capital`, `financial_leverage`, `interest_coverage`,
`payout_ratio`, `tax_rate_effective`, `interest_rate_effective`.

## `wc` — working capital cycle

`days_sales_out` (DSO), `days_inventory_out` (DIO), `days_payables_out` (DPO),
`cash_conversion_cycle`.

## `growth` — generated per base metric

Growth and CAGR slugs are generated for many base metrics:

- `<base>_growth` — period-over-period growth
- `<base>_cagr_3y`, `<base>_cagr_5y`, `<base>_cagr_7y` — multi-year CAGR
- EPS variants exist too.

CAGR needs the full year span of history; the engine fetches the lookback
automatically when you set `num_periods` to the visible count you want.

## LTM (trailing twelve months)

The `ltm` module rolls trailing four quarters. It needs quarterly history to
exist for the filer. Use `period_type="quarterly"` upstream.

## Beta (`edgar.metrics.beta`)

5-year monthly beta and R² vs the S&P 500 (^GSPC), one row per ticker. This is
**peer beta / R² only** — not a bottom-up unlever → cash-correct → relever
chain. Needs ≥24 months of aligned monthly history; fails soft on Yahoo errors
or short history. Not exposed as a `compute_metric` slug — it's a library
utility (and is wired into `scripts/build_comps.py`).
