"""Growth + CAGR auto-registration.

For each base metric listed in `_GROWTH_BASES`, this module registers:
    <base>_growth          — 1-period change: (curr - prior) / prior
    <base>_cagr_3y         — 3-period compound annual growth rate
    <base>_cagr_5y         — 5-period CAGR
    <base>_cagr_7y         — 7-period CAGR

Periods are positional (reverse-chronological) and assumed equal-length within
a single pull. For `annual` data that's 1Y; for `quarterly` the "_cagr_3y" slug
is technically a 3-quarter rate. Callers should request annual data when
interpreting these as years.
"""

from __future__ import annotations

from typing import Callable

from edgar.metrics.registry import (
    NormalizedStatement,
    REGISTRY,
    register,
)

# Slugs whose registered functions return per-period values that growth
# metrics should be derivable from. Must be registered before this module
# imports — handled by the __init__.py import order.
_GROWTH_BASES: tuple[str, ...] = (
    "revenue",
    "cogs",
    "gross_profit",
    "ebit",
    "ebitda",
    "ni",
    "fcf",
    "fcf_unlev",
    "ebitda_less_capex",
    "capex",
    "nwc",
    "total_debt",
)


def _resolve_base_fn(slug: str) -> Callable[[NormalizedStatement], dict[str, float | None]]:
    """Look up a base metric's function, raising clearly if it's not registered."""
    spec = REGISTRY.get(slug)
    if spec is None:
        raise KeyError(f"growth base '{slug}' is not registered")
    return spec.fn


def _value_n_back(
    series: dict[str, float | None],
    periods: list[str],
    period: str,
    n: int,
) -> float | None:
    """Return the value n positions later in the reverse-chrono series."""
    try:
        idx = periods.index(period)
    except ValueError:
        return None
    if idx + n >= len(periods):
        return None
    return series[periods[idx + n]]


def _make_growth_fn(base_slug: str):
    base_fn = _resolve_base_fn(base_slug)

    def fn(s: NormalizedStatement) -> dict[str, float | None]:
        series = base_fn(s)
        out: dict[str, float | None] = {}
        for p in s.periods:
            curr = series[p]
            prior = _value_n_back(series, s.periods, p, 1)
            if curr is None or prior is None or prior == 0:
                out[p] = None
            else:
                out[p] = (curr - prior) / abs(prior)
        return out

    fn.__name__ = f"{base_slug}_growth"
    return fn


def _make_cagr_fn(base_slug: str, years: int):
    base_fn = _resolve_base_fn(base_slug)

    def fn(s: NormalizedStatement) -> dict[str, float | None]:
        series = base_fn(s)
        out: dict[str, float | None] = {}
        for p in s.periods:
            curr = series[p]
            anchor = _value_n_back(series, s.periods, p, years)
            if curr is None or anchor is None or anchor <= 0 or curr <= 0:
                # CAGR with sign-flips or zeros isn't meaningful
                out[p] = None
            else:
                out[p] = (curr / anchor) ** (1.0 / years) - 1.0
        return out

    fn.__name__ = f"{base_slug}_cagr_{years}y"
    return fn


def _register_all():
    base_descriptions = {spec.slug: spec.description for spec in REGISTRY.values()}
    base_statements = {spec.slug: spec.statements for spec in REGISTRY.values()}
    for base in _GROWTH_BASES:
        if base not in REGISTRY:
            # Skip if a base wasn't loaded (e.g. test scenarios)
            continue
        desc_base = base_descriptions.get(base, base)
        stmts = base_statements.get(base, ())

        register(
            slug=f"{base}_growth",
            description=f"Period-over-period growth in {base} ({desc_base}).",
            statements=stmts,
            unit="ratio",
            category="growth",
            needs_lookback=1,
        )(_make_growth_fn(base))

        for years in (3, 5, 7):
            register(
                slug=f"{base}_cagr_{years}y",
                description=f"{years}-period CAGR of {base}.",
                statements=stmts,
                unit="ratio",
                category="growth",
                needs_lookback=years,
            )(_make_cagr_fn(base, years))


_register_all()


# EPS growth metrics use the raw IS concepts directly (no base derivation).
def _eps_series(s: NormalizedStatement, concept: str) -> dict[str, float | None]:
    return s.get(concept)


for _eps_slug, _eps_concept in (("eps_basic", "eps_basic"), ("eps_diluted", "eps_diluted")):
    if f"{_eps_slug}_growth" in REGISTRY:
        continue

    def _make_eps_growth(concept_name: str = _eps_concept):
        def fn(s: NormalizedStatement) -> dict[str, float | None]:
            series = _eps_series(s, concept_name)
            out: dict[str, float | None] = {}
            for p in s.periods:
                curr = series[p]
                prior = _value_n_back(series, s.periods, p, 1)
                if curr is None or prior is None or prior == 0:
                    out[p] = None
                else:
                    out[p] = (curr - prior) / abs(prior)
            return out
        return fn

    register(
        slug=f"{_eps_slug}_growth",
        description=f"Period-over-period growth in {_eps_slug}.",
        statements=("IS",),
        unit="ratio",
        category="growth",
        needs_lookback=1,
    )(_make_eps_growth())

    for _years in (3, 5, 7):
        def _make_eps_cagr(concept_name: str = _eps_concept, n: int = _years):
            def fn(s: NormalizedStatement) -> dict[str, float | None]:
                series = _eps_series(s, concept_name)
                out: dict[str, float | None] = {}
                for p in s.periods:
                    curr = series[p]
                    anchor = _value_n_back(series, s.periods, p, n)
                    if curr is None or anchor is None or anchor <= 0 or curr <= 0:
                        out[p] = None
                    else:
                        out[p] = (curr / anchor) ** (1.0 / n) - 1.0
                return out
            return fn

        register(
            slug=f"{_eps_slug}_cagr_{_years}y",
            description=f"{_years}-period CAGR of {_eps_slug}.",
            statements=("IS",),
            unit="ratio",
            category="growth",
            needs_lookback=_years,
        )(_make_eps_cagr())
