# edgar-connect

Analyst-grade SEC EDGAR financials. A Python library — and MCP server —
that pulls XBRL filings directly from the SEC and layers a
**vendor-comparable normalized metric / comps / beta engine** on top.
Unlike raw-filings tools, `edgar-connect` returns reconciled margins,
EBITDA/EBIT (analyst-normalized), growth, returns, debt ratios, LTM
rollups, and bottom-up beta — not just structured filing dumps. No API
key, no subscription; free directly against the SEC.

> **SEC identity required.** SEC fair-access policy requires every
> requester to identify themselves. Before any live call, set:
> ```bash
> export EDGAR_IDENTITY="Your Name your@email.com"   # macOS/Linux
> $env:EDGAR_IDENTITY = "Your Name your@email.com"   # Windows PowerShell
> ```
> Without it the SEC throttles requests (the package still imports
> offline for the metrics engine and tests). Never ship or borrow
> someone else's identity.

## Overview

The EDGAR Financial Tool provides access to financial data from public companies by interacting directly with the U.S. Securities and Exchange Commission's Electronic Data Gathering, Analysis, and Retrieval (EDGAR) system and its APIs. This tool simplifies the process of retrieving, parsing, and presenting financial statement data from SEC filings in XBRL format.

## Features

- **Company Search**: Look up companies by name or ticker symbol
- **Financial Statement Retrieval**: Download Balance Sheets, Income Statements, Cash Flow Statements, and more
- **XBRL Data Extraction**: Access structured financial data using SEC's official XBRL APIs
- **Multi-period Analysis**: Retrieve data across multiple reporting periods
- **Flexible Output Formats**: Export data as CSV, JSON, Excel, or view directly in console
- **Interactive Mode**: User-friendly command-line interface for step-by-step data retrieval
- **Caching System**: Efficient data retrieval with local caching to minimize redundant API calls

## SEC EDGAR API Integration

This tool leverages the SEC's official XBRL data APIs, which provide standardized financial data in JSON format:

- **Company Facts API**: Retrieves all XBRL facts for a company in a single request
  - Endpoint: `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
  - Usage: Provides all financial data across all reporting periods
  
- **Company Concept API**: Retrieves specific financial concepts for a company
  - Endpoint: `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/taxonomy/tag.json`
  - Example: `https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Assets.json`
  - Usage: Provides historical values for a specific financial metric

These APIs offer several advantages over traditional EDGAR filing access:
- Standardized data structure across companies
- Clean, normalized values for financial metrics
- Historical data in a single request
- Lower bandwidth requirements and faster processing

## SEC API Compliance

The tool adheres to SEC.gov's access requirements:
- Includes proper User-Agent headers with contact information
- Implements rate limiting (maximum 10 requests per second)
- Uses caching to minimize redundant requests
- No CORS usage or scraping of HTML content

For more information on SEC's API requirements, visit: https://www.sec.gov/developer

## Installation

### Prerequisites

- Python 3.9 or higher
- pip (Python package installer)

### Quick install (from GitHub)

No PyPI release yet — install straight from the repo:

```bash
# library + metrics engine
pip install "git+https://github.com/jibarix/edgar-connect.git#egg=edgar-connect"

# with the MCP server extra
pip install "edgar-connect[mcp] @ git+https://github.com/jibarix/edgar-connect.git"
```

Then set your SEC identity (see the note at the top) before any live call.

### Steps (from a clone, for development)

1. Clone the repository:
   ```bash
   git clone https://github.com/jibarix/edgar-connect.git
   cd edgar-connect
   ```

2. Create a virtual environment:
   ```bash
   # On Windows
   python -m venv venv
   venv\Scripts\activate

   # On macOS/Linux
   python3 -m venv venv
   source venv/bin/activate
   ```

3. Install the package:
   ```bash
   pip install -e .
   ```

   Dependencies are declared in `pyproject.toml` and pinned to known-good
   versions.

4. When you're done using the tool, you can deactivate the virtual environment:
   ```bash
   deactivate
   ```

## Usage

### Interactive Mode

For a guided experience, simply run:

```bash
python main.py
```

The interactive mode will prompt you for:
- Company name or ticker
- Financial statement type (Balance Sheet, Income Statement, Cash Flow, etc.)
- Reporting period (Annual, Quarterly, or Year-to-Date)
- Number of periods to retrieve
- Output format

### Command-line Mode

For scripted or automated use:

```bash
python main.py --company "Apple Inc" --statement-type BS --period-type annual --num-periods 3 --output-format excel
```

### Command-line Options

```
Company Information:
  --company COMPANY, -c COMPANY
                        Company name or ticker
  --cik CIK             Company CIK number (overrides --company if provided)

Filing Selection:
  --statement-type {BS,IS,CF,EQ,CI,ALL}, -s {BS,IS,CF,EQ,CI,ALL}
                        Financial statement type to extract (default: ALL)
  --period-type {annual,quarterly,ytd}, -p {annual,quarterly,ytd}
                        Reporting period type (default: annual)
  --num-periods NUM_PERIODS, -n NUM_PERIODS
                        Number of periods to retrieve

Output Options:
  --output-format {csv,json,excel,console}, -f {csv,json,excel,console}
                        Output format (default: csv)
  --output-file OUTPUT_FILE, -o OUTPUT_FILE
                        Output file path (default: auto-generated)
```

## Examples

### Retrieving Apple's Balance Sheet for the Past 3 Years

```bash
python main.py -c "Apple Inc" -s BS -p annual -n 3 -f excel
```

### Getting Amazon's Income Statement for Recent Quarters

```bash
python main.py -c "Amazon.com Inc" -s IS -p quarterly -n 4 -f json
```

### Viewing Microsoft's Cash Flow Statement in the Console

```bash
python main.py -c "Microsoft Corporation" -s CF -p annual -n 2 -f console
```

## MCP Server

The same retrieval and parsing pipeline is exposed as an [MCP](https://modelcontextprotocol.io)
stdio server so MCP clients (e.g. Claude Code) can call EDGAR directly in a
conversation instead of running `main.py`.

### Install

```bash
pip install -e ".[mcp]"
```

### Register with Claude Code

```bash
claude mcp add edgar -e EDGAR_IDENTITY="Your Name your@email.com" -- python -m edgar_mcp
```

### Register with any MCP client (Claude Desktop, etc.)

Add to your client's MCP config (e.g. `claude_desktop_config.json`).
The `EDGAR_IDENTITY` env var is required — the server reaches the SEC
under it:

```json
{
  "mcpServers": {
    "edgar": {
      "command": "python",
      "args": ["-m", "edgar_mcp"],
      "env": {
        "EDGAR_IDENTITY": "Your Name your@email.com"
      }
    }
  }
}
```

### Available tools

| Tool | Purpose |
|------|---------|
| `lookup_company(query)` | Resolve a name or ticker to its SEC CIK (fuzzy-matched). |
| `get_financial_statement(cik_or_ticker, statement_type, period_type, num_periods)` | Normalized BS / IS / CF / EQ / CI / ALL by period. |
| `get_concept(cik_or_ticker, concept, taxonomy)` | Full historical time series for a single XBRL concept. |
| `search_companies(sic, industry, country_inc, revenue_country, name_substring, limit)` | Filter the local SIC/country/revenue classification index. |

`search_companies` reads `data/company_index.json`; build it once with
`python -m edgar.company_classifier --build` before querying.

## Understanding the Data

### Financial Statement Types

| Code | Description |
|------|-------------|
| BS   | Balance Sheet |
| IS   | Income Statement |
| CF   | Cash Flow Statement |
| EQ   | Equity Statement |
| CI   | Comprehensive Income |
| ALL  | All Financial Statements |

### XBRL Data Structure

Financial data retrieved through the SEC API is structured as follows:
- Company facts include multiple taxonomies (typically `us-gaap` or `ifrs-full`)
- Each taxonomy contains financial concepts (e.g., `Assets`, `Liabilities`, `Revenues`)
- Each concept has facts with:
  - Value (`val`): The numeric value
  - Period start and end dates (`start`, `end`)
  - Filing date (`filed`)
  - Unit of measure (`USD`, `shares`, etc.)

The tool normalizes this data and organizes it by financial statement type, making it easy to analyze and export.

## Financial Metrics

The `edgar.metrics` package layers a vendor-comparable metric engine on top of
the raw XBRL pull. ~60 metrics are registered across five modules:

| Module | Examples |
|--------|----------|
| `derived_lines` | revenue, cogs, gross_profit, ebit, ebitda, ebitda_less_capex, fcf, fcf_unlev, nwc, total_debt, total_debt_incl_leases, invested_capital, cash_and_st_investments |
| `margins` | gross_profit_margin, ebit_margin, ebitda_margin, ni_margin, fcf_margin, capex_margin, sga_margin, ... |
| `ratios` | debt_to_capital, debt_to_ebitda, current_ratio, quick_ratio, interest_coverage, ... |
| `returns` | roa, roe, roic, asset_turnover, inventory_turnover, fixed_asset_turnover |
| `working_capital` | dso, dio, dpo, cash_conversion_cycle |
| `growth` (auto-registered) | `<base>_growth`, `<base>_cagr_{3,5,7}y` for revenue / ebit / ebitda / ni / fcf / capex / nwc / total_debt |

### Analyst-normalized EBIT methodology

`derived_lines.ebit()` is normalized to the institutional-analyst
convention rather than the raw `us-gaap:OperatingIncomeLoss` tag. Under
that convention, Operating Income excludes Unusual Items — which absorbs
goodwill impairment and held-for-sale asset writedowns (treated as part
of gain/loss on sale of assets). SEC issuers do not make those
reclassifications inside their XBRL, so the metric layer adds them back:

```
EBIT = OperatingIncomeLoss + goodwill_impairment + asset_impairment
```

`goodwill_impairment` and `asset_impairment` are concept chains in
`edgar/metrics/_concepts.py` that walk the relevant us-gaap tags
(`GoodwillImpairmentLoss`, `AssetImpairmentCharges`,
`TangibleAssetImpairmentCharges`,
`ImpairmentOfIntangibleAssetsExcludingGoodwill`,
`ImpairmentOfLongLivedAssetsHeldForUse`, ...) across both Income and
OperatingCashFlow categories. The add-back propagates automatically to
`ebit_margin`, `ebitda`, `ebitda_margin`, `ebit_growth`, `ebitda_growth`,
and `roic`.

A pretax+interest fallback covers hybrid-finance issuers (e.g. CarMax)
that route finance-segment interest through revenue and never tag
`OperatingIncomeLoss`.

### LTM and dealer-specific extensions

- `edgar.metrics.ltm.build_ltm_statement` rolls annual + quarterly facts
  into a trailing-twelve-months statement for issuers whose fiscal year
  ends off-calendar (e.g. CarMax FY ends Feb 28, America's Car-Mart FY
  ends April 30).
- `edgar/metrics/_statement_taxonomy.py` defines a frozen, closed-set
  slot taxonomy for the Balance Sheet (29 slots) and Cash Flow Statement
  (23 slots), plus generic / bank / insurance / reit overlays that
  re-interpret input slots **without re-partitioning the accounting
  identity** (`Assets ≡ Liabilities + Equity`,
  `CFO + CFI + CFF + FX ≡ ΔCash`). `_bs_prefilter.py` / `_cf_prefilter.py`
  deterministically classify each us-gaap BS/CF tag against that closed
  set with a name-polarity guardrail. This is a structural foundation
  layer; it is not yet consumed by a runtime buildup metric, so existing
  `derived_lines` outputs are unaffected. (The resolved tag→slot map
  itself is produced by an offline regeneration pipeline kept out of
  this public tree.)
- `edgar/_extension_mappings.py` declares dealer-specific extension
  concepts (floor-plan notes payable, non-recourse auto-finance notes,
  loaner-vehicle notes) that the Company Facts API does not expose by
  default. `XBRLParser.augment_with_extensions` injects them from the
  raw instance documents so total-debt and debt-ratio metrics are
  apples-to-apples with vendor templates.

### Known reconciliation gaps

Validated against an institutional auto-dealer comparables set
(Dec 2025) — EBIT margin within ±0.2pp on all names; EBIT growth
within ±2pp on most. Two residual gaps remain:

- **Held-for-sale impairment strips**: some issuers' analyst-normalized
  operating income reflects a footnote-driven partial strip of a
  held-for-sale asset-impairment charge that cannot be replicated from
  raw XBRL alone. EBIT-growth residual on affected names: ~+10pp.
- **Gain/loss on sale of a business**: stripping a
  `GainLossOnSaleOfBusiness` from operating income helps some issuers
  but over-corrects others, so it is not added back uniformly.
  EBIT-growth residual on affected names: ~+4pp.

For screening and ranking the engine is a viable free alternative to a
commercial comparables template. For point-estimate accuracy on issuers
with active divestiture activity, read the 10-K footnotes manually.

## Project Structure

```
edgar-connect/
│
├── main.py                     # CLI entry point
├── web_app.py                  # Flask UI for browsing the index
├── pyproject.toml              # Build config + pinned dependencies
├── requirements.lock           # Hash-pinned lockfile (--require-hashes)
├── LICENSE                     # MIT license
├── README.md                   # Documentation
│
├── edgar_mcp/                  # MCP stdio server (optional [mcp] extra)
│   ├── __main__.py             # `python -m edgar_mcp` entry point
│   └── server.py               # FastMCP tools wrapping the edgar/ package
│
├── edgar/                      # Core package
│   ├── company_lookup.py       # CIK and company lookup
│   ├── filing_retrieval.py     # SEC submissions, Company Facts, Company Concept
│   ├── xbrl_parser.py          # Normalize XBRL facts by period and category
│   ├── tag_classifier.py       # Map XBRL tags to statement sections
│   ├── statement_extractor.py  # HTML/XML fallback when XBRL is incomplete
│   ├── data_formatter.py       # CSV / JSON / Excel / HTML / console output
│   ├── company_classifier.py   # Build SIC/country/revenue index from SEC bulk data
│   ├── _extension_mappings.py  # Dealer-specific extension concept rules
│   └── metrics/                # Vendor-comparable metric engine
│       ├── registry.py         # NormalizedStatement + @register decorator
│       ├── _concepts.py        # Fallback chains for us-gaap concepts
│       ├── derived_lines.py    # ebit, ebitda, fcf, total_debt, ...
│       ├── margins.py          # *_margin metrics
│       ├── ratios.py           # debt/capital, interest coverage, ...
│       ├── returns.py          # roa, roe, roic, turnover
│       ├── working_capital.py  # dso, dio, dpo, ccc
│       ├── growth.py           # auto-registers <base>_growth + _cagr_{3,5,7}y
│       ├── ltm.py              # trailing-twelve-months rollup
│       ├── _statement_taxonomy.py # Frozen BS/CF slot taxonomy + industry overlays
│       ├── _bs_prefilter.py    # Deterministic BS tag->slot prefilter + guardrail
│       └── _cf_prefilter.py    # Deterministic CF tag->slot prefilter + guardrail
│
├── config/
│   ├── settings.py             # Configuration settings
│   ├── constants.py            # API endpoints, filing types, etc.
│   └── sic_codes.py            # SIC -> sub-industry mapping
│
├── utils/
│   ├── validators.py           # Input validation
│   ├── cache.py                # File-based pickle cache
│   └── helpers.py              # retry_request with exponential backoff
│
└── data/                       # Persisted classifier inputs
    ├── company_index.json
    └── sec_tag_mapping.json
```

## Limitations

- Data availability depends on company's XBRL filings with the SEC
- Only U.S. publicly traded companies and foreign companies that file with the SEC are available
- Historical data may be limited for some companies
- Companies may use different taxonomies or tag names for similar financial concepts
- The SEC API has rate limiting restrictions (10 requests/second)

## Troubleshooting

### Common Issues

1. **Company Not Found**
   - Try searching by ticker symbol instead of company name
   - Verify the company is publicly traded and files with the SEC

2. **No Data Available**
   - Some companies may not have filed in XBRL format for older periods
   - Try using a different statement type or period type

3. **API Rate Limiting**
   - The tool implements caching to minimize API calls
   - Wait a few minutes and try again if you encounter rate limit errors

4. **Missing Financial Metrics**
   - Companies may use different taxonomy tags for reporting
   - Not all financial metrics are reported by all companies

## Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add some amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

Distributed under the MIT License. See `LICENSE` for more information.

## Acknowledgments

- This tool uses the SEC's EDGAR system and APIs for data access
- Thanks to the SEC for providing standardized financial data through their XBRL APIs
- Inspired by the need for accessible financial data for investment research and analysis