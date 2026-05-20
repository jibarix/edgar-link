# edgar-link

Analyst-grade SEC EDGAR financials. A Python library, CLI, and MCP
server that pulls XBRL filings directly from the SEC and layers an
analyst-normalized metric engine on top.

Unlike raw filing tools, `edgar-link` aims to return usable financial
outputs such as normalized revenue, EBIT, EBITDA, FCF, leverage, margins,
returns, growth, LTM rollups, and structural balance-sheet / cash-flow
buildups. It also includes 5-year peer beta / R^2 utilities. No API key, no
subscription; the live data source is the SEC.

> SEC identity required. SEC fair-access policy requires every requester to
> identify themselves. Before any live call, set:
>
> ```bash
> export EDGAR_IDENTITY="Your Name your@email.com"   # macOS/Linux
> $env:EDGAR_IDENTITY = "Your Name your@email.com"   # Windows PowerShell
> ```
>
> Without it the SEC will throttle requests. Do not hardcode, borrow, or commit
> someone else's identity. `SEC_EDGAR_USER_AGENT` is also accepted as an
> alias for compatibility with other SEC tooling.

## What it does

- Looks up public companies by name or ticker and resolves them to SEC CIKs
- Pulls Company Facts and Company Concept data from the SEC XBRL APIs
- Normalizes issuer-specific XBRL tags into reusable statement data
- Computes analyst-normalized derived metrics on top of that normalized data
- Builds structural balance-sheet and cash-flow statements from a frozen slot taxonomy
- Exposes the engine through a local CLI and an MCP server

## SEC API usage

This project primarily uses the SEC XBRL APIs:

- Company Facts API
  - `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`
  - Full XBRL fact history for a filer in one response
- Company Concept API
  - `https://data.sec.gov/api/xbrl/companyconcept/CIK##########/taxonomy/tag.json`
  - Historical values for one concept such as `Assets` or `Revenues`

The engine uses:

- request identification via `EDGAR_IDENTITY`
- local caching to reduce repeated pulls
- rate-limited access intended to stay within SEC fair-access expectations

More information: https://www.sec.gov/developer

## Installation

### Requirements

- Python 3.11+
- `pip`

> The pinned dependency set (`pandas`, `numpy`) requires Python 3.11+,
> so 3.11 is the hard floor. The hash-verified clean-room install and
> CI path are validated on Windows / CPython 3.11 (`requirements.lock`
> is Windows/cp311-specific); a Linux lock is deferred until a fresh
> dependency resolve is safe under the active supply-chain incident
> policy.

### Install from GitHub

No PyPI release yet.

```bash
# library + metric engine
pip install "git+https://github.com/jibarix/edgar-link.git#egg=edgar-link"

# with MCP support
pip install "edgar-link[mcp] @ git+https://github.com/jibarix/edgar-link.git"
```

### Development install

```bash
git clone https://github.com/jibarix/edgar-link.git
cd edgar-link
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # macOS/Linux
pip install -e .
```

Dependencies are declared in [`pyproject.toml`](./pyproject.toml) and pinned to
reviewed versions. `requirements.lock` is the corresponding hash-pinned lockfile.

## Usage

Three ways to drive the engine. They share the same Python core; pick
the one that fits the workflow.

|  | Talk to Claude (MCP) | CLI | Library |
|---|---|---|---|
| **Best for** | Ad-hoc questions, exploration, no coding | One-shot pulls, scripted or scheduled exports | Building analyses, pipelines, custom code |
| **Invocation** | Natural-language prompt to an MCP client | `python main.py ...` | `from edgar.metrics import ...` |
| **Setup** | Install `[mcp]` extra, register the server once | Install the runtime | Install the runtime |
| **Output** | Conversational response in the client | Console table or Excel / CSV / JSON / HTML file | Python objects (dicts, DataFrames) |
| **Lookup by** | Name, ticker, or CIK | Name, ticker, or `--cik` | CIK (resolve names via `edgar.company_lookup`) |
| **`EDGAR_IDENTITY`** | In the MCP client's env config | In your shell env | In your shell env |

### Talk to Claude (MCP)

The conversational path. The same engine is exposed as an MCP stdio
server, so an MCP-aware client (Claude Code, Claude Desktop) can call
it directly in natural language:

- *"Show Apple's last 3 fiscal years of revenue, EBIT, and FCF."*
- *"Compute ROIC for ticker MSFT, last 5 quarters."*
- *"Find software filers (SIC 7372) with U.S. revenue dominance."*

Install and register the server once — see
[MCP server reference](#mcp-server-reference) below — then just ask.
No code from the caller.

### CLI

Interactive mode:

```bash
python main.py
```

Command-line mode:

```bash
python main.py --company "Apple Inc" --statement-type BS --period-type annual --num-periods 3 --output-format excel
```

Supported statement types:

- `BS` - Balance Sheet
- `IS` - Income Statement
- `CF` - Cash Flow Statement
- `EQ` - Equity Statement
- `CI` - Comprehensive Income
- `ALL` - All supported statements

### Library (Python import)

For building analyses on top of the engine in your own code:

```python
from edgar.filing_retrieval import FilingRetrieval
from edgar.xbrl_parser import XBRLParser
from edgar.metrics import REGISTRY, NormalizedStatement, compute, list_slugs

filings = FilingRetrieval()
parser  = XBRLParser()

facts = filings.get_company_facts("0000320193")   # Apple Inc.
normalized = parser.parse_company_facts(
    facts,
    statement_type="IS",
    period_type="annual",
    num_periods=3,
)
ns = NormalizedStatement(normalized)

ebit_series   = compute("ebit", ns)               # {period: value}
margin_slugs  = list_slugs(category="margins")    # discover what's registered
spec          = REGISTRY["roic"]                  # MetricSpec (fn, unit, ...)
```

`EDGAR_IDENTITY` must be set in the environment for any live retrieval.
See [Architecture](#architecture) for module boundaries and
[MCP server reference](#mcp-server-reference) for the equivalent tool
surface.

## MCP server reference

Install, registration, configuration, and the tool surface for the
stdio server invoked via `python -m edgar_mcp`.

### Install MCP extra

```bash
pip install -e ".[mcp]"
```

### Register with Claude Code

```bash
claude mcp add edgar -e EDGAR_IDENTITY="Your Name your@email.com" -- python -m edgar_mcp
```

### MCP config example

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

### MCP tools

| Tool | Purpose |
|------|---------|
| `lookup_company(query)` | Resolve a name or ticker to SEC CIK candidates |
| `get_financial_statement(cik_or_ticker, statement_type, period_type, num_periods)` | Return normalized BS / IS / CF / EQ / CI / ALL data by period |
| `get_concept(cik_or_ticker, concept, taxonomy)` | Return a full historical time series for one XBRL concept |
| `search_companies(sic, industry, country_inc, revenue_country, name_substring, limit)` | Filter the local company classification index |
| `list_metrics(category)` | Enumerate registered derived metrics |
| `compute_metric(slug, cik_or_ticker, period_type, num_periods)` | Compute one derived metric series with the required internal lookback |

`search_companies` reads `data/company_index.json`; build it first with:

```bash
python -m edgar.company_classifier --build
```

## Architecture

### 1. Retrieval

- [`edgar/filing_retrieval.py`](./edgar/filing_retrieval.py)
  - SEC submissions, Company Facts, Company Concept, filing instance retrieval
- [`edgar/company_lookup.py`](./edgar/company_lookup.py)
  - ticker / company-name lookup and CIK resolution

### 2. Statement normalization

- [`edgar/xbrl_parser.py`](./edgar/xbrl_parser.py)
  - converts Company Facts into categorized, periodized statement data
- [`edgar/tag_classifier.py`](./edgar/tag_classifier.py)
  - maps raw XBRL concepts into statement categories

### 3. Derived metrics

The metric engine is registry-based. Metric functions register themselves in
[`edgar/metrics/registry.py`](./edgar/metrics/registry.py), and the public
surface is imported through [`edgar/metrics/__init__.py`](./edgar/metrics/__init__.py).

Main metric modules:

| Module | Examples |
|--------|----------|
| `derived_lines.py` | revenue, gross_profit, ebit, ebitda, fcf, total_debt |
| `margins.py` | ebit_margin, ebitda_margin, ni_margin, fcf_margin |
| `ratios.py` | debt_to_capital, debt_to_equity, current_ratio, quick_ratio |
| `returns.py` | roa, roe, roic, asset_turnover |
| `working_capital.py` | dso, dio, dpo, cash_conversion_cycle |
| `growth.py` | `<base>_growth`, `<base>_cagr_{3,5,7}y` |
| `ltm.py` | trailing-twelve-months rollups |

### 4. Structural BS / CF buildup

The repo also includes a second normalization path for balance-sheet and
cash-flow structure:

- [`edgar/metrics/_statement_taxonomy.py`](./edgar/metrics/_statement_taxonomy.py)
  - frozen closed-set slot taxonomy
- [`edgar/metrics/_bs_prefilter.py`](./edgar/metrics/_bs_prefilter.py)
  - deterministic balance-sheet tag prefilter with polarity guardrails
- [`edgar/metrics/_cf_prefilter.py`](./edgar/metrics/_cf_prefilter.py)
  - deterministic cash-flow tag prefilter with polarity guardrails
- [`edgar/metrics/statement_buildup.py`](./edgar/metrics/statement_buildup.py)
  - derives structural BS / CF buildups from raw Company Facts

Important design rule:

- reported subtotal tags are kept for provenance and drift checking
- they are not treated as raw input lines to be summed into the buildup

### 5. Beta utilities

[`edgar/metrics/beta.py`](./edgar/metrics/beta.py) computes 5-year monthly
beta and R^2 versus the S&P 500 from Yahoo monthly bars.

What it does today:

- peer beta / R^2 regression
- one row per ticker
- aligned monthly return window

What it does not currently implement:

- bottom-up beta chain
- unlever -> cash-correct -> total-beta -> relever workflow

## Analyst-normalized EBIT

`derived_lines.ebit()` is intentionally not just raw
`us-gaap:OperatingIncomeLoss`.

Current normalization:

```text
EBIT = OperatingIncomeLoss + goodwill_impairment + asset_impairment
```

This is meant to move the output closer to institutional analyst convention for
names where unusual impairments sit inside reported operating income. A
pretax-plus-interest fallback is also used for some hybrid-finance issuers that
do not tag `OperatingIncomeLoss` cleanly.

## Scripts

The current `scripts/` directory is focused on validation, maintenance,
and workbook/reporting workflows:

| Script | Purpose |
|--------|---------|
| `scripts/smoke_test_metrics.py` | Live AAPL smoke test for the parser + metric registry. Prints a compact multi-period table of hand-checked metrics. Requires `EDGAR_IDENTITY` or `SEC_EDGAR_USER_AGENT`. |
| `scripts/gen_lockfile.py` | Regenerates `requirements.lock` from `pip --dry-run --report ...` output using exact versions and `sha256` hashes. |
| `scripts/update_sec_tag_mapping.py` | Maintenance tool for `data/sec_tag_mapping.json`. Forward-only integrity check via a sha256 manifest, plus an additive merge of new us-gaap tags from a fresh SEC Financial Statement Data Set quarter. |
| `scripts/update_company_index.py` | Maintenance tool for `data/company_index.json`. Forward-only integrity check via a sha256 manifest, plus a snapshot rebuild of the company classification index from one or more fresh SEC Financial Statement Data Set quarters. |
| `scripts/build_comps.py` | Build a styled multi-peer comparables workbook from `data/company_index.json` (no anchor company required). Filters by SIC plus optional name / subindustry / country, pulls Company Facts per peer, and writes one Excel with a `Universe` sheet (classification fields), a `Metrics` matrix (peers × metric × relative fiscal period), CapIQ-mirror `Screening_24col` / `Screening_36col` snapshot sheets (LTM, point-in-time at `--as-of`), per-peer drilldown sheets (BS / IS / CF stacked), and an `About` methodology sheet. Styled header band, freeze panes, auto-filter, accounting number formats. Optional `--extensions` merges captive-finance extension XBRL; 5Y monthly β + R² vs ^GSPC is on by default (fail-soft on Yahoo errors / `<24` months of history) and disabled with `--no-beta`. Requires `EDGAR_IDENTITY` or `SEC_EDGAR_USER_AGENT` for live runs; `--dry-run` previews the peer set offline; `--no-capiq-layout` skips the Screening / submissions / quarterly path for a faster build. |

Lockfile regeneration flow:

```bash
pip install --dry-run --report report.json --ignore-installed -e .
python scripts/gen_lockfile.py report.json requirements.lock
```

Tag-mapping update flow (after a new FSDS quarter is published):

```bash
python scripts/update_sec_tag_mapping.py init                   # one-time, baselines the current file
python scripts/update_sec_tag_mapping.py check                  # verify nobody hand-edited the mapping
python scripts/update_sec_tag_mapping.py update 2026q1          # dry-run: diff + review report
python scripts/update_sec_tag_mapping.py update 2026q1 --apply  # merge new tags; needs_review held back
```

Company-index rebuild flow (after one or more new FSDS quarters are
published):

```bash
python scripts/update_company_index.py init                                   # one-time, baselines the current file
python scripts/update_company_index.py check                                  # verify nobody hand-edited the index
python scripts/update_company_index.py rebuild 2025q4 2026q1                  # dry-run: snapshot diff
python scripts/update_company_index.py rebuild 2025q4 2026q1 --apply          # replace the index, rotate the manifest
```

Comparables workbook flow (no anchor company; universe is selected by
filtering `data/company_index.json`):

```bash
# Preview the peer set without any SEC calls (offline)
python scripts/build_comps.py --sic 5500 \
    --exclude-name casey murphy copart openlane camping lazydays \
    --dry-run

# Live build (writes output/comps_<label>_<period>_<YYYYMMDD>.xlsx).
# Requires EDGAR_IDENTITY or SEC_EDGAR_USER_AGENT in the environment.
python scripts/build_comps.py --sic 5500 \
    --exclude-name casey murphy copart openlane camping lazydays \
    --num-periods 5
```

Each row in the resulting `Universe` sheet carries the classification
fields already present in `company_index.json` (SIC, industry,
subindustry, country of incorporation, dominant revenue country, and
the per-country revenue mix where the issuer tags it). The `Metrics`
sheet is a peers × (metric, relative fiscal period) matrix; peers with
different fiscal-year ends line up because columns are FY 0 / FY -1 /
FY -2 ... rather than absolute dates. The CapIQ-mirror `Screening_24col`
and `Screening_36col` sheets give a single point-in-time snapshot at
`--as-of` (LTM = trailing 4 quarters whose period-end is ≤ as-of for
non-Dec filers, else the FY-aligned annual); 36-col adds 6 trailing
LTM revenue columns. Forward analyst-estimate columns (CapIQ cols
26–29) are blank by design — EDGAR has no equivalent and they are
never fabricated. Per-peer drilldown sheets stack the normalized BS /
IS / CF line items with the peer's own period dates as columns. The
`About` sheet records the filter set, the as-of date, the period basis,
and which columns are blank by design.

## Project structure

```text
edgar-link/
|-- main.py
|-- pyproject.toml
|-- requirements.lock
|-- README.md
|-- LICENSE
|-- scripts/
|   |-- smoke_test_metrics.py
|   |-- gen_lockfile.py
|   |-- update_sec_tag_mapping.py
|   |-- update_company_index.py
|   `-- build_comps.py
|-- edgar_mcp/
|   |-- __main__.py
|   `-- server.py
|-- edgar/
|   |-- company_lookup.py
|   |-- filing_retrieval.py
|   |-- xbrl_parser.py
|   |-- tag_classifier.py
|   |-- statement_extractor.py
|   |-- data_formatter.py
|   |-- company_classifier.py
|   |-- _extension_mappings.py
|   |-- market_data/
|   `-- metrics/
|       |-- registry.py
|       |-- _concepts.py
|       |-- _statement_taxonomy.py
|       |-- _bs_prefilter.py
|       |-- _cf_prefilter.py
|       |-- _bs_slot_map.py
|       |-- _cf_slot_map.py
|       |-- statement_buildup.py
|       |-- derived_lines.py
|       |-- margins.py
|       |-- ratios.py
|       |-- returns.py
|       |-- working_capital.py
|       |-- growth.py
|       |-- ltm.py
|       `-- beta.py
|-- config/
|-- utils/
`-- data/
```

## Limitations

- Data quality depends on the issuer's XBRL filings with the SEC
- Some companies use different taxonomies or inconsistent tags for similar concepts
- Some analyst-normalized adjustments cannot be reproduced from raw XBRL alone
- Live SEC usage is subject to throttling and availability limits
- The company classifier index must be built locally before `search_companies` is useful
- Beta utilities currently cover peer beta / R^2 only, not the full bottom-up beta chain

## Troubleshooting

1. Company not found
   - Try the ticker instead of the full company name
   - Verify the company is an SEC filer

2. No data available
   - Older periods may not be available in XBRL
   - Try a different statement type or period type

3. MCP `search_companies` returns an error
   - Build the local index first:
     - `python -m edgar.company_classifier --build`

4. Smoke test fails or throttles
   - Confirm `EDGAR_IDENTITY` is set
   - `scripts/smoke_test_metrics.py` performs live SEC calls

5. Missing derived metrics
   - Not all filers expose every concept needed for every metric
   - Use `list_metrics()` to inspect the public metric catalog, then test one metric at a time with `compute_metric()`

## Contributing

1. Create a feature branch
2. Keep metric semantics explicit and avoid casual dependency changes
3. Validate live SEC-dependent changes when possible
4. Regenerate `requirements.lock` if dependencies change
5. Open a pull request

## License

Distributed under the MIT License. See [`LICENSE`](./LICENSE).
