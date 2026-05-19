"""Layer-2 balance-sheet & cash-flow structural buildup.

Turns raw SEC Company Facts into the frozen closed-set structure defined
in ``_statement_taxonomy.py`` (29 BS slots / 23 CF slots), then derives
the subtotals *from the slotted inputs* and reports the accounting-
identity residual.

Design contract
---------------
* **Full closed-set coverage.** Every us-gaap BS/CF concept is resolved
  by the same two layers the offline pipeline used, in the same order:
  the shipped deterministic pre-filter (`_bs_prefilter` / `_cf_prefilter`,
  high-precision, low-recall) first; whatever it leaves *ambiguous*
  falls through to the compiled adjudicated map (`_bs_slot_map` /
  `_cf_slot_map`, the committed CODE form of the archived fan-out).
  Confident pre-filter hits never consult the map.
* **Subtotals are derived, never tagged in.** A concept the pre-filter
  recognises as a *reported subtotal* (``Assets``, ``LiabilitiesCurrent``,
  ``NetCashProvidedByUsedInOperatingActivities``, …) is NOT summed into
  the buildup. It is captured separately as a provenance / guardrail
  value so the engine can report reported-vs-derived drift. The
  structural totals are recomputed from the input slots only -- this is
  the CLAUDE.md invariant ("Do not classify raw tags into subtotal
  slots as if they were input lines").
* **One slot, one economic figure.** A filer tags the same quantity at
  several XBRL hierarchy levels into a slot (net + gross + components,
  bundle + narrow line). The slotted tags are NOT blindly summed:
  ``_slot_selection.select`` first reduces each slot to its
  non-double-counting set (pre-filter-confident beats ambiguous map,
  gross/net & rollup overlap stripped, single-line slots collapse to one
  reported line, additive slots sum the distinct survivors).
* **Identities are reported, not assumed.** BS exposes
  ``Assets - (Liabilities + Equity)``; CF exposes
  ``(CFO + CFI + CFF + FX) - ΔCash``. A non-zero residual is surfaced,
  not hidden -- it is the honest signal of tag double-count / coverage
  gaps in a single filer.
* **Industry overlays re-label, they do not re-partition.** ``sic``
  selects at most one of bank / insurance / reit overlay membership for
  presentation; the numeric partition and the identity are unchanged
  (see ``_statement_taxonomy.OVERLAYS``).

This module is a *new* consumer of the raw Company Facts payload; it
does NOT go through ``xbrl_parser`` (whose curated-map admission gate
would hide most of the closed-set universe), so existing
``derived_lines`` / parser outputs are unaffected.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from edgar.metrics import _bs_prefilter, _cf_prefilter
from edgar.metrics._bs_slot_map import BS_SLOT_MAP
from edgar.metrics._cf_slot_map import CF_SLOT_MAP
from edgar.metrics._filing_structure import (
    StatementStructure,
    parse_statement_structure,
)
from edgar.metrics._slot_selection import Candidate, select
from edgar.metrics._statement_taxonomy import (
    BANK,
    BS_SLOTS_BY_ID,
    CF_SLOTS_BY_ID,
    GENERIC,
    INSURANCE,
    OVERLAYS,
    REIT,
    Balance,
    Section,
)

_USD = ("USD",)
_ASSET_SECTIONS = (Section.CURRENT_ASSET, Section.NONCURRENT_ASSET)
_LIAB_SECTIONS = (Section.CURRENT_LIABILITY, Section.NONCURRENT_LIABILITY)
_EQUITY_SECTIONS = (Section.EQUITY,)


# ── concept -> slot resolution (pre-filter first, then compiled map) ──

def resolve_bs_slot(concept: str) -> tuple[str | None, str]:
    """(slot_id, source) for a us-gaap BS concept.

    source ∈ {'prefilter', 'map', 'subtotal', 'unclassified'}.
    'subtotal' => a reported subtotal: provenance only, not an input.
    """
    c = _bs_prefilter.classify(concept)
    if c.disposition == "confident":
        return c.slot, "prefilter"
    if c.disposition == "subtotal":
        return c.slot, "subtotal"
    slot = BS_SLOT_MAP.get(concept)
    if slot is not None:
        return slot, "map"
    # Genuinely BS-shaped (a pre-filter rule fired but the polarity
    # guardrail vetoed it) yet uncovered -> real coverage gap. A concept
    # no rule even touched is simply out of scope (IS / disclosure).
    return None, "unclassified" if c.rule is not None else "out_of_scope"


def resolve_cf_slot(concept: str) -> tuple[str | None, str]:
    """(slot_id, source) for a us-gaap CF concept. See resolve_bs_slot."""
    c = _cf_prefilter.classify(concept)
    if c.disposition == "confident":
        return c.slot, "prefilter"
    if c.disposition == "subtotal":
        return c.slot, "subtotal"
    slot = CF_SLOT_MAP.get(concept)
    if slot is not None:
        return slot, "map"
    return None, "unclassified" if c.rule is not None else "out_of_scope"


# ── structure-driven (per-filing tree) resolution ────────────────────
#
# In structured mode the filing's own R-file tree -- not tag morphology
# -- decides leaf vs subtotal. So a leaf is ALWAYS an input line: the
# pre-filter's "subtotal" disposition is overridden (the filing already
# proved this concept is a leaf here), and we fall through to the map.

#: us-gaap subtotal/total concept -> the reported_subtotals key it
#: provides for the reported-vs-derived identity check.
_BS_SUBTOTAL_KEY: dict[str, str] = {
    "AssetsCurrent": "current_assets",
    "Assets": "total_assets",
    "LiabilitiesAndStockholdersEquity": "total_assets",  # grand total
    "LiabilitiesCurrent": "current_liabilities",
    "Liabilities": "total_liabilities",
    "StockholdersEquity": "total_equity",
    "StockholdersEquityIncludingPortionAttributableToNoncontrolling"
    "Interest": "total_equity",
    "LiabilitiesAndStockholdersEquityAbstract": "total_assets",
}

#: enclosing-subtotal concept -> the catch-all slot an unmapped company
#: extension leaf inside that section falls back to.
_PARENT_FALLBACK_SLOT: dict[str, str] = {
    "AssetsCurrent": "other_current_assets",
    "Assets": "other_noncurrent_assets",
    "LiabilitiesCurrent": "other_current_liabilities",
    "Liabilities": "other_noncurrent_liabilities",
}


def _parent_fallback_slot(
    concept: str, structure: StatementStructure,
    fallback_map: dict[str, str],
) -> str | None:
    """Catch-all slot of the nearest enclosing *section* total.

    Walk the filer's subtotal chain upward from ``concept`` and return
    the first ancestor that is a recognized section total. A flat
    (un-indented) statement -- banks render this way -- nests a leaf
    under an intermediate roll-up subtotal rather than the section
    total, so the single nearest subtotal is not slot-mappable; the
    chain still reaches ``Assets`` / ``Liabilities`` / a CF section.
    """
    seen: set[str] = set()
    node = structure.parent.get(concept)
    while node and node not in seen:
        slot = fallback_map.get(node)
        if slot is not None:
            return slot
        seen.add(node)
        node = structure.parent.get(node)
    return None


def _resolve_bs_leaf_slot(
    concept: str, structure: StatementStructure,
) -> tuple[str | None, str]:
    """Resolve a filing-declared BS leaf to an INPUT slot.

    The filing already proved this is a leaf, so the pre-filter's
    subtotal verdict is ignored and the general map still applies.
    A declared face leaf the map cannot place -- a company extension
    OR an unmapped us-gaap line such as a bank's loan-loss allowance
    shown net on the face -- inherits its enclosing section's catch-all
    slot via the parent chain (``structure_parent``). Its sign then
    comes from the filer's own rendered presentation (see the build
    loop), so a parenthesised contra line nets correctly within the
    section instead of vanishing out_of_scope.
    """
    c = _bs_prefilter.classify(concept)
    if c.disposition == "confident":
        return c.slot, "prefilter"
    slot = BS_SLOT_MAP.get(concept)
    if slot is not None:
        return slot, "map"
    fb = _parent_fallback_slot(concept, structure, _PARENT_FALLBACK_SLOT)
    if fb is not None:
        return fb, "structure_parent"
    return None, "unclassified" if c.rule is not None else "out_of_scope"


def _structure_parent_contra_sign(
    concept: str, structure: StatementStructure, period: str,
    concepts: dict[str, dict], accession: str | None,
) -> int | None:
    """Sign of a ``structure_parent`` BS leaf from the filer's OWN
    declared parent subtotal, when that subtotal pins it unambiguously.

    Returns -1 (the declared parent proves this is a contra / negated
    line shown net on the face), +1 (proves it is additive), or None
    (the parent does not pin the sign -- keep the rendered-cell sign).

    The rendered cell is only an authoritative direction when the filer
    parenthesises the contra (JPM's loan-loss allowance). A REIT shows
    accumulated depreciation as a bare positive under a "Less ..."
    caption, so the rendered sign is +1 and the contra would be ADDED.
    The filing still declares the net subtotal, so its arithmetic is the
    real authority: when the parent subtotal has exactly ONE
    structure_parent child (this leaf), ``V_parent`` minus the trusted
    (mapped / prefiltered) siblings is exactly +/- this leaf's
    magnitude, and the matching sign is decisive. A parent with several
    ambiguous children (a flat bank ``Assets`` total) does not pin a
    single leaf -> None -> rendered sign, so AAPL/MSFT/JPM/MET, which
    never reach this single-child case, are unaffected.
    """
    parent = structure.parent.get(concept)
    if not parent or parent not in structure.subtotals:
        return None
    pdata = concepts.get(parent)
    cdata = concepts.get(concept)
    if pdata is None or cdata is None:
        return None
    v_parent = _instant_value(pdata, period, accession)
    val = _instant_value(cdata, period, accession)
    if v_parent is None or not val:
        return None
    trusted = 0.0
    ambiguous = 0
    for sib in structure.leaves:
        if structure.parent.get(sib) != parent:
            continue
        s_slot, s_src = _resolve_bs_leaf_slot(sib, structure)
        if s_slot is None or BS_SLOTS_BY_ID[s_slot].balance is Balance.NA:
            continue
        if s_src == "structure_parent":
            ambiguous += 1
            continue  # excluded from the trusted base
        scd = concepts.get(sib)
        if scd is None:
            continue
        sv = _instant_value(scd, period, accession)
        if sv is not None:
            trusted += _bs_signed(s_slot, sv)
    if ambiguous != 1:
        return None  # parent does not pin a single ambiguous leaf
    residual = v_parent - trusted
    if abs(abs(residual) - abs(val)) <= 1.0:
        return -1 if residual < 0 else 1
    return None


#: us-gaap CF subtotal/total concept -> the reported_subtotals key it
#: provides for the reported-vs-derived identity check. Mirrors
#: ``_BS_SUBTOTAL_KEY``; the variants match the pre-filter subtotal
#: rules plus the older non-restricted change-in-cash spelling.
_CF_SUBTOTAL_KEY: dict[str, str] = {
    "NetCashProvidedByUsedInOperatingActivities": "cfo",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations":
        "cfo",
    "NetCashProvidedByUsedInInvestingActivities": "cfi",
    "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations":
        "cfi",
    "NetCashProvidedByUsedInFinancingActivities": "cff",
    "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations":
        "cff",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    "PeriodIncreaseDecreaseIncludingExchangeRateEffect":
        "cf_change_in_cash",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    "PeriodIncreaseDecreaseExcludingExchangeRateEffect":
        "cf_change_in_cash",
    "CashAndCashEquivalentsPeriodIncreaseDecrease": "cf_change_in_cash",
}

#: enclosing-subtotal concept -> the catch-all CF slot an unmapped
#: company extension leaf inside that section falls back to.
_CF_PARENT_FALLBACK_SLOT: dict[str, str] = {
    "NetCashProvidedByUsedInOperatingActivities": "cf_other_operating",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations":
        "cf_other_operating",
    "NetCashProvidedByUsedInInvestingActivities": "cf_other_investing",
    "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations":
        "cf_other_investing",
    "NetCashProvidedByUsedInFinancingActivities": "cf_other_financing",
    "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations":
        "cf_other_financing",
}

#: CF Section -> coarse activity family. Two slots are in the "same
#: section" iff their families match; operating/investing/financing
#: totals collapse onto their input section. Cash-reconciliation slots
#: (fx, change-in-cash) have no family and are never re-sectioned.
_CF_SECTION_FAMILY: dict[Section, str] = {
    Section.OPERATING: "op",
    Section.OPERATING_TOTAL: "op",
    Section.INVESTING: "inv",
    Section.INVESTING_TOTAL: "inv",
    Section.FINANCING: "fin",
    Section.FINANCING_TOTAL: "fin",
}


#: Canonical indirect-method "start" concepts. On a flat feed these are
#: dominantly income-statement tags, so the deterministic pre-filter
#: deliberately will not claim them for CF and the fan-out only mapped
#: the less-ambiguous ``ProfitLoss``. But when the *filing itself*
#: declares one as the first operating leaf, the structure has resolved
#: that ambiguity -- it IS the cash-flow net-income start here.
_CF_NET_INCOME_STARTS: frozenset[str] = frozenset({
    "NetIncomeLoss",
    "ProfitLoss",
    "NetIncomeLossAvailableToCommonStockholdersBasic",
    "NetIncomeLossAllocatedToLimitedPartners",
    "IncomeLossFromContinuingOperationsIncludingPortionAttributable"
    "ToNoncontrollingInterest",
})


def _resolve_cf_leaf_slot(
    concept: str, structure: StatementStructure,
) -> tuple[str | None, str]:
    """Resolve a filing-declared CF leaf to an INPUT slot.

    The CF analog of ``_resolve_bs_leaf_slot``: the filing proved this
    is a flow line, so the pre-filter subtotal verdict is ignored, the
    general map still applies, and a declared face leaf the map cannot
    place -- a company extension OR an unmapped us-gaap flow such as an
    insurer's market-risk-benefit remeasurement shown inside operating
    activities -- inherits its enclosing section's catch-all
    (``cf_other_*``) slot via the parent chain. Its direction then comes
    from the filer's OWN rendered cell (the build loop already uses
    rendered sign for every CF leaf), so a parenthesised contra flow
    nets correctly within the section instead of vanishing
    out_of_scope.

    Structure-gated section override: when the general map's slot sits
    in a different activity section than the one the filer rendered the
    leaf in (its parent chain reaches a different ``NetCash…Activities``
    total), the filing wins -- the leaf is re-slotted to that section's
    ``cf_other_*`` catch-all. This corrects issuer/industry section
    disagreements (a bank reports fed-funds-purchased / repo as a
    financing flow; the generic map files the bundled tag under
    investing) without a name list or a global map edit. It is a no-op
    when map and filing agree (the well-formed case), so exactly
    reconciling filers are unaffected.
    """
    c = _cf_prefilter.classify(concept)
    if c.disposition == "confident":
        return c.slot, "prefilter"
    slot = CF_SLOT_MAP.get(concept)
    if slot is not None:
        sect = _parent_fallback_slot(
            concept, structure, _CF_PARENT_FALLBACK_SLOT)
        if sect is not None:
            fam_map = _CF_SECTION_FAMILY.get(CF_SLOTS_BY_ID[slot].section)
            fam_str = _CF_SECTION_FAMILY.get(CF_SLOTS_BY_ID[sect].section)
            if (fam_map is not None and fam_str is not None
                    and fam_map != fam_str):
                return sect, "structure_section"
        return slot, "map"
    # Structure-gated: a net-income-shaped leaf whose enclosing subtotal
    # is the operating section is the indirect-method start line. This
    # uses the filing's OWN tree to settle a flat-feed ambiguity; it is
    # not a global map change.
    if (concept in _CF_NET_INCOME_STARTS
            and _parent_fallback_slot(
                concept, structure, _CF_PARENT_FALLBACK_SLOT)
            == "cf_other_operating"):
        return "cf_net_income", "structure_start"
    # A declared face leaf the map cannot place -- company extension OR
    # unmapped us-gaap -- inherits its enclosing CF section's catch-all
    # (mirrors _resolve_bs_leaf_slot; the parent chain only reaches a
    # NetCash…Activities total for genuine in-section flows, so a
    # supplemental/non-cash line whose chain misses every section total
    # still stays out_of_scope).
    fb = _parent_fallback_slot(
        concept, structure, _CF_PARENT_FALLBACK_SLOT)
    if fb is not None:
        return fb, "structure_parent"
    return None, "unclassified" if c.rule is not None else "out_of_scope"


# ── signed contribution into the structural buildup ──────────────────

def _bs_signed(slot_id: str, val: float) -> float:
    """As-filed sign, except treasury stock is contra-equity.

    us-gaap reports asset / liability / common-stock / APIC magnitudes
    positive and the mixed-sign equity lines (retained earnings,
    AOCI, NCI) already signed, so a plain sum reconstructs each side.
    ``TreasuryStock*`` values are reported as a positive magnitude but
    REDUCE equity, so they are negated.
    """
    return -val if slot_id == "treasury_stock" else val


def _cf_signed(slot_id: str, val: float) -> float | None:
    """Flow sign from the slot's balance polarity.

    CREDIT = inflow / non-cash add-back (+), DEBIT = outflow (-),
    EITHER = an already-signed net reconciling item (as filed),
    NA = non-monetary (excluded).
    """
    bal = CF_SLOTS_BY_ID[slot_id].balance
    if bal is Balance.CREDIT or bal is Balance.EITHER:
        return val
    if bal is Balance.DEBIT:
        return -val
    return None  # NA


# ── period selection over the raw Company Facts payload ──────────────

def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _gaap_concepts(facts_data: dict) -> dict[str, dict]:
    facts = facts_data.get("facts", {})
    out: dict[str, dict] = {}
    for tax in ("us-gaap", "ext"):
        out.update(facts.get(tax, {}))
    return out


def _fiscal_month(concepts: dict[str, dict]) -> int:
    months: dict[int, int] = defaultdict(int)
    for cdata in concepts.values():
        for f in cdata.get("units", {}).get("USD", []):
            end = f.get("end")
            if end:
                months[_d(end).month] += 1
    return max(months, key=months.get) if months else 12


def _annual_period_ends(concepts: dict[str, dict], fmonth: int,
                         num_periods: int) -> list[str]:
    ends: set[str] = set()
    for cdata in concepts.values():
        for f in cdata.get("units", {}).get("USD", []):
            end = f.get("end")
            if end and _d(end).month == fmonth:
                ends.add(end[:10])
    return sorted(ends, reverse=True)[:num_periods]


def _norm_accn(a) -> str:
    return str(a).replace("-", "") if a else ""


def _pick(cands: list[dict], accn: str | None) -> float | None:
    """Choose one fact value from period-matched candidates.

    Default = latest-filed (the flat-path behaviour, oracle-stable).
    When ``accn`` is given (structure-driven mode reconstructs ONE
    specific filing) the fact tagged in THAT accession wins: a later
    10-Q can carry a restated/reclassified value for the same
    concept+date (Apple FY25 ``OtherAssetsNoncurrent``: 83,727 as filed
    in the 10-K vs 72,634 reclassified in a later 10-Q), and the
    structure-driven buildup must match the filing it is parsing, not
    the newest restatement.
    """
    if not cands:
        return None
    if accn:
        want = _norm_accn(accn)
        own = [f for f in cands if _norm_accn(f.get("accn")) == want]
        if own:
            cands = own
    best = None
    for f in cands:
        if best is None or f.get("filed", "") > best.get("filed", ""):
            best = f
    return None if best is None else best["val"]


def _instant_value(cdata: dict, period_end: str,
                    accn: str | None = None) -> float | None:
    """Point-in-time fact at period_end (balance sheet). See _pick."""
    cands = []
    for f in cdata.get("units", {}).get("USD", []):
        if f.get("end", "")[:10] != period_end:
            continue
        if f.get("start") and f["start"][:10] != period_end:
            continue  # a duration fact, not an instant
        if "val" in f:
            cands.append(f)
    return _pick(cands, accn)


def _duration_value(cdata: dict, period_end: str,
                    accn: str | None = None) -> float | None:
    """~Full-year duration fact ending at period_end (CF). See _pick."""
    cands = []
    for f in cdata.get("units", {}).get("USD", []):
        if f.get("end", "")[:10] != period_end or not f.get("start"):
            continue
        if "val" not in f:
            continue
        span = (_d(f["end"]) - _d(f["start"])).days
        if 350 <= span <= 380:
            cands.append(f)
    return _pick(cands, accn)


# ── result shape ─────────────────────────────────────────────────────

@dataclass
class BuildupResult:
    statement: str                       # 'BS' | 'CF'
    period: str                          # YYYY-MM-DD
    overlay: str                         # generic | bank | insurance | reit
    slots: dict[str, float]              # input slot_id -> signed sum
    slot_tags: dict[str, list[str]]      # slot_id -> contributing concepts
    subtotals: dict[str, float]          # DERIVED from inputs
    reported_subtotals: dict[str, float] # provenance (tagged, not summed)
    identity_residual: float             # see per-statement note
    reported_residual: dict[str, float]  # derived vs tagged drift
    unclassified: dict[str, float]       # BS/CF-shaped but uncovered
    dropped_tags: dict[str, list[tuple[str, str]]] = field(
        default_factory=dict)            # slot -> [(concept, drop reason)]
    overlay_members: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _overlay_for_sic(sic) -> str:
    if sic is None:
        return GENERIC
    try:
        n = int(str(sic).strip()[:4])
    except (TypeError, ValueError):
        return GENERIC
    if 6000 <= n <= 6029:
        return BANK
    if 6300 <= n <= 6411:
        return INSURANCE
    if n == 6798:
        return REIT
    return GENERIC


# ── balance sheet ────────────────────────────────────────────────────

def build_balance_sheet(facts_data: dict, *, num_periods: int = 1,
                         sic=None,
                         structure: StatementStructure | None = None,
                         accession: str | None = None,
                         ) -> list[BuildupResult]:
    """Structural balance-sheet buildup.

    When ``structure`` (a parsed filing R-file tree) is supplied, the
    PRIMARY path: only the filer's declared leaf lines are summed and
    its declared subtotals become reported-vs-derived provenance, so
    hierarchy double-count is removed at the source and the identity
    closes exactly for well-formed filers. With ``structure=None`` the
    legacy flat-Company-Facts path runs (pre-filter + map + the
    hierarchy-aware ``_slot_selection`` fallback).
    """
    concepts = _gaap_concepts(facts_data)
    if not concepts:
        return []
    overlay = _overlay_for_sic(sic)
    fmonth = _fiscal_month(concepts)
    periods = _annual_period_ends(concepts, fmonth, num_periods)
    results: list[BuildupResult] = []

    for period in periods:
        cand: dict[str, list[Candidate]] = defaultdict(list)
        reported: dict[str, float] = {}
        unclassified: dict[str, float] = {}

        slots: dict[str, float] = {}
        slot_tags: dict[str, list[str]] = {}
        dropped_tags: dict[str, list[tuple[str, str]]] = {}

        if structure:
            # PRIMARY: the filing's own tree. Leaves are disjoint inputs
            # by construction, so they are summed directly -- no
            # hierarchy de-dup needed. Declared subtotals are provenance.
            acc: dict[str, float] = defaultdict(float)
            for concept in structure.leaves:
                cdata = concepts.get(concept)
                if cdata is None:
                    continue  # extension leaf absent from Company Facts
                val = _instant_value(cdata, period, accession)
                if val is None:
                    continue
                slot, src = _resolve_bs_leaf_slot(concept, structure)
                if slot is None:
                    if src == "unclassified":
                        unclassified[concept] = val
                    continue
                if BS_SLOTS_BY_ID[slot].balance is Balance.NA:
                    continue
                # Mapped/prefiltered leaves keep the as-filed sum (+
                # treasury negation) that reconciles well-formed filers.
                # A leaf resolved only by the section catch-all is an
                # unmapped face line whose direction the general path
                # cannot know; the filer's OWN rendered cell (a
                # parenthesised contra-asset such as a loan-loss
                # allowance shown net) is then authoritative -- scoped
                # to this fallback so exactly-reconciling filers, which
                # never reach it, are unaffected.
                if src == "structure_parent":
                    # Declared parent subtotal first (authoritative for a
                    # contra shown net on the face -- a REIT's "Less
                    # accumulated depreciation" is a bare positive, so
                    # the rendered cell would wrongly ADD it); else the
                    # filer's rendered sign (a parenthesised contra);
                    # else slot polarity.
                    psign = _structure_parent_contra_sign(
                        concept, structure, period, concepts, accession)
                    if psign is not None:
                        acc[slot] += psign * abs(val)
                    else:
                        rsign = structure.sign.get(
                            concept, {}).get(period)
                        if rsign is not None:
                            acc[slot] += rsign * abs(val)
                        else:
                            acc[slot] += _bs_signed(slot, val)
                else:
                    acc[slot] += _bs_signed(slot, val)
                slot_tags.setdefault(slot, []).append(concept)
            slots = dict(acc)
            for concept in structure.subtotals:
                key = _BS_SUBTOTAL_KEY.get(concept)
                cdata = concepts.get(concept)
                if key is None or cdata is None:
                    continue
                val = _instant_value(cdata, period, accession)
                if val is not None:
                    reported[key] = val
        else:
            for concept, cdata in concepts.items():
                slot, src = resolve_bs_slot(concept)
                if src == "out_of_scope":
                    continue
                val = _instant_value(cdata, period)
                if val is None:
                    continue
                if src == "subtotal":
                    reported[slot] = val  # provenance, not an input
                    continue
                if src == "unclassified":
                    unclassified[concept] = val
                    continue
                if BS_SLOTS_BY_ID[slot].balance is Balance.NA:
                    continue  # share counts, non-monetary
                cand[slot].append(Candidate(
                    concept, src, val, _bs_signed(slot, val)))

            # Reduce each slot's competing hierarchy levels to the
            # non-double-counting set, then sum only the survivors.
            for slot, cands in cand.items():
                kept, dropped = select(slot, cands, statement="BS")
                if kept:
                    slots[slot] = sum(c.signed_val for c in kept)
                    slot_tags[slot] = [c.concept for c in kept]
                if dropped:
                    dropped_tags[slot] = [(c.concept, r)
                                          for c, r in dropped]

        def _sum(sections) -> float:
            return sum(
                v for sid, v in slots.items()
                if BS_SLOTS_BY_ID[sid].section in sections
            )

        cur_a = _sum((Section.CURRENT_ASSET,))
        tot_a = _sum(_ASSET_SECTIONS)
        cur_l = _sum((Section.CURRENT_LIABILITY,))
        tot_l = _sum(_LIAB_SECTIONS)
        tot_e = _sum(_EQUITY_SECTIONS)
        subtotals = {
            "current_assets": cur_a,
            "total_assets": tot_a,
            "current_liabilities": cur_l,
            "total_liabilities": tot_l,
            "total_equity": tot_e,
        }
        reported_residual = {}
        if "total_assets" in reported:
            reported_residual["total_assets"] = (
                reported["total_assets"] - tot_a)
        if "total_liabilities" in reported:
            reported_residual["total_liabilities"] = (
                reported["total_liabilities"] - tot_l)
        if "total_equity" in reported:
            reported_residual["total_equity"] = reported["total_equity"] - tot_e

        results.append(BuildupResult(
            statement="BS", period=period, overlay=overlay,
            slots=dict(sorted(slots.items())),
            slot_tags={k: sorted(v) for k, v in sorted(slot_tags.items())},
            subtotals=subtotals,
            reported_subtotals=reported,
            identity_residual=tot_a - (tot_l + tot_e),
            reported_residual=reported_residual,
            unclassified=dict(sorted(unclassified.items())),
            dropped_tags={k: sorted(v) for k, v in
                          sorted(dropped_tags.items())},
            overlay_members=OVERLAYS["BS"],
        ))
    return results


# ── cash flow ────────────────────────────────────────────────────────

def build_cash_flow(facts_data: dict, *, num_periods: int = 1,
                    sic=None,
                    structure: StatementStructure | None = None,
                    accession: str | None = None,
                    ) -> list[BuildupResult]:
    """Structural cash-flow buildup.

    The CF analog of ``build_balance_sheet``: when ``structure`` (a
    parsed filing CF R-file tree) is supplied, only the filer's declared
    flow leaves are summed and its declared section subtotals become
    reported-vs-derived provenance. With ``structure=None`` the legacy
    flat-Company-Facts path runs (pre-filter + map + the hierarchy-aware
    ``_slot_selection`` fallback).
    """
    concepts = _gaap_concepts(facts_data)
    if not concepts:
        return []
    overlay = _overlay_for_sic(sic)
    fmonth = _fiscal_month(concepts)
    periods = _annual_period_ends(concepts, fmonth, num_periods)
    results: list[BuildupResult] = []

    for period in periods:
        cand: dict[str, list[Candidate]] = defaultdict(list)
        reported: dict[str, float] = {}
        unclassified: dict[str, float] = {}

        slots: dict[str, float] = {}
        slot_tags: dict[str, list[str]] = {}
        dropped_tags: dict[str, list[tuple[str, str]]] = {}

        if structure:
            # PRIMARY: the filing's own CF tree. Leaves are disjoint
            # flows by construction, so they are summed directly -- no
            # hierarchy de-dup needed. Declared subtotals are provenance.
            # Instant cash roll-forward balances (begin/end of period)
            # carry no ~year duration fact, so _duration_value naturally
            # excludes them from the flow sum.
            acc: dict[str, float] = defaultdict(float)
            for concept in structure.leaves:
                cdata = concepts.get(concept)
                if cdata is None:
                    continue  # extension leaf absent from Company Facts
                val = _duration_value(cdata, period, accession)
                if val is None:
                    continue
                slot, src = _resolve_cf_leaf_slot(concept, structure)
                if slot is None:
                    if src == "unclassified":
                        unclassified[concept] = val
                    continue
                if CF_SLOTS_BY_ID[slot].balance is Balance.NA:
                    continue
                # The filer's OWN rendered sign is authoritative: the
                # flat Company Facts value is a bare magnitude, so a
                # parenthesised cell (cash use) cannot be recovered from
                # the slot's coarse balance polarity. Fall back to the
                # slot-polarity guess only when the R-file gave no
                # rendered sign for this concept/period.
                rsign = structure.sign.get(concept, {}).get(period)
                if rsign is not None:
                    signed = rsign * abs(val)
                else:
                    signed = _cf_signed(slot, val)
                    if signed is None:
                        continue
                acc[slot] += signed
                slot_tags.setdefault(slot, []).append(concept)
            slots = dict(acc)
            for concept in structure.subtotals:
                key = _CF_SUBTOTAL_KEY.get(concept)
                cdata = concepts.get(concept)
                if key is None or cdata is None:
                    continue
                val = _duration_value(cdata, period, accession)
                if val is not None:
                    reported[key] = val
        else:
            for concept, cdata in concepts.items():
                slot, src = resolve_cf_slot(concept)
                if src == "out_of_scope":
                    continue
                val = _duration_value(cdata, period)
                if val is None:
                    continue
                if src == "subtotal":
                    reported[slot] = val
                    continue
                if src == "unclassified":
                    unclassified[concept] = val
                    continue
                signed = _cf_signed(slot, val)
                if signed is None:
                    continue
                cand[slot].append(Candidate(concept, src, val, signed))

            for slot, cands in cand.items():
                kept, dropped = select(slot, cands, statement="CF")
                if kept:
                    slots[slot] = sum(c.signed_val for c in kept)
                    slot_tags[slot] = [c.concept for c in kept]
                if dropped:
                    dropped_tags[slot] = [(c.concept, r)
                                          for c, r in dropped]

        def _sum(section) -> float:
            return sum(
                v for sid, v in slots.items()
                if CF_SLOTS_BY_ID[sid].section is section
            )

        cfo = _sum(Section.OPERATING)
        cfi = _sum(Section.INVESTING)
        cff = _sum(Section.FINANCING)
        fx = slots.get("cf_fx_effect", 0.0)
        delta = cfo + cfi + cff + fx
        subtotals = {
            "cfo": cfo, "cfi": cfi, "cff": cff,
            "cf_fx_effect": fx, "cf_change_in_cash": delta,
        }
        reported_residual = {
            sid: reported[sid] - subtotals[sid]
            for sid in ("cfo", "cfi", "cff", "cf_change_in_cash")
            if sid in reported
        }

        # Identity: derived ΔCash vs the reported ΔCash subtotal when the
        # filer tagged one; else fall back to internal consistency (0).
        if "cf_change_in_cash" in reported:
            identity = delta - reported["cf_change_in_cash"]
        else:
            identity = 0.0

        results.append(BuildupResult(
            statement="CF", period=period, overlay=overlay,
            slots=dict(sorted(slots.items())),
            slot_tags={k: sorted(v) for k, v in sorted(slot_tags.items())},
            subtotals=subtotals,
            reported_subtotals=reported,
            identity_residual=identity,
            reported_residual=reported_residual,
            unclassified=dict(sorted(unclassified.items())),
            dropped_tags={k: sorted(v) for k, v in
                          sorted(dropped_tags.items())},
            overlay_members=OVERLAYS["CF"],
        ))
    return results


# ── instance-XML fact source for company-extension leaves ────────────
#
# A filer's ``<ticker>:`` extension leaves are stripped from the
# Company Facts API, so a structure-driven buildup that needs them must
# read the filing's own validated XBRL instance. We only synthesize the
# Company-Facts shape (``{concept: {"units": {"USD": [facts]}}}``) for
# the specific extension concepts the structure declared as leaves, and
# only USD monetary facts -- everything else still comes from the flat
# Company Facts feed. Uses stdlib ElementTree so this module stays
# import-light and network-free.

def _extension_leaves(structure: StatementStructure | None) -> set[str]:
    """Declared leaf concepts whose taxonomy prefix is not us-gaap."""
    if not structure:
        return set()
    return {
        c for c in structure.leaves
        if structure.prefix.get(c, "us-gaap") != "us-gaap"
    }


def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _instance_ext_units(
    xml_bytes: bytes, wanted: set[str],
) -> dict[str, dict]:
    """Parse a filing instance doc into a Company-Facts-shaped slice.

    Returns ``{concept: {"units": {"USD": [{end,start,val,filed}, …]}}}``
    for the wanted extension concepts only. A tolerant parser by design:
    a malformed instance yields ``{}`` and the buildup simply has no
    value for those extension leaves.
    """
    if not xml_bytes or not wanted:
        return {}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return {}

    # contextRef -> (period_start|None, period_end). Instants store the
    # same date in both slots so the instant/duration split downstream
    # (``_instant_value`` / ``_duration_value``) still works.
    #
    # Only the non-dimensional (default consolidated) context is the
    # primary-statement face value. A context whose segment/scenario
    # carries an xbrldi explicit/typed member is a dimensional
    # breakdown (segment, product, member roll-up); keeping it would
    # let a disaggregated slice masquerade as the reported line and
    # blow up the buildup. Skip any dimensional context outright.
    ctx: dict[str, tuple[str | None, str]] = {}
    for c in root.iter():
        if _localname(c.tag) != "context":
            continue
        cid = c.get("id")
        if not cid:
            continue
        start = end = None
        dimensional = False
        for p in c.iter():
            ln = _localname(p.tag)
            if ln in ("explicitMember", "typedMember"):
                dimensional = True
            elif ln == "instant" and p.text:
                start, end = p.text.strip(), p.text.strip()
            elif ln == "startDate" and p.text:
                start = p.text.strip()
            elif ln == "endDate" and p.text:
                end = p.text.strip()
        if end and not dimensional:
            ctx[cid] = (start, end)

    out: dict[str, dict] = {}
    for el in root.iter():
        name = _localname(el.tag)
        if name not in wanted or el.text is None:
            continue
        cref = el.get("contextRef")
        if cref is None or cref not in ctx:
            continue
        raw = el.text.strip().replace(",", "")
        if not raw:
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        if el.get("sign") == "-":
            val = -val
        start, end = ctx[cref]
        fact = {"end": end, "val": val, "filed": ""}
        if start is not None:
            fact["start"] = start
        out.setdefault(name, {"units": {"USD": []}})
        out[name]["units"]["USD"].append(fact)
    return out


def _merge_ext_facts(facts_data: dict, ext_units: dict[str, dict]) -> None:
    """Fold instance-derived extension units into ``facts['ext']`` so
    ``_gaap_concepts`` picks them up alongside the us-gaap feed."""
    if not ext_units:
        return
    facts = facts_data.setdefault("facts", {})
    bucket = facts.setdefault("ext", {})
    for concept, payload in ext_units.items():
        bucket.setdefault(concept, payload)


# ── per-filing orchestrator ──────────────────────────────────────────

def build_filing_statements(
    cik, accession_number, retriever, *,
    sic=None, num_periods: int = 1,
) -> dict[str, list[BuildupResult]]:
    """Structure-driven BS + CF for one specific filing.

    Ties the per-filing tree to the closed-set buildup:

      1. flat Company Facts (the us-gaap value source),
      2. the filing's rendered BS/CF R-files -> ``StatementStructure``
         (the leaf-vs-subtotal authority),
      3. the filing's XBRL instance for any company-extension leaves the
         Company Facts API stripped,
      4. ``build_balance_sheet`` / ``build_cash_flow`` in structured
         mode (flat fallback if a statement could not be parsed).

    ``retriever`` is injected (the ``edgar.filing_retrieval``
    ``FilingRetrieval`` surface: ``get_company_facts``,
    ``get_filing_statement_rfiles``, ``get_filing_instance_xml``) so
    this module stays network-free and offline-testable.
    """
    facts = retriever.get_company_facts(cik)
    if not facts:
        return {"BS": [], "CF": []}

    rfiles = retriever.get_filing_statement_rfiles(
        cik, accession_number) or {}
    bs_struct = parse_statement_structure(rfiles.get("BS", "")) or None
    cf_struct = parse_statement_structure(rfiles.get("CF", "")) or None

    need_ext = _extension_leaves(bs_struct) | _extension_leaves(cf_struct)
    if need_ext:
        xml = retriever.get_filing_instance_xml(cik, accession_number)
        if xml:
            _merge_ext_facts(
                facts, _instance_ext_units(xml, need_ext))

    return {
        "BS": build_balance_sheet(
            facts, num_periods=num_periods, sic=sic,
            structure=bs_struct, accession=accession_number),
        "CF": build_cash_flow(
            facts, num_periods=num_periods, sic=sic,
            structure=cf_struct, accession=accession_number),
    }
