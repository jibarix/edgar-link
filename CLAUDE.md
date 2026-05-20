See @README.md for the product overview and @pyproject.toml for package metadata and dependency pins.

# edgar-link

## What This Repo Is

- `edgar-link` is an analyst-grade SEC EDGAR financial engine, not just a filing scraper.
- The core value is converting raw SEC XBRL / Company Facts data into normalized, vendor-comparable financial outputs.
- Favor changes that preserve metric comparability and stable downstream outputs over changes that only increase raw data surface area.

## Core Architecture

- `edgar/filing_retrieval.py`: SEC retrieval, rate-limited HTTP access, and cache-backed source pulls.
- `edgar/xbrl_parser.py`: normalizes Company Facts into categorized, periodized statement data.
- `edgar/metrics/`: registry-backed derived metrics such as revenue, EBIT, EBITDA, FCF, debt, margins, returns, and growth.
- `edgar/metrics/_statement_taxonomy.py`: frozen closed-set Balance Sheet / Cash Flow slot taxonomy for structural classification.
- `edgar/metrics/_bs_prefilter.py` and `edgar/metrics/_cf_prefilter.py`: deterministic prefilters that classify confident BS/CF tags, preserve reported subtotals only for provenance, and leave ambiguous tags for downstream fan-out.
- `edgar_mcp/server.py`: MCP tool surface over lookup, normalized statements, concepts, and derived metrics.
- `main.py`: a CLI interface over the engine, not the core product.

## Project Invariants

- Live SEC calls require `EDGAR_IDENTITY`. Do not run live retrieval or smoke tests without it.
- Never hardcode or commit a real SEC identity, API credential, or personal contact detail.
- Keep company lookup / search resolution issues conceptually separate from financial-engine correctness where possible.
- Prefer targeted concept-resolution fixes over broad global mapping reorderings; issuer- and industry-specific tagging differences are common.
- Preserve the closed-set BS/CF taxonomy. Raw tags should map into structural slots; analyst-normalized or vendor-like adjustments belong in Layer-2 computed metrics, not in tag-level classification targets.
- Do not classify raw tags into subtotal slots as if they were input lines. Reported subtotals are provenance / guardrail signals; the engine should derive structural buildup from inputs.
- Maintain the accounting identities the taxonomy is built around: `Assets = Liabilities + Equity` and `CFO + CFI + CFF + FX = ΔCash`.
- Preserve public metric slugs and MCP tool names / return shapes unless the user explicitly asks for a breaking change.

## Dependency Rules

- Dependency versions are intentionally pinned and reviewed for supply-chain safety.
- Do not add or bump packages casually.
- If dependencies change, explain why, verify the selected versions, and keep `requirements.lock` in sync.
- Regenerate the lockfile with:
  - `pip install --dry-run --report report.json --ignore-installed -e .`
  - `python scripts/gen_lockfile.py report.json requirements.lock`

## Working Rules

- When adding or changing a metric, use the existing registry / decorator pattern in `edgar.metrics`.
- Keep metric semantics explicit. If a metric is meant to be analyst-normalized rather than a raw SEC line, preserve that intent in code and documentation.
- When working in the BS/CF taxonomy layer, preserve the deterministic prefilter philosophy: low-recall / high-precision auto-classification, subtotal-aware handling, and polarity contradiction guardrails.
- Keep cross-statement concepts aligned when they are intentionally shared, especially D&A and impairment handling between the cash-flow prefilter and the EBIT / EBITDA metric logic.
- Avoid moving business logic into the MCP layer when it belongs in the reusable engine.
- If a change affects parser output, check whether it also affects derived metrics, the CLI, and MCP responses.
- Keep `CHANGELOG.md` in sync with substantive commits. The file follows Keep-a-Changelog with an `[Unreleased]` section between version tags; new entries go there under `Added` / `Changed` / `Fixed` / `Removed` / `Validation`. Update it in the same commit as the change whenever practical. Substantive = anything that touches behavior, public API, CI, dependencies, supply-chain pins, build/install, or developer-facing tooling (new scripts, new maintenance flows). README- or comment-only edits are not logged (the 0.1.0 / 0.1.1 entries set that precedent). When a release is cut, the `[Unreleased]` entries get promoted under the new version heading.

## Validation

- Preferred live validation for parser / metric changes:
  - `python scripts/smoke_test_metrics.py`
- Useful manual entry points:
  - `python main.py`
  - `python -m edgar_mcp`
  - `python -m edgar.company_classifier --build`
- If live SEC validation is not possible, say so explicitly and limit claims accordingly.
