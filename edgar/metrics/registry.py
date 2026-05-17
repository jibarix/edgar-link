"""Metric registry, normalized-statement wrapper, and compute dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from edgar.metrics._concepts import CONCEPT_CHAINS

# ── NormalizedStatement ───────────────────────────────────────────────


class NormalizedStatement:
    """Thin wrapper over XBRLParser output with a concept resolver.

    Constructed once per company-pull. Metric functions call `.get(concept)`
    or `.line(...)` to extract per-period values without caring which
    us-gaap tag the issuer used.
    """

    def __init__(self, raw: dict, chain_overrides: dict | None = None):
        self.periods: list[str] = raw.get("periods", [])
        self._metrics: dict[str, dict] = raw.get("metrics", {})
        # Per-comp-set fallback-chain overrides. Keyed by logical concept
        # name (same keys as CONCEPT_CHAINS); when present, the override
        # chain fully replaces the global one for that concept. Used to
        # adapt resolution to industry-specific tagging (e.g. REIT/LP
        # filers report rental income under Revenues / lease-income tags
        # instead of the contract-revenue tag dealers use) without a
        # global reorder that would regress other comp sets.
        self._chain_overrides: dict[str, list[tuple[str, str]]] = (
            chain_overrides or {}
        )
        # Index by (category, concept_name) for O(1) resolver lookup
        self._by_cat_concept: dict[tuple[str, str], dict] = {}
        for key, meta in self._metrics.items():
            cat = meta.get("category", "")
            tag = meta.get("tag", "")
            concept = tag.split(":")[-1] if tag else ""
            if cat and concept:
                self._by_cat_concept[(cat, concept)] = meta

    # ── concept access ──

    def get(
        self,
        concept_chain: str | Iterable[tuple[str, str]],
    ) -> dict[str, float | None]:
        """Resolve a concept (via fallback chain) to per-period values.

        Pass either:
            - a logical name registered in CONCEPT_CHAINS (e.g. "revenue")
            - an iterable of (category, concept_name) tuples

        Per-period merging: for each period, walks the chain and picks
        the first non-None value. This handles tag migrations where an
        issuer reports under concept A in older years and concept B in
        newer years (e.g. ABG migrated from RevenueFromContractWithCustomer
        back to Revenues for FY25 only).
        """
        if isinstance(concept_chain, str):
            chain = self._chain_overrides.get(concept_chain)
            if chain is None:
                chain = CONCEPT_CHAINS.get(concept_chain)
            if chain is None:
                raise KeyError(f"Unknown logical concept: {concept_chain}")
        else:
            chain = list(concept_chain)

        out: dict[str, float | None] = {p: None for p in self.periods}
        for category, concept in chain:
            meta = self._by_cat_concept.get((category, concept))
            if meta is None:
                continue
            values = meta.get("values", {})
            for p in self.periods:
                if out[p] is None:
                    v = values.get(p)
                    if v is not None:
                        out[p] = v
            if all(v is not None for v in out.values()):
                break
        return out

    def has(self, concept_chain: str) -> bool:
        """True if any concept in the chain has data for any period."""
        v = self.get(concept_chain)
        return any(x is not None for x in v.values())

    def prior_period(self, period: str) -> str | None:
        """Return the period one index later in the (reverse-chrono) series."""
        try:
            idx = self.periods.index(period)
        except ValueError:
            return None
        if idx + 1 >= len(self.periods):
            return None
        return self.periods[idx + 1]


# ── Metric registry ───────────────────────────────────────────────────


@dataclass
class MetricSpec:
    slug: str
    fn: Callable[[NormalizedStatement], dict[str, float | None]]
    description: str
    statements: tuple[str, ...]  # subset of ("BS", "IS", "CF", "EQ")
    unit: str = "ratio"  # "ratio" | "pct" | "USD" | "days" | "x" | "USD/shares"
    category: str = "other"  # "ratio" | "margin" | "return" | "wc" | "derived_line" | "growth"
    needs_lookback: int = 0  # extra periods required (for avg / growth / cagr)
    extra: dict = field(default_factory=dict)


REGISTRY: dict[str, MetricSpec] = {}


def register(
    slug: str,
    *,
    description: str,
    statements: tuple[str, ...],
    unit: str = "ratio",
    category: str = "other",
    needs_lookback: int = 0,
    **extra,
):
    """Decorator: register a metric function under `slug`."""

    def deco(fn: Callable):
        if slug in REGISTRY:
            raise ValueError(f"Duplicate metric slug: {slug}")
        REGISTRY[slug] = MetricSpec(
            slug=slug,
            fn=fn,
            description=description,
            statements=statements,
            unit=unit,
            category=category,
            needs_lookback=needs_lookback,
            extra=extra,
        )
        return fn

    return deco


def compute(slug: str, stmt: NormalizedStatement) -> dict[str, float | None]:
    """Dispatch a slug to its registered function."""
    spec = REGISTRY.get(slug)
    if spec is None:
        raise KeyError(f"Unknown metric: {slug}")
    return spec.fn(stmt)


def list_slugs(category: str | None = None) -> list[dict]:
    """Return registry entries (optionally filtered by category)."""
    out = []
    for spec in REGISTRY.values():
        if category and spec.category != category:
            continue
        out.append({
            "slug": spec.slug,
            "description": spec.description,
            "statements": list(spec.statements),
            "unit": spec.unit,
            "category": spec.category,
            "needs_lookback": spec.needs_lookback,
        })
    return out


# ── Numeric helpers used by metric functions ──────────────────────────


def safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return num / den


def safe_add(*vals: float | None) -> float | None:
    """Sum, treating None as 0 if at least one value is not None."""
    nonnull = [v for v in vals if v is not None]
    if not nonnull:
        return None
    return sum(nonnull)


def safe_sub(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def avg2(a: float | None, b: float | None) -> float | None:
    """Two-point average; returns None if either side is missing."""
    if a is None or b is None:
        return None
    return (a + b) / 2.0
