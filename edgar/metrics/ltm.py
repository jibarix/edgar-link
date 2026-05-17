"""LTM (last-twelve-months) rollup for non-Dec filers.

Synthesizes an LTM income/cash-flow figure for an arbitrary target date by
combining annual + quarterly YTD facts:

    LTM(target) = YTD_curr(target) + Annual(prior_FY) - YTD_prior(target - 1y)

Balance-sheet items are taken as the snapshot at `target`.

Returns a 2-period NormalizedStatement (LTM_curr + LTM_prior) so growth/ratio
metrics in the registry work transparently.

Limitations:
- Commercial data vendors' proprietary LTM definitions may differ slightly
  (e.g. some calendarize or estimate Dec for Feb-year-end filers). Our
  rollup uses the strict sum of trailing-four-quarters from filed SEC facts.
- The XBRL parser picks one fact per end-date without distinguishing 3-month
  vs YTD instances. Empirically it returns the longest-running period (YTD),
  which is what this module assumes.
"""
from __future__ import annotations

from edgar.metrics.registry import NormalizedStatement


# Categories whose values are CUMULATIVE flow figures (must roll up to LTM).
# Balance-sheet/equity categories are point-in-time snapshots.
_FLOW_CATEGORIES = {
    "Revenue", "Income", "EPS",
    "OperatingCashFlow", "InvestingCashFlow", "FinancingCashFlow",
    "OCI",
}


def _find_match(periods: list[str], predicate) -> str | None:
    for p in periods:
        if predicate(p):
            return p
    return None


def _ltm_value(
    flow_meta: dict | None,
    ann_meta: dict | None,
    target_q: str,
    prior_fy: str,
    prior_ytd: str,
) -> float | None:
    """LTM = YTD_curr + Annual_prior - YTD_prior. Returns None if any leg missing."""
    if flow_meta is None or ann_meta is None:
        return None
    ytd_curr = flow_meta.get("values", {}).get(target_q)
    ytd_prior = flow_meta.get("values", {}).get(prior_ytd)
    ann_prior = ann_meta.get("values", {}).get(prior_fy)
    if ytd_curr is None or ytd_prior is None or ann_prior is None:
        return None
    return ytd_curr + ann_prior - ytd_prior


def build_ltm_statement(
    annual_norm: dict,
    qtr_norm: dict,
    as_of: str,
    chain_overrides: dict | None = None,
) -> tuple[NormalizedStatement, str] | None:
    """Build a 2-period LTM NormalizedStatement (LTM_curr + LTM_prior).

    Returns (statement, ltm_curr_period) or None if rollup not feasible.

    `as_of` is an ISO date (e.g. "2025-12-31"). The LTM period ends on the
    most recent quarterly end-date that is <= as_of.
    """
    a_periods: list[str] = annual_norm.get("periods", [])
    q_periods: list[str] = qtr_norm.get("periods", [])
    if not a_periods or not q_periods:
        return None

    # Pick the latest quarterly period <= as_of
    target_q = _find_match(q_periods, lambda p: p <= as_of)
    if target_q is None:
        return None

    # Prior FY annual end (strictly before target_q)
    prior_fy = _find_match(a_periods, lambda p: p < target_q)
    if prior_fy is None:
        return None

    # Prior YTD: same month-day as target_q, but in the PRIOR fiscal year
    # (i.e. ends within prior_fy's year — before prior_fy's year-end).
    # For CRMT FY26 Q2 (2025-10-31), prior_ytd = 2024-10-31 (FY25 Q2 YTD).
    target_mmdd = target_q[5:]
    prior_ytd = _find_match(
        q_periods,
        lambda p: p < prior_fy and p[5:] == target_mmdd,
    )
    if prior_ytd is None:
        return None

    # LTM_prior: identical formula evaluated one fiscal year earlier.
    ltm_prior_end = prior_ytd
    fy_two_back = _find_match(a_periods, lambda p: p < ltm_prior_end)
    ytd_two_back = _find_match(
        q_periods,
        lambda p: fy_two_back is not None and p < fy_two_back and p[5:] == target_mmdd
    ) if fy_two_back else None

    a_metrics = annual_norm.get("metrics", {})
    q_metrics = qtr_norm.get("metrics", {})

    # Build the union of metric keys (some keys only appear in one slice)
    all_keys = set(a_metrics) | set(q_metrics)
    ltm_metrics: dict[str, dict] = {}

    for key in all_keys:
        # Prefer quarterly meta for the metric stub (has the most recent values)
        meta = q_metrics.get(key) or a_metrics.get(key)
        cat = meta.get("category", "")

        if cat in _FLOW_CATEGORIES:
            # LTM = YTD_curr + Annual_prior - YTD_prior
            q_meta = q_metrics.get(key)
            a_meta = a_metrics.get(key)
            ltm_curr = _ltm_value(q_meta, a_meta, target_q, prior_fy, prior_ytd)
            if ltm_prior_end and ytd_two_back and fy_two_back:
                ltm_prior = _ltm_value(q_meta, a_meta, ltm_prior_end, fy_two_back, ytd_two_back)
            else:
                ltm_prior = None
            values = {target_q: ltm_curr, ltm_prior_end: ltm_prior}
        else:
            # Balance sheet / equity: snapshot at each period
            q_meta = q_metrics.get(key)
            if q_meta:
                values = {
                    target_q: q_meta.get("values", {}).get(target_q),
                    ltm_prior_end: q_meta.get("values", {}).get(ltm_prior_end),
                }
            else:
                # Annual-only metric — try the FY closest to each LTM end
                a_meta = a_metrics.get(key, {})
                values = {
                    target_q: a_meta.get("values", {}).get(prior_fy),
                    ltm_prior_end: a_meta.get("values", {}).get(fy_two_back) if fy_two_back else None,
                }

        # Skip metrics with no usable data
        if all(v is None for v in values.values()):
            continue

        ltm_metrics[key] = {**meta, "values": values}

    synth = {
        "periods": [target_q, ltm_prior_end],
        "metrics": ltm_metrics,
        "metadata": {
            "period_type": "ltm",
            "as_of": as_of,
            "ltm_curr_end": target_q,
            "ltm_prior_end": ltm_prior_end,
            "prior_fy": prior_fy,
            "prior_ytd": prior_ytd,
            "fy_two_back": fy_two_back,
            "ytd_two_back": ytd_two_back,
        },
    }
    return NormalizedStatement(synth, chain_overrides), target_q
