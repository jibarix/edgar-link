"""Parser for standalone XBRL instance documents.

Reads the `*_htm.xml` file that ships with each 10-K/10-Q filing
(the regulator-validated XBRL extracted from the iXBRL HTML). Captures
every reported fact INCLUDING company-extension concepts that the
Company Facts API strips.

Returns a flat list of fact dicts ready to merge with the existing
us-gaap pipeline.
"""

from __future__ import annotations

import logging
from typing import Iterator

from lxml import etree

logger = logging.getLogger(__name__)

# XBRL core namespaces
NS_XBRLI = "http://www.xbrl.org/2003/instance"
NS_LINK = "http://www.xbrl.org/2003/linkbase"
NS_XLINK = "http://www.w3.org/1999/xlink"
NS_XBRLDI = "http://xbrl.org/2006/xbrldi"
NS_XSI = "http://www.w3.org/2001/XMLSchema-instance"
NS_ISO4217 = "http://www.xbrl.org/2003/iso4217"

# Namespace URIs whose elements are not facts
_RESERVED_NS = frozenset({
    NS_XBRLI, NS_LINK, NS_XLINK, NS_XBRLDI, NS_XSI, NS_ISO4217,
})

# Unit local-names we keep (everything else is exotic / non-monetary)
_KEEP_UNITS = frozenset({"USD", "shares", "USD/shares", "pure"})


def parse_instance(xml_bytes: bytes) -> list[dict]:
    """Parse an XBRL instance document into a list of fact dicts.

    Each fact carries:
        prefix          taxonomy prefix (us-gaap, abg, kmx, dei, ...)
        concept         local concept name (no namespace)
        value           numeric value as float
        period_start    ISO date (None for instants)
        period_end      ISO date (always present)
        period_type     "duration" or "instant"
        unit            unit local-name ("USD", "shares", "USD/shares", "pure")
        decimals        decimal precision (None if "INF" or missing)

    Skips:
        - facts whose context carries dimensional segments (breakdown lines)
        - non-numeric facts (text disclosures)
        - facts with units outside the keep set
    """
    parser = etree.XMLParser(remove_blank_text=True, huge_tree=True, recover=True)
    root = etree.fromstring(xml_bytes, parser=parser)

    # ── Contexts (only consolidated contexts; skip those with dimensions) ──
    contexts: dict[str, dict] = {}
    for ctx in root.findall(f"{{{NS_XBRLI}}}context"):
        ctx_id = ctx.get("id")
        if not ctx_id:
            continue
        segment = ctx.find(f"{{{NS_XBRLI}}}entity/{{{NS_XBRLI}}}segment")
        if segment is not None and len(segment) > 0:
            # has dimensional breakdown — skip (we only want consolidated facts)
            continue
        period = ctx.find(f"{{{NS_XBRLI}}}period")
        if period is None:
            continue
        instant = period.find(f"{{{NS_XBRLI}}}instant")
        start = period.find(f"{{{NS_XBRLI}}}startDate")
        end = period.find(f"{{{NS_XBRLI}}}endDate")
        if instant is not None and instant.text:
            contexts[ctx_id] = {
                "period_type": "instant",
                "period_start": None,
                "period_end": instant.text.strip(),
            }
        elif start is not None and end is not None and start.text and end.text:
            contexts[ctx_id] = {
                "period_type": "duration",
                "period_start": start.text.strip(),
                "period_end": end.text.strip(),
            }

    # ── Units (resolve unitRef → local-name) ──
    units: dict[str, str] = {}
    for unit in root.findall(f"{{{NS_XBRLI}}}unit"):
        unit_id = unit.get("id")
        if not unit_id:
            continue
        divide = unit.find(f"{{{NS_XBRLI}}}divide")
        if divide is not None:
            num = divide.find(f"{{{NS_XBRLI}}}unitNumerator/{{{NS_XBRLI}}}measure")
            den = divide.find(f"{{{NS_XBRLI}}}unitDenominator/{{{NS_XBRLI}}}measure")
            if num is not None and den is not None and num.text and den.text:
                n = num.text.split(":")[-1]
                d = den.text.split(":")[-1]
                units[unit_id] = f"{n}/{d}"
        else:
            measure = unit.find(f"{{{NS_XBRLI}}}measure")
            if measure is not None and measure.text:
                units[unit_id] = measure.text.split(":")[-1]

    # ── Invert namespace map (URI → prefix) for prefix lookup ──
    uri_to_prefix: dict[str, str] = {}
    for prefix, uri in root.nsmap.items():
        if prefix and uri not in uri_to_prefix:
            uri_to_prefix[uri] = prefix

    # ── Facts ──
    # Issuers commonly emit the same (concept, context) tuple multiple times
    # within one instance document — once for the primary financial statement
    # and again for footnote/disclosure tables that re-tag the same fact.
    # Values are identical across these copies; if we don't dedupe here the
    # downstream extension-rule aggregator will sum each occurrence and
    # multiply the canonical balance.
    facts: list[dict] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for elem in root.iterchildren():
        tag = elem.tag
        if not isinstance(tag, str) or not tag.startswith("{"):
            continue
        ns_uri, _, local = tag[1:].partition("}")
        if ns_uri in _RESERVED_NS:
            continue
        ctx_id = elem.get("contextRef")
        if not ctx_id or ctx_id not in contexts:
            continue
        unit_id = elem.get("unitRef")
        if not unit_id:
            continue  # non-numeric (text disclosure)
        unit_name = units.get(unit_id, "")
        if unit_name not in _KEEP_UNITS:
            continue
        val_text = (elem.text or "").strip()
        if not val_text:
            continue
        try:
            value = float(val_text)
        except ValueError:
            continue
        sign = elem.get("sign")
        if sign == "-":
            value = -value
        decimals_attr = elem.get("decimals")
        decimals = None
        if decimals_attr and decimals_attr != "INF":
            try:
                decimals = int(decimals_attr)
            except ValueError:
                pass

        prefix = uri_to_prefix.get(ns_uri, "")
        ctx = contexts[ctx_id]
        key = (prefix, local, ctx["period_end"], ctx["period_type"], unit_name)
        if key in seen:
            continue
        seen.add(key)
        facts.append({
            "prefix": prefix,
            "concept": local,
            "value": value,
            "period_start": ctx["period_start"],
            "period_end": ctx["period_end"],
            "period_type": ctx["period_type"],
            "unit": unit_name,
            "decimals": decimals,
        })

    logger.debug(f"Parsed {len(facts)} facts from instance ({len(contexts)} contexts, "
                 f"{len(units)} units)")
    return facts


def iter_extension_facts(facts: list[dict], host_prefix: str) -> Iterator[dict]:
    """Yield only company-extension facts (not us-gaap/dei/srt/cyd/ecd).

    `host_prefix` is the issuer's own prefix (e.g. "abg"). Anything not
    in the well-known taxonomy prefixes is treated as an extension.
    """
    well_known = {"us-gaap", "dei", "srt", "cyd", "ecd", "country", "currency",
                  "exch", "naics", "sic", "stpr"}
    for f in facts:
        if f["prefix"] in well_known:
            continue
        yield f
