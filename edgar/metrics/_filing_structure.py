"""Parse SEC-rendered statement R-files into the filer's own tree.

A filing's ``R*.htm`` financial report is SEC's rendering of the
company's calculation + presentation linkbases. Each data row carries
the us-gaap (or company-extension) concept and a row class whose
trailing ``u`` marks a *declared subtotal/total* (the renderer
underlines computed rows). That is the authoritative, per-filing
leaf-vs-subtotal signal the flat Company Facts feed cannot provide:

  * ``re`` / ``ro``           -> leaf input line
  * ``reu`` / ``rou`` ...     -> declared subtotal / total (NOT an input)
  * ``*Abstract`` concept     -> section header (no value)

The Layer-2 structure-driven buildup keeps only the leaves, sums them
into the closed-set slots via the *existing* general resolution, and
treats the declared subtotals purely as reported-vs-derived provenance.
The result is an exact reconciliation for well-formed filers instead of
guessing hierarchy from tag names.

This is a tolerant text parser by design: an unparseable or empty
R-file yields an empty structure and the buildup falls back to the
flat Company-Facts path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_ROW = re.compile(r"<tr[^>]*class=\"([^\"]*)\"[^>]*>(.*?)</tr>", re.S)
_DEFREF = re.compile(r"defref_([A-Za-z0-9-]+)_([A-Za-z0-9]+)")
_CELL = re.compile(r"<td class=\"(num|nump|text)\"[^>]*>(.*?)</td>", re.S)
_PADDING = re.compile(r"padding-left:\s*(\d+)")
_TAG = re.compile(r"<[^>]+>")
_TH = re.compile(r"<th[^>]*>(.*?)</th>", re.S)
_COL_DATE = re.compile(
    r"([A-Z][a-z]{2})\.?\s+(\d{1,2}),?\s+(\d{4})")
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), start=1)}


def _has_value(cells: list[tuple[str, str]]) -> bool:
    for kind, body in cells:
        if kind in ("num", "nump"):
            txt = _TAG.sub("", body).replace("&#160;", "").strip()
            if re.search(r"\d", txt):
                return True
    return False


@dataclass
class StatementStructure:
    """The filer's own statement tree for one rendered R-file.

    leaves      bare concept names that are input lines (order-preserved)
    subtotals   bare concept names the filer declared as computed totals
    prefix      concept -> taxonomy prefix ('us-gaap' or an extension ns)
    parent      concept -> nearest enclosing declared-subtotal concept,
                recorded for subtotals too so the extension-slot
                fallback can walk leaf -> subtotal -> ... -> section
                total on flat (un-indented) statements
    period_ends ISO period-end dates, in rendered left->right column
                order (parsed from the R-file column header)
    sign        concept -> {period_iso: +1 | -1} taken from the filer's
                OWN rendered presentation (a parenthesised cell is the
                authoritative negative). The flat Company Facts value is
                a bare magnitude, so for sign-mixed statements (the CF
                indirect method) this rendered sign -- not a slot's
                balance polarity -- is the only correct direction.
    """

    leaves: list[str] = field(default_factory=list)
    subtotals: list[str] = field(default_factory=list)
    prefix: dict[str, str] = field(default_factory=dict)
    parent: dict[str, str] = field(default_factory=dict)
    period_ends: list[str] = field(default_factory=list)
    sign: dict[str, dict[str, int]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.leaves)


def _parse_period_ends(r_html: str) -> list[str]:
    """ISO period-end dates from the R-file column header, L->R."""
    head = r_html[:6000]
    ends: list[str] = []
    for th in _TH.findall(head):
        txt = _TAG.sub(" ", th).replace("&#160;", " ")
        for mon, day, year in _COL_DATE.findall(txt):
            mnum = _MONTHS.get(mon[:3])
            if mnum:
                ends.append(f"{year}-{mnum:02d}-{int(day):02d}")
    return ends


def _cell_sign(body: str) -> int | None:
    """+1 / -1 from one rendered numeric cell; None if it has no
    figure (an em-dash / blank column for that period)."""
    txt = _TAG.sub("", body).replace("&#160;", "").strip()
    if not re.search(r"\d", txt):
        return None
    return -1 if "(" in txt else 1


def _primary_concept(defrefs: list[tuple[str, str]]):
    """The row's economic concept: first defref that is neither an
    abstract header nor a share-count companion (matches the prototype
    that reconciled MSFT exactly)."""
    for pfx, cc in defrefs:
        if cc.endswith("Abstract") or "Shares" in cc:
            continue
        return pfx, cc
    return None, None


def parse_statement_structure(r_html: str) -> StatementStructure:
    """Parse one rendered statement R-file into a StatementStructure."""
    st = StatementStructure()
    if not r_html:
        return st

    st.period_ends = _parse_period_ends(r_html)

    # First pass: ordered (concept, prefix, role, indent) records, and
    # per-concept rendered sign per period column (the filer's own
    # presentation is the authoritative direction).
    recs: list[tuple[str, str, str, int]] = []
    for cls, body in _ROW.findall(r_html):
        defrefs = _DEFREF.findall(body)
        if not defrefs:
            continue
        prefix, concept = _primary_concept(defrefs)
        if concept is None:
            continue
        cells = _CELL.findall(body)
        if not _has_value(cells):
            continue  # abstract / section header
        indent = 0
        m = _PADDING.search(body)
        if m:
            indent = int(m.group(1))
        role = "subtotal" if cls.rstrip().endswith("u") else "leaf"
        recs.append((concept, prefix, role, indent))
        # Numeric columns align L->R with period_ends by index.
        col = 0
        per_period = st.sign.setdefault(concept, {})
        for kind, cbody in cells:
            if kind not in ("num", "nump"):
                continue
            s = _cell_sign(cbody)
            if s is not None and col < len(st.period_ends):
                per_period.setdefault(st.period_ends[col], s)
            col += 1

    # Second pass: a leaf's parent is the nearest *following* declared
    # subtotal that encloses it (indent <= the leaf's). Used only as the
    # extension-concept slot fallback; us-gaap leaves resolve directly.
    for i, (concept, prefix, role, indent) in enumerate(recs):
        if role == "subtotal":
            if concept not in st.subtotals:
                st.subtotals.append(concept)
            st.prefix.setdefault(concept, prefix)
        else:
            if concept in st.leaves:
                continue
            st.leaves.append(concept)
            st.prefix.setdefault(concept, prefix)
        # Nearest *following* declared subtotal that encloses this row
        # (indent <= this row's). Recorded for subtotals as well as
        # leaves so the slot resolver can walk leaf -> subtotal -> ...
        # -> section total: a filer that renders a flat (un-indented)
        # statement -- banks do this -- nests a leaf under an
        # intermediate roll-up subtotal, not the section total, so the
        # single nearest subtotal is not slot-mappable on its own.
        if concept not in st.parent:
            for nc, _np, nr, ni in recs[i + 1:]:
                if nr == "subtotal" and ni <= indent and nc != concept:
                    st.parent[concept] = nc
                    break
    return st
