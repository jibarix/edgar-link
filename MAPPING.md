# The Mapping Process

How `edgar-connect` turns raw SEC filing tags into clean, comparable
financials. This is the single most important concept in the engine, so
this document explains it from the simple mental model down to the exact
code.

---

## 1. The one-sentence model

> **It is a VLOOKUP where the lookup result is a *ranked list of SEC tags
> to try*, and the engine returns the first one that has a number.**

A plain VLOOKUP returns one value. Ours returns *Plan A, Plan B, Plan C*,
because Apple, a bank, and Caterpillar all report the same real-world
figure ("revenue") under **different SEC tags**. The ranked list is what
lets you ask one question — "give me revenue" — and get the right answer
for every company.

The ranked lists live in one file: [`edgar/metrics/_concepts.py`](./edgar/metrics/_concepts.py).

---

## 2. What happens when you ask for a company

You ask for **Apple** (by name or ticker `AAPL`). End to end:

| Step | What happens | Where |
|------|--------------|-------|
| 1. **Find the company** | Resolve "Apple" / "AAPL" → SEC CIK number | `edgar/company_lookup.py` |
| 2. **Download filings** | Pull the SEC Company Facts API: one JSON of every number Apple ever reported, each labeled with an SEC tag (`Revenues`, `CashAndCashEquivalentsAtCarryingValue`, …) | `edgar/filing_retrieval.py` |
| 3. **Classify the tags** | Decide which statement each tag belongs to (Balance Sheet / Income / Cash Flow) and give it a clean display name | `edgar/tag_classifier.py` |
| 4. **(If needed) recover hidden tags** | A few issuers hide a figure in a non-standard tag the SEC strips out; a regex rule re-injects it under a standard name | `edgar/_extension_mappings.py` |
| 5. **Resolve logical concepts** | For each thing you care about (`revenue`, `ebitda`, `total_debt`, …) walk its ranked tag list and take the first tag with a value — **per period** | `edgar/metrics/_concepts.py` + `edgar/metrics/registry.py` |
| 6. **Compute & return** | Derived metrics (EBIT, EBITDA, FCF, ratios, LTM, beta) are computed on top of the resolved concepts and handed back via CLI or MCP | `edgar/metrics/*` |

Steps 3–5 together are "the mapping."

> **The runtime is the engine library, not the scripts.** When you call a
> company, the `edgar/` modules run (driven by MCP or the CLI). Everything
> in `scripts/` is validation/tooling and is **not** in the path of a
> company call.

### Two steps, not one: *classify* vs *resolve*

A common misread is to think the "big list" of tags does the whole job.
It does not — there are **two distinct steps backed by two different
files**:

| | Step 1 — **Classify** | Step 2 — **Resolve** |
|---|---|---|
| Question it answers | "Which statement/section is this tag, and what do we label it?" | "Which tag *is* this company's revenue (debt, …) number?" |
| File | `tag_classifier.py` + `data/sec_tag_mapping.json` | `_concepts.py` + `registry.py` |
| Granularity | one tag → one bucket (IS/BS/CF + category + display name) | one logical line → a **ranked list** of tags, first hit wins, **per period** |
| Unrecognized input | tag not in the list → **silently skipped** (footnotes, disclosures) | n/a |

Why both are needed: a single company can simultaneously carry
`Revenues`, `SalesRevenueNet`, *and* a contract-revenue tag. Step 1
classifies **all three** as "Income / Revenue" — it cannot tell you which
one is THE revenue figure. Step 2 is what picks the winner. Classifying
is bucketing; resolving is choosing.

---

## 3. How the date / period is decided

There are **two different behaviors**. Only one of them takes a calendar
date — and it is not the normal company call.

### A) Core engine — a normal company call (CLI / MCP)

You do **not** pick a calendar date. You pick:

- `period_type` → `annual` or `quarterly`
- `num_periods` → e.g. last 3

The engine returns the **most recent N periods of that type that exist in
the filings**, newest first. "Date" here just means "the latest N
filings." There is no *as-of an arbitrary date* logic in the core path.

### B) Comps workbook generator — the on-demand `--as-of` tool (archive)

This is the only place a real as-of date is honored. Given
`--as-of 2025-03-31` it decides:

- **Latest Annual** = the most recent fiscal year whose period-end is
  **on or before** the as-of date.
- **LTM** = trailing four quarters with period-end ≤ as-of, used when the
  latest annual is not already aligned to the as-of date.

So "give me financials as of March 2025" is a property of the comps
generator, not of a plain company call.

---

## 4. The resolver, precisely

The lookup list is **the same for every company**. What differs is
*which row wins* — and that is decided **independently for each period**,
not once for the whole company.

From `NormalizedStatement.get()` in
[`edgar/metrics/registry.py`](./edgar/metrics/registry.py):

> For each period, walk the chain top to bottom and pick the first
> non-`None` value. Stop early once every period is filled.

Why per-period and not "first tag that exists anywhere": issuers
**migrate tags mid-history**. Example documented in the code: ABG
reported revenue under `RevenueFromContractWithCustomer…` for years, then
switched back to `Revenues` for FY25 only. A whole-company "pick one tag"
rule would blank out FY25. Per-period resolution stitches the two eras
into one continuous series.

There is also a **per-comp-set override** hook (`chain_overrides`): a
caller can replace the global ranked list for one concept without a
global reorder that would regress other companies (used e.g. for REIT/LP
filers that report rental income under different tags). The override
fully replaces the global chain for that concept when present.

---

## 5. The four mapping layers and their source of truth

There are **two independent normalization paths** that do not share
state. Path A produces the metrics/comps numbers. Path B produces the
structural balance-sheet / cash-flow buildup. A change in one does **not**
automatically flow to the other.

```
SEC Company Facts API ──(us-gaap/dei/srt only; extensions stripped)──┐
   + optional per-filing instance XML (extensions)                   │
                                                                     ▼
                                              Layer 1.5  _extension_mappings.py
                                              regex re-tag ext: → canonical
                                                                     │
              ┌──────────────────── two non-shared paths ────────────┴─────────────┐
              ▼                                                                     ▼
  PATH A  (metrics / comps)                                  PATH B  (structural buildup)
  Layer 1   tag_classifier.py                                Layer 2b  statement_buildup.py
   1. _SKIP_TAGS        → drop                                1. _bs/_cf_prefilter (high precision)
   2. _BUILTIN_TAGS     (~150 curated)                        2. _bs/_cf_slot_map.py (GENERATED)
   3. sec_tag_mapping.json (~3,500)                           3. _slot_selection dedupe
              │                                               → 29 BS / 23 CF closed slots
              ▼  (xbrl_parser admission gate)                 subtotals DERIVED, not tagged in
  Layer 2a  _concepts.py  CONCEPT_CHAINS                      identity residual surfaced
   logical name → ranked (cat, tag) list
   resolved per period in registry.py
              ▼
  derived_lines / margins / ratios / ltm / beta
```

### Layer 1 — tag → statement classification
**File:** `edgar/tag_classifier.py`
**Backing data:** `data/sec_tag_mapping.json` (~3,500 tags, auto-derived
from the SEC Financial Statement Data Set).
Two-tier, priority-ordered lookup:
1. `_SKIP_TAGS` — footnote / disclosure pollution → dropped.
2. `_BUILTIN_TAGS` — ~150 hand-curated tags carrying clean display name,
   sort order, indent, `is_subtotal`, section.
3. `sec_tag_mapping.json` — the broad auto-derived fallback.

Tags in none of these are silently skipped. Every hit is stamped with
`source: builtin|sec` for provenance.

### Layer 1.5 — company-extension re-tagging
**File:** `edgar/_extension_mappings.py`
The Company Facts API strips anything that is not us-gaap/dei/srt, so
industry-specific lines (dealer floor-plan debt; Deere's post-FY2022
captive-finance long-term debt) are invisible in the flat feed. An
`ExtensionRule` is a regex on the concept's local name; when it matches,
the fact is re-tagged under a synthetic `ext:` canonical concept,
**summed per period** across all matching raw tags, and injected back
into the same pipeline. Rule sets (`DEALER_RULES`,
`EQUIPMENT_FINANCE_RULES`) are SIC/industry-gated and regex-scoped, so
they are inert and regression-safe for issuers they do not target.

### Layer 2a — concept fallback chains (the VLOOKUP lists)
**File:** `edgar/metrics/_concepts.py`
**Resolver:** `NormalizedStatement.get()` in `edgar/metrics/registry.py`
`CONCEPT_CHAINS`: ~50 logical names (`revenue`, `cogs`,
`long_term_debt_noncurrent`, `cfo`, …) each mapped to an ordered list of
`(category, us-gaap concept)` tuples. The resolver returns, per period,
the value of the first tag in the list that has data. Inline comments
document *why* each fallback exists (e.g. KMX hybrid-finance interest,
Deere `DebtCurrent`, NEN limited-partnership equity).

### Layer 2b — structural BS/CF closed-set slots
**Files:** `edgar/metrics/_statement_taxonomy.py`,
`_bs_prefilter.py` / `_cf_prefilter.py`,
`_bs_slot_map.py` / `_cf_slot_map.py`,
`_slot_selection.py`, `statement_buildup.py`
A *separate* path that deliberately bypasses `xbrl_parser`'s admission
gate. A frozen closed set of **29 BS / 23 CF slots**, each with an
expected balance polarity used as a merge guardrail. Resolution order:
1. Deterministic prefilter (high-precision, low-recall) — confident hits win.
2. Whatever is left ambiguous → `_bs_slot_map.py` / `_cf_slot_map.py`,
   the **generated** code form of an archived, adjudicated fan-out.
3. `_slot_selection` strips gross/net and rollup double-counts within a slot.
4. Subtotals are **derived from the slotted inputs, never tagged in**;
   reported subtotals are kept only as a drift/guardrail signal; the
   accounting-identity residual (`Assets − (Liab + Equity)`,
   `CFO + CFI + CFF + FX − ΔCash`) is surfaced, not hidden.

Industry overlays (bank / insurance / REIT) **re-label, they do not
re-partition** — the numeric partition and the identity are unchanged.

---

## 6. Worked example: `revenue`

The list, verbatim from `_concepts.py`:

```python
"revenue": [
    ("Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax"),  # try 1st
    ("Revenue", "Revenues"),                                             # then this
    ("Revenue", "SalesRevenueNet"),                                      # then this
    ("Revenue", "SalesRevenueGoodsNet"),                                 # then this
    ("Revenue", "RevenueFromContractWithCustomerIncludingAssessedTax"),  # last resort
]
```

Same 5-row list runs for every company. Each falls through to the first
row it actually reported, **per period**:

| Company | Winning row | Why |
|---|---|---|
| Apple | Row 1 `RevenueFromContractWithCustomerExcludingAssessedTax` | Modern post-ASC-606 tag |
| Caterpillar | Row 2 `Revenues` | Tags the generic concept |
| Older retailer | Row 3 `SalesRevenueNet` | Pre-2018 reporting style |
| ABG | Row 1 for most years, Row 2 for FY25 | Mid-history tag migration — handled per period |

> Note: the "winning row" column is the *expected* resolution from tag
> conventions. Confirming it for a live company requires an actual SEC
> pull, which needs `EDGAR_IDENTITY` set.

---

## 7. Module & script reference

### Engine modules involved in mapping

| Module | Role |
|---|---|
| `edgar/company_lookup.py` | Name/ticker → CIK resolution |
| `edgar/filing_retrieval.py` | Rate-limited SEC Company Facts / Concept / instance retrieval, cache-backed |
| `edgar/tag_classifier.py` | Layer 1 — tag → statement/category/display, two-tier (builtin → SEC map) |
| `edgar/_extension_mappings.py` | Layer 1.5 — regex re-tag of stripped company-extension concepts |
| `edgar/xbrl_parser.py` | Builds the normalized per-period statement (the admission gate for Path A) |
| `edgar/metrics/_concepts.py` | Layer 2a — `CONCEPT_CHAINS`, the ranked VLOOKUP lists |
| `edgar/metrics/registry.py` | `NormalizedStatement.get()` — the per-period chain resolver + override hook |
| `edgar/metrics/derived_lines.py` | Computes EBIT/EBITDA/FCF/etc. on top of resolved concepts |
| `edgar/metrics/_statement_taxonomy.py` | Layer 2b — frozen closed-set BS/CF slot taxonomy + polarity guardrail |
| `edgar/metrics/_bs_prefilter.py`, `_cf_prefilter.py` | Layer 2b — deterministic high-precision tag→slot prefilter |
| `edgar/metrics/_bs_slot_map.py`, `_cf_slot_map.py` | Layer 2b — **generated** ambiguous-tag→slot map (code form of archived fan-out) |
| `edgar/metrics/_slot_selection.py` | Layer 2b — strips double-counts within a slot |
| `edgar/metrics/statement_buildup.py` | Layer 2b — assembles structural BS/CF, derives subtotals, reports identity residual |

### Scripts (clean tree `scripts/`)

| Script | Purpose |
|---|---|
| `scripts/smoke_test_metrics.py` | Live AAPL smoke test of the parser + metric registry; prints a compact multi-period table of hand-checked metrics. Requires `EDGAR_IDENTITY`. The fastest way to verify the mapping end to end. |
| `scripts/gen_lockfile.py` | Regenerates `requirements.lock` (exact versions + sha256) from a `pip --dry-run --report` output. Not part of the mapping itself; listed for completeness. |

### Scripts that live in the archive tree (not shipped here)

The bulk Layer-2b adjudication data and its tooling are kept out of the
public tree. The committed `_bs_slot_map.py` / `_cf_slot_map.py` are the
**generated code form** of that data.

| Script (archive) | Purpose |
|---|---|
| `scripts/gen_slot_map.py` | Compiles the adjudicated fan-out JSON into `_bs_slot_map.py` / `_cf_slot_map.py`. Run this to regenerate the slot maps after the fan-out changes. |
| reconcile / sweep scripts | Validation harnesses that score the engine against a vendor oracle (CapIQ screening reports). Test scaffolding, not part of the runtime mapping. |

---

## 8. How to extend the mapping

- **New issuer reports a logical line under an unhandled tag** → add the
  tag to the end of that concept's list in `_concepts.py`. Lowest
  priority placement avoids regressing companies that already resolve.
- **Industry hides a line in a stripped extension** → add an
  `ExtensionRule` (regex) to the appropriate rule set in
  `_extension_mappings.py`; keep the regex narrow so it stays inert
  elsewhere.
- **One comp set needs a different resolution order** → pass
  `chain_overrides` rather than reordering the global chain.
- **BS/CF slot coverage gap** → fix the prefilter or the adjudicated
  fan-out in the archive, then regenerate the slot maps with
  `python scripts/gen_slot_map.py`.

Guiding principle (from `CLAUDE.md`): prefer targeted, lowest-priority
concept-resolution fixes over broad global mapping reorderings.
