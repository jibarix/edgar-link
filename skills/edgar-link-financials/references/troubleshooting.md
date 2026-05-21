# Troubleshooting

## "No company matched '<query>'"

Cause: the name/ticker did not resolve to a CIK.

Fix:
- Try the ticker instead of the full legal name (e.g. `AAPL` not
  `Apple Incorporated`).
- Call `lookup_company(query)` to see fuzzy candidates and confirm the CIK.
- Confirm the entity is actually an SEC filer (foreign private issuers and
  private companies may not be).

## `search_companies` returns an empty result with a "Company index not built" error

Cause: `data/company_index.json` does not exist. `search_companies` is the only
tool that depends on this local index.

Fix: build it once, then retry:
```bash
python -m edgar.company_classifier --build
```
Country filters (`country_inc`, `revenue_country`) expect ISO 3166-1 alpha-2
codes (`US`, `JP`); `sic` matches the exact 4-digit code.

## Live calls throttle, hang, or 403

Cause: `EDGAR_IDENTITY` is not set in the MCP server's environment, so SEC fair-
access throttling kicks in.

Fix: set `EDGAR_IDENTITY="Your Name you@example.com"` (or the
`SEC_EDGAR_USER_AGENT` alias) in the server's env config and restart the server.
Do not hardcode or borrow another person's identity.

## "Unknown metric slug: <slug>"

Cause: the slug is not registered.

Fix: call `list_metrics(category=...)` and copy an exact slug. Common gotcha:
gross margin is `gross_profit_margin`, not `gross_margin`. Don't guess slugs.

## A metric returns null/empty for some periods

Cause: not every filer tags every concept needed for every metric, and older
periods may predate XBRL coverage.

Fix:
- Try `get_financial_statement(... "ALL" ...)` to see what the filer actually
  reports.
- Try a different `period_type` (`annual` vs `quarterly`).
- For one specific concept's full history, use `get_concept`.

## A metric value doesn't match the filing

Often expected, not a bug: `ebit`, `ebitda`, `fcf`, and `total_debt` are
**analyst-normalized** (e.g. EBIT adds back goodwill/asset impairments). Tell the
user the value is normalized and point at `references/metrics-catalog.md` for the
exact composition if they need to reconcile.

## LTM or beta returns nothing

- LTM needs quarterly history for the filer — pull with `period_type="quarterly"`.
- Beta needs ≥24 months of monthly bars and fails soft on Yahoo errors / short
  history.
