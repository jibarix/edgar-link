"""MCP server exposing edgar_search_tool over stdio.

Tools:
    lookup_company          - resolve a name or ticker to SEC CIK
    get_financial_statement - normalized BS / IS / CF / EQ / CI by period
    get_concept             - time series for a single XBRL concept
    search_companies        - filter the local SIC/country/revenue index
    list_metrics            - list registered derived metrics
    compute_metric          - compute a derived metric (ratios, margins, growth, etc.)
"""
from __future__ import annotations

import functools
import logging
from typing import Literal

from mcp.server.fastmcp import FastMCP

from edgar.company_lookup import format_cik, search_company
from edgar.filing_retrieval import FilingRetrieval
from edgar.xbrl_parser import XBRLParser
from edgar import metrics as edgar_metrics
from utils.validators import is_valid_concept

# Per-invocation audit trail. Each tool call logs its name, parameters, and
# outcome (ok / error / exception) — the forensic record an MCP server should
# leave. Output payloads are intentionally NOT logged (they can be large and
# may carry sensitive data); only the outcome class is. Logging is configured
# by the host process; the server only emits records.
logger = logging.getLogger("edgar_mcp")


def _audit(fn):
    """Log a tool invocation's name, arguments, and outcome at INFO."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        logger.info("tool=%s args=%r", fn.__name__, kwargs if kwargs else args)
        try:
            result = fn(*args, **kwargs)
        except Exception:
            logger.exception("tool=%s outcome=exception", fn.__name__)
            raise
        if isinstance(result, dict) and "error" in result:
            logger.info("tool=%s outcome=error detail=%s",
                        fn.__name__, result["error"])
        else:
            logger.info("tool=%s outcome=ok", fn.__name__)
        return result
    return wrapper


mcp = FastMCP("edgar-search")

_filings = FilingRetrieval()
_parser = XBRLParser()

_classifier_index: dict | None = None


def _resolve_cik(cik_or_ticker: str) -> str | None:
    s = str(cik_or_ticker).strip()
    if s.isdigit():
        return format_cik(s)
    matches = search_company(s)
    return matches[0]["cik"] if matches else None


def _get_classifier_index() -> dict:
    # Lazy: build on first call, cache for the life of the server process.
    global _classifier_index
    if _classifier_index is None:
        from edgar.company_classifier import load_index
        _classifier_index = load_index()
    return _classifier_index


@mcp.tool()
@_audit
def lookup_company(query: str) -> list[dict]:
    """Resolve a company name or ticker to its SEC CIK.

    Returns up to 5 fuzzy matches; an exact name or ticker yields one row.
    """
    return search_company(query)


@mcp.tool()
@_audit
def get_financial_statement(
    cik_or_ticker: str,
    statement_type: Literal["BS", "IS", "CF", "EQ", "CI", "ALL"] = "ALL",
    period_type: Literal["annual", "quarterly", "ytd"] = "annual",
    num_periods: int = 4,
) -> dict:
    """Retrieve a normalized financial statement for a company.

    `cik_or_ticker` accepts either a 10-digit CIK or a name/ticker that
    will be resolved via the SEC company-tickers feed.
    """
    cik = _resolve_cik(cik_or_ticker)
    if cik is None:
        return {"error": f"No company matched '{cik_or_ticker}'"}

    facts = _filings.get_company_facts(cik)
    if not facts:
        return {"error": f"No company facts available for CIK {cik}"}

    normalized = _parser.parse_company_facts(
        facts,
        statement_type=statement_type,
        period_type=period_type,
        num_periods=num_periods,
    )
    if not normalized:
        return {"error": f"No {statement_type} data for CIK {cik}"}

    return {
        "cik": cik,
        "entity_name": facts.get("entityName", ""),
        "statement_type": statement_type,
        "period_type": period_type,
        **normalized,
    }


@mcp.tool()
@_audit
def get_concept(
    cik_or_ticker: str,
    concept: str,
    taxonomy: Literal["us-gaap", "ifrs-full", "dei"] = "us-gaap",
) -> dict:
    """Retrieve the full historical time series for a single XBRL concept."""
    if not is_valid_concept(concept):
        return {"error": (
            f"Invalid concept name: {concept!r}. Use a bare XBRL element "
            "name such as 'AssetsCurrent' or 'Revenues'."
        )}
    cik = _resolve_cik(cik_or_ticker)
    if cik is None:
        return {"error": f"No company matched '{cik_or_ticker}'"}
    data = _filings.get_company_concept(cik, taxonomy, concept)
    if not data:
        return {"error": f"No data for {taxonomy}:{concept} on CIK {cik}"}
    return data


@mcp.tool()
@_audit
def search_companies(
    sic: str | None = None,
    industry: str | None = None,
    country_inc: str | None = None,
    revenue_country: str | None = None,
    name_substring: str | None = None,
    limit: int = 25,
) -> dict:
    """Filter the local company classification index.

    All filters are AND-combined. `sic` matches the exact 4-digit code,
    `industry` is a case-insensitive substring of the broad industry name,
    country filters use ISO 3166-1 alpha-2 codes (e.g. "US", "JP").
    The index is built from SEC Financial Statement Data Sets; if the
    `data/company_index.json` file is absent the result will be empty.
    """
    index = _get_classifier_index()
    if not index:
        return {
            "error": (
                "Company index not built. Run "
                "`python -m edgar.company_classifier --build` to generate "
                "data/company_index.json."
            ),
            "results": [],
        }

    needle = name_substring.lower() if name_substring else None
    industry_needle = industry.lower() if industry else None

    out: list[dict] = []
    for cik, info in index.items():
        if sic and info.get("sic") != sic:
            continue
        if industry_needle and industry_needle not in info.get("industry", "").lower():
            continue
        if country_inc and info.get("country_inc") != country_inc.upper():
            continue
        if revenue_country and info.get("revenue_country") != revenue_country.upper():
            continue
        if needle and needle not in info.get("name", "").lower():
            continue
        out.append({"cik": cik, **info})
        if len(out) >= limit:
            break

    return {"count": len(out), "results": out}


@mcp.tool()
@_audit
def list_metrics(category: str | None = None) -> dict:
    """List registered derived metrics.

    `category` filters by metric category: "ratio", "margin", "return",
    "wc" (working capital), "derived_line", "growth", or None for all.
    """
    slugs = edgar_metrics.list_slugs(category=category)
    return {"count": len(slugs), "metrics": slugs}


@mcp.tool()
@_audit
def compute_metric(
    slug: str,
    cik_or_ticker: str,
    period_type: Literal["annual", "quarterly", "ytd"] = "annual",
    num_periods: int = 3,
) -> dict:
    """Compute a derived metric for a company.

    `slug` is a metric registered in edgar.metrics.REGISTRY (call
    `list_metrics()` to enumerate). Internally fetches enough history to
    satisfy the metric's `needs_lookback` requirement (averages, CAGR).
    """
    spec = edgar_metrics.REGISTRY.get(slug)
    if spec is None:
        return {"error": f"Unknown metric slug: {slug}"}

    cik = _resolve_cik(cik_or_ticker)
    if cik is None:
        return {"error": f"No company matched '{cik_or_ticker}'"}

    facts = _filings.get_company_facts(cik)
    if not facts:
        return {"error": f"No company facts available for CIK {cik}"}

    fetch_n = num_periods + spec.needs_lookback
    normalized = _parser.parse_company_facts(
        facts,
        statement_type="ALL",
        period_type=period_type,
        num_periods=fetch_n,
    )
    if not normalized:
        return {"error": f"No data for CIK {cik}"}

    stmt = edgar_metrics.NormalizedStatement(normalized)
    series = spec.fn(stmt)

    # Trim to the requested num_periods (drop oldest lookback rows)
    visible_periods = stmt.periods[:num_periods]
    visible_values = {p: series.get(p) for p in visible_periods}

    return {
        "cik": cik,
        "entity_name": facts.get("entityName", ""),
        "slug": slug,
        "description": spec.description,
        "unit": spec.unit,
        "category": spec.category,
        "statements_used": list(spec.statements),
        "periods": visible_periods,
        "values": visible_values,
    }
