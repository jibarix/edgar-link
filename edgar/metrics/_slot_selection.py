"""Hierarchy-aware tag selection inside a closed-set slot.

The Layer-2 buildup (`statement_buildup`) resolves every us-gaap concept
to a frozen slot, but a single filer routinely tags the *same economic
quantity at several levels of the XBRL hierarchy* into one slot:

  * net **and** gross **and** components
    (``PropertyPlantAndEquipmentNet`` + ``…Gross`` + ``BuildingsAndImprovementsGross`` + ``Land``)
  * a bundled superset **and** the narrow line
    (``CashCashEquivalentsAndShortTermInvestments`` + ``CashAndCashEquivalentsAtCarryingValue``)
  * a net rollup **and** its decomposition
    (``DeferredTaxAssetsNet`` + ``…DeferredIncome`` + ``…Other``)

A naive "sum every tag in the slot" double-counts all three patterns —
that is exactly what blew the Microsoft balance-sheet identity out by
~$720 B. This module is the fix, and it deliberately mirrors the
income-statement selection model in ``_concepts.py``: **pick the single
most authoritative reported line per slot rather than summing the
hierarchy.**

Resolution-source precedence is the backbone. The shipped deterministic
pre-filter is high-precision / low-recall: when it is *confident* about a
tag it has found THE canonical reported line for that slot. The compiled
fan-out map only carries what the pre-filter left ambiguous —
components, bundles, alternates, and the occasional mis-slotted
disclosure. So a ``prefilter`` tag categorically beats a ``map`` tag in
the same slot.

Slot shape then decides what to do with the survivors:

  * **single-line slots** (cash, ppe_net, goodwill, retained_earnings, …)
    are ONE reported figure — collapse to a single tag.
  * **additive slots** (the ``other_*`` catch-alls, ``common_stock_apic``
    = par + APIC, ``short_term_debt`` = CP + current LTD, and every
    non-subtotal cash-flow slot, which is additive by construction) keep
    the distinct survivors and sum them, after stripping gross/net and
    rollup double-counts.

The synthetic balanced filer the offline oracle uses has exactly one tag
per slot, so every rule below is a no-op there (a lone candidate is
returned unchanged) — the structural contract is preserved.
"""
from __future__ import annotations

from dataclasses import dataclass

from edgar.metrics._statement_taxonomy import BS_SLOTS_BY_ID, CF_SLOTS_BY_ID

# ── slot-shape partition ─────────────────────────────────────────────
# Catch-all "other …" slots legitimately aggregate several distinct
# reported line items; common_stock_apic = par + APIC (+ preferred par)
# and short_term_debt = commercial paper + current LTD + ST borrowings
# are additive of distinct lines too. Every other non-subtotal BS slot
# is a single reported figure. All non-subtotal CF slots are additive by
# construction (each is a distinct flow), so CF is treated as additive
# everywhere — the de-dup rules still strip net/gross & rollup overlap.
_CATCH_ALL_BS: frozenset[str] = frozenset({
    "other_current_assets",
    "other_noncurrent_assets",
    "other_current_liabilities",
    "other_noncurrent_liabilities",
})
ADDITIVE_BS: frozenset[str] = _CATCH_ALL_BS | frozenset({
    "common_stock_apic",
    "short_term_debt",
})

#: Concepts that resolve into a BS/CF slot in the archived fan-out but
#: are really income-statement DISCLOSURES, not balance-sheet inputs.
#: Documented stopgap: dropped here until the fan-out is regenerated with
#: the corrected adjudication (then this set should shrink to empty).
#: ``RevenueRemainingPerformanceObligation`` (deferred-revenue backlog,
#: ASC 606) was mis-slotted into ``other_noncurrent_liabilities`` and is
#: a $375 B phantom on Microsoft alone.
DISCLOSURE_DENY: frozenset[str] = frozenset({
    "RevenueRemainingPerformanceObligation",
})

# Bundle / derivation markers in a us-gaap concept name. The more of
# these a name carries, the more it is a combined/derived figure rather
# than the clean headline line, so it loses the single-line tie-break.
_HEAVY_MARKERS = ("Restricted", "Including", "Combined", "Aggregate",
                  "Consolidated")
_LIGHT_MARKERS = ("And",)


def _join_weight(concept: str) -> int:
    """Heuristic 'how bundled is this name' score (lower = cleaner)."""
    w = 0
    for m in _HEAVY_MARKERS:
        w += 3 * concept.count(m)
    for m in _LIGHT_MARKERS:
        w += concept.count(m)
    return w


@dataclass(frozen=True)
class Candidate:
    """One tag competing for a slot in one period."""

    concept: str
    source: str          # 'prefilter' | 'map'
    raw_val: float       # as-filed magnitude
    signed_val: float    # sign already applied by the buildup


def _drop(concept: str) -> bool:
    return concept in DISCLOSURE_DENY


def _net_gross_dedup(
    kept: list[Candidate],
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Drop ``<Stem>Gross`` when ``<Stem>Net`` / ``<Stem>`` also present."""
    names = {c.concept for c in kept}
    survivors: list[Candidate] = []
    dropped: list[tuple[Candidate, str]] = []
    for c in kept:
        if c.concept.endswith("Gross"):
            stem = c.concept[:-len("Gross")]
            if (stem + "Net") in names or stem in names:
                dropped.append((c, "gross_of_net_pair"))
                continue
        survivors.append(c)
    return survivors, dropped


def _net_family_dedup(
    kept: list[Candidate],
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Within an additive slot, a ``<Stem>Net`` tag is the rollup of its
    same-stem siblings (``DeferredTaxAssetsNet`` subsumes
    ``DeferredTaxAssetsDeferredIncome`` / ``…Other``). Keep the Net
    rollup, drop the same-family components it already totals."""
    stems = [c.concept[:-len("Net")] for c in kept
             if c.concept.endswith("Net")]
    survivors: list[Candidate] = []
    dropped: list[tuple[Candidate, str]] = []
    for c in kept:
        if c.concept.endswith("Net"):
            survivors.append(c)
            continue
        if any(c.concept.startswith(s) for s in stems):
            dropped.append((c, "net_family_component"))
        else:
            survivors.append(c)
    return survivors, dropped


def _prefix_rollup_dedup(
    kept: list[Candidate],
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Within an additive slot, drop a tag whose name is a strict
    prefix of another surviving tag: the longer is the more specific
    child, the shorter is the parent rollup that already counts it."""
    names = sorted({c.concept for c in kept}, key=len)
    rollup = {
        a for i, a in enumerate(names)
        for b in names[i + 1:]
        if b.startswith(a)
    }
    survivors: list[Candidate] = []
    dropped: list[tuple[Candidate, str]] = []
    for c in kept:
        if c.concept in rollup:
            dropped.append((c, "prefix_rollup_parent"))
        else:
            survivors.append(c)
    return survivors, dropped


def select(
    slot_id: str, candidates: list[Candidate], *, statement: str,
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Reduce a slot's competing tags to the non-double-counting set.

    Returns ``(kept, dropped)`` where ``dropped`` is a list of
    ``(candidate, reason)`` for provenance. A single candidate is always
    returned unchanged (the synthetic-oracle invariant).
    """
    dropped: list[tuple[Candidate, str]] = []
    pool: list[Candidate] = []
    for c in candidates:
        if _drop(c.concept):
            dropped.append((c, "disclosure_deny"))
        else:
            pool.append(c)
    if len(pool) <= 1:
        return pool, dropped

    additive = statement == "CF" or slot_id in ADDITIVE_BS

    # Rule 1 — pre-filter confidence is canonical for a SINGLE-LINE slot.
    # A single-line slot holds one reported figure, so if the high-
    # precision pre-filter named it, the ambiguous fan-out map tags are
    # hierarchy noise (components / bundles / mis-slots) and are dropped.
    # A catch-all/additive slot legitimately aggregates several DISTINCT
    # reported lines (which may be a mix of confident and map), so the
    # confident tag is NOT a supersede signal there.
    if not additive and any(c.source == "prefilter" for c in pool):
        survivors = []
        for c in pool:
            if c.source == "prefilter":
                survivors.append(c)
            else:
                dropped.append((c, "map_superseded_by_confident"))
        pool = survivors
        if len(pool) <= 1:
            return pool, dropped

    # Rule 2 — never sum a Gross alongside its Net/base sibling.
    pool, d = _net_gross_dedup(pool)
    dropped.extend(d)
    if len(pool) <= 1:
        return pool, dropped

    if not additive:
        # Rule 3 — single-line slot: keep the one cleanest headline tag.
        pool.sort(key=lambda c: (_join_weight(c.concept),
                                 len(c.concept), c.concept))
        head, *rest = pool
        for c in rest:
            dropped.append((c, "single_line_collapse"))
        return [head], dropped

    # Rule 4 — additive slot: collapse same-family Net rollups and
    # prefix parents, then sum the distinct survivors. NOTE: a catch-all
    # ``Other…`` rollup tag is deliberately NOT auto-superseding its
    # slot-mates here. Whether the issuer's ``Other<Section>`` line is a
    # superset of the slot's note-level members or merely one peer face
    # line among several is issuer-specific and not determinable from
    # tag names; collapsing on it undercounts the peer-line filers as
    # badly as summing over-counts the superset filers. The residual is
    # surfaced (reported_residual / identity_residual), per the design
    # contract — it is the honest face-vs-note coverage signal, left for
    # the separate vendor-numeric reconciliation, not silently guessed.
    pool, d = _net_family_dedup(pool)
    dropped.extend(d)
    pool, d = _prefix_rollup_dedup(pool)
    dropped.extend(d)
    return pool, dropped
