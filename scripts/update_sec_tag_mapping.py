"""Update system for ``data/sec_tag_mapping.json``.

The file is the Layer-1 backing data for ``edgar/tag_classifier.py`` —
~3,500 us-gaap tags, each carrying ``{statement, category, display_name,
count}``. It was originally auto-derived from the SEC Financial Statement
Data Set (FSDS); this script is the maintenance tool that keeps it in
sync with future FSDS quarters.

Design contract (decided up front, documented for the next maintainer):

* **Forward-only integrity.** The current file is the historical
  baseline. We record its sha256 in ``data/sec_tag_mapping.source.json``
  on first ``init``. From that point forward every change must come
  through ``apply``; any hand-edit is detected by ``check``.
* **New tags only.** Existing tags keep their classifications. The
  closed (statement, category) vocabulary is frozen. New tags from the
  upstream FSDS quarter are appended only when the per-statement rules
  fire confidently; tags that don't match a rule are held out as
  ``needs_review`` so somebody adjudicates them by hand. Anything that
  would introduce a NEW (statement, category) pair halts.
* **High precision, low recall.** Same philosophy as the BS/CF
  prefilters in ``edgar.metrics`` — we'd rather hold a tag out than
  silently mis-slot it.

Usage::

    python scripts/update_sec_tag_mapping.py init
    python scripts/update_sec_tag_mapping.py check
    python scripts/update_sec_tag_mapping.py update 2026q1
    python scripts/update_sec_tag_mapping.py update 2026q1 --apply
    python scripts/update_sec_tag_mapping.py update 2026q1 --source-zip path/to/2026q1.zip --apply

The ``update`` subcommand runs the full pipeline: integrity check →
download (or read local zip) → derive candidate → diff → report. With
``--apply`` it also writes the merged file and updates the manifest.

Requires ``EDGAR_IDENTITY`` for any path that downloads from SEC.
``--source-zip`` lets you skip the live download for offline / CI runs.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import io
import json
import logging
import os
import re
import sys
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

logger = logging.getLogger("update_sec_tag_mapping")

REPO_ROOT = Path(__file__).resolve().parent.parent
MAPPING_PATH = REPO_ROOT / "data" / "sec_tag_mapping.json"
MANIFEST_PATH = REPO_ROOT / "data" / "sec_tag_mapping.source.json"
FSDS_CACHE_DIR = REPO_ROOT / "data" / "sec_datasets"

# Closed (statement, category) vocabulary as observed in the historical
# baseline. Used as the format-equivalence check: derivation output must
# stay inside this set or we halt for review.
CATEGORIES_ALLOWED: tuple[tuple[str, str], ...] = (
    ("BS", "Assets"),
    ("BS", "Liabilities"),
    ("BS", "Equity"),
    ("IS", "Revenue"),
    ("IS", "Income"),
    ("IS", "EPS"),
    ("CF", "OperatingCashFlow"),
    ("CF", "InvestingCashFlow"),
    ("CF", "FinancingCashFlow"),
)

# FSDS download. SEC stable URL pattern; the file is a zipped bundle of
# tab-separated text files (sub.txt, num.txt, pre.txt, tag.txt, ...).
FSDS_URL_FMT = "https://www.sec.gov/files/dera/data/financial-statement-data-sets/{quarter}.zip"
QUARTER_RE = re.compile(r"^(20\d{2})q([1-4])$")


# ── Subcategory rules ─────────────────────────────────────────────────
#
# Each statement (BS / IS / CF) gets a list of ``(regex, category)``
# pairs. First match wins; ``None`` means abstain. Rules are intentionally
# narrow — a tag-name with no strong structural marker is held out as
# ``needs_review`` rather than silently auto-classified.
#
# These only ever apply to tags NOT already in ``sec_tag_mapping.json``.

# CF: the strongest signal is the ``…Activities`` suffix on the abstract
# headers and many flow tags. Specific-flow prefixes catch the rest of
# the well-known financing / investing items. Operating is the residual.
_CF_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"FinancingActivities"), "FinancingCashFlow"),
    (re.compile(r"InvestingActivities"), "InvestingCashFlow"),
    (re.compile(r"OperatingActivities"), "OperatingCashFlow"),
    # Explicit financing flows (issuance / repayment of debt or equity,
    # dividends, repurchases, distributions to owners).
    (re.compile(
        r"^(ProceedsFromIssuanceOf(LongTermDebt|CommonStock|PreferredStock|Debt|"
        r"SharesUnderEmployeeStockPurchasePlan)|RepaymentsOf(LongTermDebt|Debt|"
        r"LinesOfCredit|ShortTermDebt|SeniorLongTermNotes|NotesPayable)|"
        r"PaymentsOfDividends|PaymentsForRepurchaseOf(CommonStock|RedeemableNoncontrolling|"
        r"PreferredStock)|DistributionsMade|DividendsPaid|"
        r"ProceedsFromStockOptionsExercised|ProceedsFromLinesOfCredit|"
        r"ProceedsFromContributedCapital|ProceedsFromIssuance(OfDebt)?)"
    ), "FinancingCashFlow"),
    # Explicit investing flows (acquire / dispose of PPE, businesses,
    # investments; lend / collect on notes receivable).
    (re.compile(
        r"^(PaymentsToAcquire|ProceedsFromSaleOf|ProceedsFromMaturitiesOf|"
        r"PaymentsForProceedsFrom|ProceedsFromDivestiture|"
        r"PaymentsToFundLongTermLoansToRelatedParties|"
        r"ProceedsFromCollectionOfNotesReceivable|PaymentsForCapitalImprovements|"
        r"ProceedsFromSaleAndMaturityOf)"
    ), "InvestingCashFlow"),
)

# BS: split is debit-side (Assets) vs credit-side (Liabilities, Equity).
# Equity has a well-known stem family (StockholdersEquity, RetainedEarnings,
# AdditionalPaidInCapital, etc.). Liabilities is hardest because many tags
# don't start with the word — we list well-known liability stems plus an
# explicit ``…Liabilit`` substring check.
_BS_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # Equity stems.
    (re.compile(
        r"^(StockholdersEquity|MinorityInterest|PartnersCapital|"
        r"MembersEquity|AdditionalPaidInCapital|RetainedEarnings|"
        r"TreasuryStock|AccumulatedOtherComprehensive|CommonStock|"
        r"PreferredStock|TemporaryEquity|RedeemableNoncontrollingInterest)"
    ), "Equity"),
    # Liabilities — start-anchored stems first.
    (re.compile(
        r"^(Liabilities|LongTermDebt|ShortTermBorrowings|CommercialPaper|"
        r"AccountsPayable|AccruedLiabilities|DeferredRevenue|"
        r"DeferredTaxLiabilities|OperatingLeaseLiability|FinanceLeaseLiability|"
        r"PensionAndOtherPostretirement|DebtCurrent|DebtLongTerm|SeniorNotes|"
        r"SubordinatedDebt|ConvertibleNotes|NotesPayable|BankLoans|Mortgages|"
        r"CapitalLeaseObligations|InterestPayable|TaxesPayable|"
        r"IncomeTaxesPayable|EmployeeRelatedLiabilities|"
        r"UnearnedRevenue|CustomerDeposits|AssetRetirementObligation|"
        r"RestructuringReserve)"
    ), "Liabilities"),
    # Liabilities — any tag whose name contains the literal substring
    # "Liabilit" (Liability, Liabilities). Covers OtherLiabilitiesCurrent,
    # DeferredIncomeTaxesAndOtherLiabilitiesNoncurrent, etc.
    (re.compile(r"Liabilit"), "Liabilities"),
    # Liabilities — broader "Payable" tail (NOT receivable). The negative
    # lookbehind excludes Receivable-style names.
    (re.compile(r"Payable($|[A-Z])"), "Liabilities"),
    # Assets stems.
    (re.compile(
        r"^(Assets|CashAndCashEquivalents|CashCashEquivalents|RestrictedCash|"
        r"AccountsReceivable|InventoryNet|Inventories|"
        r"PropertyPlantAndEquipment|Goodwill|IntangibleAssets|"
        r"OperatingLeaseRightOfUseAsset|FinanceLeaseRightOfUseAsset|"
        r"DeferredTaxAssets|FinancingReceivable|MarketableSecurities|"
        r"AvailableForSaleSecurities|HeldToMaturitySecurities|"
        r"PrepaidExpense|DueFromRelatedParties|OtherAssets)"
    ), "Assets"),
    # Assets — broader "Receivable" tail (the most common asset stem we
    # haven't already caught).
    (re.compile(r"Receivable($|[A-Z])"), "Assets"),
)

# IS: EPS is the most distinctive (PerShare or EarningsPerShare in name).
# Revenue uses the explicit Revenue prefix or the well-known Sales family.
# Income is the residual.
_IS_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"PerShare|EarningsPerShare"), "EPS"),
    (re.compile(
        r"^(Revenue|SalesRevenueNet|SalesRevenueGoodsNet|SalesRevenueServicesNet|"
        r"Sales$|InterestAndDividendIncomeOperating|InterestIncomeOperating|"
        r"PremiumsEarnedNet|RevenuesNetOfInterestExpense)"
    ), "Revenue"),
)

_RULES_BY_STMT: dict[str, tuple[tuple[re.Pattern[str], str], ...]] = {
    "BS": _BS_RULES,
    "IS": _IS_RULES,
    "CF": _CF_RULES,
}


def _classify_subcategory(statement: str, tag: str) -> str | None:
    """Return a category for ``tag`` within ``statement``, or None to abstain."""
    rules = _RULES_BY_STMT.get(statement)
    if rules is None:
        return None
    for rgx, cat in rules:
        if rgx.search(tag):
            return cat
    return None


# ── Hashing & manifest ────────────────────────────────────────────────

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_manifest() -> dict | None:
    if not MANIFEST_PATH.is_file():
        return None
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_manifest(m: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)
        f.write("\n")


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── FSDS retrieval ────────────────────────────────────────────────────

def _validate_quarter(quarter: str) -> None:
    if not QUARTER_RE.match(quarter):
        raise SystemExit(f"Invalid quarter '{quarter}'. Expected form YYYYqN (e.g. 2026q1).")


def _identity_or_die() -> str:
    identity = os.environ.get("EDGAR_IDENTITY", "").strip()
    if not identity:
        raise SystemExit(
            "EDGAR_IDENTITY is not set. SEC fair-access policy requires every "
            "requester to identify themselves. Set it to 'Your Name your@email.com' "
            "before any live download, or pass --source-zip to use a local FSDS zip."
        )
    return identity


def _download_fsds(quarter: str, dest: Path) -> None:
    """Fetch the FSDS zip for *quarter* into *dest*. Live SEC pull."""
    identity = _identity_or_die()
    url = FSDS_URL_FMT.format(quarter=quarter)
    logger.info("Downloading %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": identity})
    with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (https)
        if resp.status != 200:
            raise SystemExit(f"SEC returned HTTP {resp.status} for {url}")
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
    logger.info("Wrote %d bytes", dest.stat().st_size)


def _read_fsds_tags(zip_path: Path) -> dict[str, dict]:
    """Pull per-tag (stmt, count) aggregates out of an FSDS zip.

    Returns a dict ``{tag_name: {"stmt": str, "count": int, "tlabel": str}}``
    restricted to us-gaap concepts (``version`` starts with ``us-gaap``).
    The stmt code is the FSDS ``stmt`` column from ``pre.txt`` (one of
    BS / IS / CF / EQ / CI / PR / UN / SI / CP) — picked as the most
    common stmt for that tag across the quarter.
    """
    counts_by_stmt: defaultdict[str, Counter] = defaultdict(Counter)
    seen_tags: set[str] = set()

    with zipfile.ZipFile(zip_path) as zf:
        names = {n.lower(): n for n in zf.namelist()}
        pre_name = names.get("pre.txt")
        tag_name = names.get("tag.txt")
        if pre_name is None or tag_name is None:
            raise SystemExit(f"FSDS zip {zip_path} is missing pre.txt or tag.txt")

        # pre.txt: presentation linkbase. Has stmt code per tag occurrence.
        with zf.open(pre_name) as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(stream, delimiter="\t")
            for row in reader:
                version = (row.get("version") or "").strip()
                if not version.startswith("us-gaap"):
                    continue
                tag = (row.get("tag") or "").strip()
                stmt = (row.get("stmt") or "").strip()
                if not tag or not stmt:
                    continue
                counts_by_stmt[tag][stmt] += 1
                seen_tags.add(tag)

        # tag.txt: tag dictionary. Carries the human-readable label (tlabel).
        labels: dict[str, str] = {}
        with zf.open(tag_name) as raw:
            stream = io.TextIOWrapper(raw, encoding="utf-8", newline="")
            reader = csv.DictReader(stream, delimiter="\t")
            for row in reader:
                version = (row.get("version") or "").strip()
                if not version.startswith("us-gaap"):
                    continue
                tag = (row.get("tag") or "").strip()
                if tag not in seen_tags:
                    continue
                # tlabel is the standard preferred label; fall back to doc.
                label = (row.get("tlabel") or "").strip()
                if label:
                    labels[tag] = label

    out: dict[str, dict] = {}
    for tag, stmt_counter in counts_by_stmt.items():
        # Most common stmt code for this tag across the quarter.
        stmt, _ = stmt_counter.most_common(1)[0]
        total = sum(stmt_counter.values())
        out[tag] = {
            "stmt": stmt,
            "count": total,
            "tlabel": labels.get(tag, ""),
        }
    return out


# ── Derivation ────────────────────────────────────────────────────────

def _derive_candidate(
    fsds_tags: dict[str, dict],
    current: dict[str, dict],
) -> tuple[dict[str, dict], list[str], list[str]]:
    """Produce a candidate merge against the current mapping.

    Returns ``(merged, added, needs_review)``.

    ``merged`` is the proposed new ``sec_tag_mapping.json`` content. It
    contains every existing entry (unchanged classifications, ``count``
    refreshed from FSDS where available) plus any newly auto-classified
    tags.  ``added`` lists tag names that were newly auto-classified.
    ``needs_review`` lists tag names FSDS surfaced that rules abstained
    on — these are NOT written; a human picks each one's classification.
    """
    merged: dict[str, dict] = {}
    added: list[str] = []
    needs_review: list[str] = []

    # 1) Existing tags: preserve classification, refresh count if seen.
    for tag, entry in current.items():
        new_entry = dict(entry)
        seen = fsds_tags.get(tag)
        if seen is not None:
            new_entry["count"] = seen["count"]
        merged[tag] = new_entry

    # 2) New tags: only those in our vocabulary statements, and only
    #    when rules fire confidently.
    for tag, info in fsds_tags.items():
        if tag in current:
            continue
        stmt = info["stmt"]
        if stmt not in _RULES_BY_STMT:
            # PR / UN / SI / CP / EQ / CI — not in our closed vocabulary.
            continue
        cat = _classify_subcategory(stmt, tag)
        if cat is None:
            needs_review.append(tag)
            continue
        if (stmt, cat) not in CATEGORIES_ALLOWED:
            # Shouldn't happen given the rule sets, but guard anyway.
            needs_review.append(tag)
            continue
        merged[tag] = {
            "statement": stmt,
            "category": cat,
            "display_name": info["tlabel"] or tag,
            "count": info["count"],
        }
        added.append(tag)

    return merged, added, needs_review


def _schema_check(merged: dict[str, dict]) -> list[str]:
    """Return a list of schema violations; empty list means OK."""
    problems: list[str] = []
    allowed = set(CATEGORIES_ALLOWED)
    for tag, e in merged.items():
        if not isinstance(e, dict):
            problems.append(f"{tag}: not a dict")
            continue
        keys = set(e.keys())
        if keys != {"statement", "category", "display_name", "count"}:
            problems.append(f"{tag}: keys={sorted(keys)}")
            continue
        if (e["statement"], e["category"]) not in allowed:
            problems.append(f"{tag}: out-of-vocab ({e['statement']},{e['category']})")
        if not isinstance(e["count"], int):
            problems.append(f"{tag}: count is not int")
    return problems


# ── Serialization (deterministic) ─────────────────────────────────────

def _dump_mapping(merged: dict[str, dict]) -> bytes:
    """Serialize the mapping deterministically: sort by tag, fixed key order."""
    ordered = {}
    for tag in sorted(merged.keys()):
        e = merged[tag]
        ordered[tag] = {
            "statement": e["statement"],
            "category": e["category"],
            "display_name": e["display_name"],
            "count": int(e["count"]),
        }
    return (json.dumps(ordered, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


# ── Subcommands ───────────────────────────────────────────────────────

def cmd_init(args: argparse.Namespace) -> int:
    """Bootstrap the manifest from the current mapping file."""
    if not MAPPING_PATH.is_file():
        raise SystemExit(f"{MAPPING_PATH} not found")

    if MANIFEST_PATH.is_file() and not args.force:
        existing = _load_manifest()
        print(f"Manifest already exists at {MANIFEST_PATH}.")
        print(f"  current_sha256: {existing.get('current_sha256')}")
        print(f"  last_quarter:   {existing.get('last_quarter')}")
        print("Pass --force to overwrite.")
        return 1

    current_sha = _sha256_file(MAPPING_PATH)
    manifest = {
        "schema_version": 1,
        "current_sha256": current_sha,
        "last_quarter": None,
        "fsds_url": None,
        "fsds_sha256": None,
        "categories_allowed": [list(p) for p in CATEGORIES_ALLOWED],
        "updated_at": _now_iso(),
        "notes": (
            "Forward-only integrity baseline. The current sec_tag_mapping.json "
            "predates this update system; its contents are accepted as historical "
            "ground truth. From this manifest forward, any change must come from "
            "running 'update --apply'; 'check' detects hand-edits."
        ),
        "history": [],
    }
    _save_manifest(manifest)
    print(f"Wrote {MANIFEST_PATH}")
    print(f"  current_sha256: {current_sha}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    """Verify the current file's sha256 against the manifest."""
    manifest = _load_manifest()
    if manifest is None:
        raise SystemExit(f"No manifest at {MANIFEST_PATH}. Run 'init' first.")
    if not MAPPING_PATH.is_file():
        raise SystemExit(f"{MAPPING_PATH} not found")

    expected = manifest.get("current_sha256")
    actual = _sha256_file(MAPPING_PATH)
    if actual != expected:
        print(f"INTEGRITY FAIL")
        print(f"  expected: {expected}")
        print(f"  actual:   {actual}")
        print(f"  {MAPPING_PATH} has been modified outside the update system.")
        return 2
    print(f"OK  sha256={actual}")
    print(f"  last_quarter: {manifest.get('last_quarter')}")
    print(f"  updated_at:   {manifest.get('updated_at')}")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    """Pull a new FSDS quarter, derive, diff, optionally apply."""
    _validate_quarter(args.quarter)

    manifest = _load_manifest()
    if manifest is None:
        raise SystemExit(f"No manifest at {MANIFEST_PATH}. Run 'init' first.")

    # 1) Integrity check first — never operate on a hand-edited file.
    actual = _sha256_file(MAPPING_PATH)
    if actual != manifest.get("current_sha256"):
        print("INTEGRITY FAIL -- refusing to update.")
        print(f"  expected: {manifest.get('current_sha256')}")
        print(f"  actual:   {actual}")
        print(f"  {MAPPING_PATH} has been modified outside the update system.")
        return 2

    # 2) Resolve the FSDS zip (download or local).
    if args.source_zip:
        zip_path = Path(args.source_zip).resolve()
        if not zip_path.is_file():
            raise SystemExit(f"--source-zip {zip_path} not found")
    else:
        zip_path = FSDS_CACHE_DIR / f"{args.quarter}.zip"
        if not zip_path.is_file():
            _download_fsds(args.quarter, zip_path)

    fsds_sha = _sha256_file(zip_path)
    fsds_url = FSDS_URL_FMT.format(quarter=args.quarter) if not args.source_zip else None
    print(f"FSDS zip: {zip_path}  sha256={fsds_sha}")

    # 3) Read tags.
    fsds_tags = _read_fsds_tags(zip_path)
    print(f"FSDS us-gaap tags observed: {len(fsds_tags)}")

    # 4) Load current mapping & derive candidate.
    with open(MAPPING_PATH, "r", encoding="utf-8") as f:
        current = json.load(f)
    merged, added, needs_review = _derive_candidate(fsds_tags, current)

    # 5) Schema check the candidate.
    problems = _schema_check(merged)
    if problems:
        print("SCHEMA CHECK FAILED:")
        for p in problems[:20]:
            print(f"  {p}")
        if len(problems) > 20:
            print(f"  ... +{len(problems) - 20} more")
        return 3

    # 6) Diff report.
    removed = [t for t in current if t not in fsds_tags]
    print()
    print(f"Diff against current ({len(current)} tags):")
    print(f"  + added (auto-classified new tags):     {len(added)}")
    print(f"  ? needs_review (new tags, no rule fit): {len(needs_review)}")
    print(f"  ~ tags absent from this FSDS quarter:   {len(removed)} (kept)")
    print(f"  total in merged file:                   {len(merged)}")

    if added:
        print("\nFirst 20 auto-classified additions:")
        for tag in added[:20]:
            e = merged[tag]
            print(f"  + {e['statement']}|{e['category']:<20} {tag}  ({e['count']}x)")
        if len(added) > 20:
            print(f"  ... +{len(added) - 20} more")

    if needs_review:
        print("\nFirst 20 needs_review (write a rule or classify by hand):")
        for tag in needs_review[:20]:
            info = fsds_tags[tag]
            print(f"  ? stmt={info['stmt']:<3} {tag}  ({info['count']}x)  '{info['tlabel'][:40]}'")
        if len(needs_review) > 20:
            print(f"  ... +{len(needs_review) - 20} more")

    if args.report:
        rpath = Path(args.report).resolve()
        rpath.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "quarter": args.quarter,
            "fsds_sha256": fsds_sha,
            "added": added,
            "needs_review": [
                {"tag": t, "stmt": fsds_tags[t]["stmt"],
                 "count": fsds_tags[t]["count"], "tlabel": fsds_tags[t]["tlabel"]}
                for t in needs_review
            ],
            "absent_from_quarter": removed,
        }
        with open(rpath, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        print(f"\nWrote review report -> {rpath}")

    if not args.apply:
        print("\nDry-run (no --apply). Mapping NOT modified.")
        return 0

    if not added:
        print("\nNothing to add. Mapping NOT modified.")
        return 0

    # 7) Apply: write merged file deterministically, update manifest.
    body = _dump_mapping(merged)
    new_sha = _sha256_bytes(body)
    with open(MAPPING_PATH, "wb") as f:
        f.write(body)

    history = list(manifest.get("history") or [])
    history.append({
        "from_sha256": manifest.get("current_sha256"),
        "to_sha256": new_sha,
        "quarter": args.quarter,
        "fsds_sha256": fsds_sha,
        "added_count": len(added),
        "applied_at": _now_iso(),
    })
    manifest.update({
        "current_sha256": new_sha,
        "last_quarter": args.quarter,
        "fsds_url": fsds_url,
        "fsds_sha256": fsds_sha,
        "updated_at": _now_iso(),
        "history": history,
    })
    _save_manifest(manifest)
    print(f"\nApplied. {MAPPING_PATH} sha256={new_sha}")
    print(f"          {MANIFEST_PATH} updated.")
    return 0


# ── CLI ───────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    pi = sub.add_parser("init", help="Bootstrap the manifest from the current mapping file.")
    pi.add_argument("--force", action="store_true",
                    help="Overwrite an existing manifest.")
    pi.set_defaults(func=cmd_init)

    pc = sub.add_parser("check", help="Verify the current mapping file's sha256 against the manifest.")
    pc.set_defaults(func=cmd_check)

    pu = sub.add_parser("update",
                        help="Pull a new FSDS quarter, derive a candidate, diff, optionally apply.")
    pu.add_argument("quarter", help="FSDS quarter, e.g. 2026q1.")
    pu.add_argument("--source-zip", help="Path to a local FSDS zip (skip download).")
    pu.add_argument("--apply", action="store_true",
                    help="Write the merged file and update the manifest (default: dry-run).")
    pu.add_argument("--report", help="Write a JSON review report to this path.")
    pu.set_defaults(func=cmd_update)

    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
