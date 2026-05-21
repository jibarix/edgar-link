---
name: edgar-link-financials
description: >-
  Pull and analyze SEC EDGAR financials through the edgar-link MCP server:
  normalized statements (BS/IS/CF/EQ/CI), analyst-normalized metrics (EBIT,
  EBITDA, FCF, ROIC, ROE, leverage, margins, working capital, growth, CAGR),
  LTM rollups, peer screening, and 5-year beta vs the S&P 500. Use when the
  user asks about a public company's financials, fundamentals, valuation
  inputs, or filings, e.g. "show Apple's last 3 years of revenue and FCF",
  "compute ROIC for MSFT over 5 quarters", "EBITDA margin trend for NVDA",
  "find software filers by SIC", or mentions a ticker, CIK, 10-K/10-Q, or
  XBRL concept. Do NOT use for live stock quotes, options, or news.
metadata:
  mcp-server: edgar
  version: 0.1.2
---

# edgar-link financials

This skill drives the `edgar` MCP server (the `edgar-link` engine). The server
exposes raw tools; this skill captures the workflow and analyst conventions a
caller would otherwise have to know. The engine returns **analyst-normalized**
outputs built from SEC XBRL, not raw filing dumps.

## ⚠️ Preconditions (check first)

- **`EDGAR_IDENTITY` must be set** in the MCP server's environment (e.g.
  `"Your Name you@example.com"`). The SEC fair-access policy requires it; without
  it, live calls throttle or fail. `SEC_EDGAR_USER_AGENT` is an accepted alias.
  Never hardcode or borrow someone else's identity.
- **`search_companies` needs a local index.** It reads `data/company_index.json`.
  If the tool returns an empty result with a "Company index not built" error,
  the user must run `python -m edgar.company_classifier --build` once. The other
  tools do not need this.

## Tools

| Tool | Use it to |
|------|-----------|
| `lookup_company(query)` | Resolve a name/ticker to SEC CIK candidates (≤5 fuzzy matches). |
| `get_financial_statement(cik_or_ticker, statement_type, period_type, num_periods)` | Normalized statement by period. |
| `get_concept(cik_or_ticker, concept, taxonomy)` | Full history of one XBRL concept (e.g. `Assets`). |
| `search_companies(sic, industry, country_inc, revenue_country, name_substring, limit)` | Filter the local classification index. |
| `list_metrics(category)` | Enumerate registered derived metrics. |
| `compute_metric(slug, cik_or_ticker, period_type, num_periods)` | Compute one derived metric series. |

Parameter values:
- `statement_type`: `BS`, `IS`, `CF`, `EQ`, `CI`, `ALL`
- `period_type`: `annual`, `quarterly`, `ytd`
- `list_metrics` `category`: `ratio`, `margin`, `return`, `wc`, `derived_line`,
  `growth`, or omit for all. (Call `list_metrics()` for the authoritative slug
  list — do not guess slugs.)

## Core workflow

Most requests follow **resolve → fetch → present**:

1. **Resolve the company.** If the user gives a ticker or CIK, you can pass it
   straight through — every tool resolves a name/ticker/CIK internally. If the
   name is ambiguous, call `lookup_company` first and confirm the right CIK with
   the user before pulling data.
2. **Fetch.**
   - For statement line items → `get_financial_statement`.
   - For a derived metric (margin, return, ratio, growth, FCF, EBITDA…) →
     `compute_metric` with the metric `slug`. If unsure the slug exists, call
     `list_metrics(category=...)` first.
   - For one specific raw XBRL concept's full history → `get_concept`.
   - For a peer set → `search_companies` (after the index precondition).
3. **Present** the periods returned, with units. Metric results include `unit`,
   `category`, and `statements_used` — surface the unit so percentages vs.
   dollars are unambiguous.

### `num_periods` and lookback

`compute_metric`'s `num_periods` is the number of **visible** periods you want
back. The engine automatically fetches extra history behind the scenes to
satisfy metrics that need a lookback (averages for ROE/ROA, multi-year CAGR).
**Do not pad `num_periods` yourself** — ask for the periods the user actually
wants and let the engine handle the lookback.

## Analyst-normalization caveats (state these when relevant)

- **EBIT is not raw `OperatingIncomeLoss`.** `derived_lines.ebit` adds back
  goodwill and asset impairments (and uses a pretax-plus-interest fallback for
  some hybrid-finance issuers). If a user is reconciling to the face of the
  filing, flag that the value is analyst-normalized.
- **EBITDA, FCF, total debt** are likewise composed from normalized inputs, not
  single tags.
- **LTM** metrics roll trailing four quarters; they need quarterly history to
  exist for that filer.
- **Beta** (`edgar.metrics.beta`) is peer beta / R² vs ^GSPC only — not a
  bottom-up unlever/relever chain. It needs ≥24 months of monthly history.

## Examples

**"Show Apple's last 3 fiscal years of revenue, EBIT, and FCF."**
→ `compute_metric("revenue", "AAPL", "annual", 3)`, then `"ebit"`, then `"fcf"`.
Present three series side by side; note EBIT is analyst-normalized.

**"ROIC for MSFT, last 5 quarters."**
→ `compute_metric("roic", "MSFT", "quarterly", 5)`. The engine fetches the extra
lookback quarters internally; report the 5 requested.

**"Find software filers by SIC 7372 incorporated in the US."**
→ Ensure the index exists, then
`search_companies(sic="7372", country_inc="US", limit=25)`.

**"What's NVDA's gross margin trend?"**
→ `list_metrics("margin")` if unsure of the slug, then
`compute_metric("gross_profit_margin", "NVDA", "annual", 5)`.

## More detail

- Metric categories and example slugs, LTM and beta specifics:
  `references/metrics-catalog.md`
- Common errors and how to resolve them: `references/troubleshooting.md`
