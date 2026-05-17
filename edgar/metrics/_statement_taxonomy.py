"""Frozen Layer-1 balance-sheet & cash-flow classification taxonomy.

This is the *closed set* every BS/CF tag-classification pass
(deterministic pre-filter and LLM fan-out alike) maps EDGAR us-gaap
tags into. It is deliberately the standard accounting structure, NOT a
vendor's adjustment-granular metric list:

  * EDGAR does not tag at a commercial vendor's granularity (there is
    no ``us-gaap:NetDebt``). Classifying raw tags into a vendor's
    derived/adjusted variants is impossible at the tag level, so those
    remain Layer-2 *computations* (the metrics engine), not
    classification targets.
  * The structural buildup (cash -> receivables -> ... -> total
    assets; CFO + CFI + CFF -> change in cash, each slot with a
    us-gaap ``balance`` polarity) is standard accounting, contains no
    proprietary material, and is safe to version-control in the public
    repo.

This module is the BS/CF analog of the (offline, archived) income-
statement taxonomy. It is intentionally self-contained and
vendor-neutral so it can ship in the public ``edgar-connect`` tree.

``balance`` is the expected us-gaap balance polarity for tags in the
slot and is the basis of the deterministic merge guardrail: a tag
whose name-derived polarity contradicts its assigned slot's expected
polarity is auto-flagged, not silently merged. ``EITHER`` = the slot
legitimately holds both signs (e.g. retained earnings vs accumulated
deficit; working-capital deltas) and is exempt from the polarity
check. ``NA`` = non-monetary (share counts).

Cash-flow polarity is modeled with the same enum: ``DEBIT`` = a cash
*outflow* line (payments, purchases, repurchases, repayments),
``CREDIT`` = a cash *inflow* / non-cash add-back line (proceeds,
issuance, depreciation/impairment add-backs). ``EITHER`` covers
genuinely mixed-sign reconciling items (working-capital changes,
deferred taxes, the section subtotals themselves).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Balance(str, Enum):
    """Expected us-gaap balance polarity for a slot's member tags.

    On the cash-flow statement the same enum encodes flow direction:
    CREDIT = inflow / non-cash add-back, DEBIT = outflow.
    """

    CREDIT = "credit"   # asset (BS) / inflow & add-back (CF)
    DEBIT = "debit"     # liability+equity (BS) / outflow (CF)
    EITHER = "either"   # slot legitimately mixed-sign (exempt from guard)
    NA = "na"           # non-monetary (share counts)


class Statement(str, Enum):
    BS = "BS"
    CF = "CF"


class Section(str, Enum):
    """Structural band a slot sits in (presentation/ordering only)."""

    # Balance sheet
    CURRENT_ASSET = "current_asset"
    NONCURRENT_ASSET = "noncurrent_asset"
    ASSET_TOTAL = "asset_total"
    CURRENT_LIABILITY = "current_liability"
    NONCURRENT_LIABILITY = "noncurrent_liability"
    LIABILITY_TOTAL = "liability_total"
    EQUITY = "equity"
    EQUITY_TOTAL = "equity_total"
    # Cash flow
    OPERATING = "operating"
    OPERATING_TOTAL = "operating_total"
    INVESTING = "investing"
    INVESTING_TOTAL = "investing_total"
    FINANCING = "financing"
    FINANCING_TOTAL = "financing_total"
    CASH_RECONCILIATION = "cash_reconciliation"


# Industry overlay keys. ``GENERIC`` is the default spine; a filer is
# routed to at most one of BANK / INSURANCE / REIT by its SEC SIC range
# (banks 60xx-602x, insurers 631x-641x, REITs 6798). Everything else --
# dealers, retailers, health-care, industrials -- uses GENERIC. On the
# balance sheet the overlay deltas are LARGER than on the income
# statement (banks have no current/non-current split; insurers carry
# reserves/separate accounts; REITs net real estate), so overlay
# membership is re-derived per BS/CF section here and must NOT be
# assumed to transfer from the IS overlay set.
GENERIC = "generic"
BANK = "bank"
INSURANCE = "insurance"
REIT = "reit"
OVERLAY_KEYS = (GENERIC, BANK, INSURANCE, REIT)


@dataclass(frozen=True)
class Slot:
    """One frozen classification target.

    id        stable identifier (the closed-set label agents emit)
    label     human label
    statement BS or CF
    section   structural band
    balance   expected us-gaap balance polarity (merge guardrail basis)
    subtotal  True if a computed roll-up, not a raw input line
              (agents must NOT classify a raw tag into a subtotal slot;
              subtotals are derived by the metrics engine)
    overlays  industry overlays that carry a variant of this slot
              (membership only)
    note      one-line scope note for the classifier
    """

    id: str
    label: str
    statement: Statement
    section: Section
    balance: Balance
    subtotal: bool
    overlays: tuple[str, ...]
    note: str


# ── Balance-sheet closed set ──────────────────────────────────────────
# Order follows the standard classified balance sheet. Subtotal slots
# (current_assets, total_assets, current_liabilities, total_liabilities,
# total_equity) are present so the engine has a canonical id and so the
# Assets ≡ Liabilities + Equity identity is expressible from the closed
# set, but they are NOT valid fan-out targets.
_BS_SLOTS: tuple[Slot, ...] = (
    # — current assets —
    Slot("cash", "Cash & cash equivalents", Statement.BS,
         Section.CURRENT_ASSET, Balance.CREDIT, False, (BANK, INSURANCE),
         "Cash, cash equivalents, restricted cash shown as current."),
    Slot("short_term_investments", "Short-term investments", Statement.BS,
         Section.CURRENT_ASSET, Balance.CREDIT, False, (BANK, INSURANCE),
         "Marketable securities / trading securities classified current."),
    Slot("accounts_receivable", "Receivables", Statement.BS,
         Section.CURRENT_ASSET, Balance.CREDIT, False, (BANK, INSURANCE),
         "Trade & other receivables, net of allowance, current."),
    Slot("inventory", "Inventory", Statement.BS,
         Section.CURRENT_ASSET, Balance.CREDIT, False, (),
         "Inventories, net."),
    Slot("other_current_assets", "Other current assets", Statement.BS,
         Section.CURRENT_ASSET, Balance.CREDIT, False, (BANK, INSURANCE),
         "Prepaids, current deferred tax/other not in the slots above."),
    Slot("current_assets", "Total current assets", Statement.BS,
         Section.ASSET_TOTAL, Balance.CREDIT, True, (),
         "SUBTOTAL = Σ current-asset slots. Not a fan-out target."),
    # — non-current assets —
    Slot("ppe_net", "Property, plant & equipment, net", Statement.BS,
         Section.NONCURRENT_ASSET, Balance.CREDIT, False, (REIT,),
         "PP&E net of accumulated depreciation; REIT real estate net."),
    Slot("goodwill", "Goodwill", Statement.BS,
         Section.NONCURRENT_ASSET, Balance.CREDIT, False, (),
         "Goodwill."),
    Slot("intangible_assets", "Intangible assets ex-goodwill",
         Statement.BS, Section.NONCURRENT_ASSET, Balance.CREDIT, False, (),
         "Finite/indefinite-lived intangibles excluding goodwill."),
    Slot("long_term_investments", "Long-term investments", Statement.BS,
         Section.NONCURRENT_ASSET, Balance.CREDIT, False,
         (BANK, INSURANCE),
         "Non-current marketable/equity-method/other investments. "
         "Bank=loans & investment securities; Insurance=invested assets."),
    Slot("other_noncurrent_assets", "Other non-current assets",
         Statement.BS, Section.NONCURRENT_ASSET, Balance.CREDIT, False,
         (BANK, INSURANCE),
         "Operating-lease ROU assets, deferred tax assets non-current, "
         "other long-term assets. Insurance=DAC / separate-account assets."),
    Slot("total_assets", "Total assets", Statement.BS,
         Section.ASSET_TOTAL, Balance.CREDIT, True, (),
         "SUBTOTAL = Σ all asset slots. Not a fan-out target."),
    # — current liabilities —
    Slot("accounts_payable", "Accounts payable", Statement.BS,
         Section.CURRENT_LIABILITY, Balance.DEBIT, False, (),
         "Trade payables, current."),
    Slot("short_term_debt", "Short-term & current debt", Statement.BS,
         Section.CURRENT_LIABILITY, Balance.DEBIT, False, (BANK,),
         "Commercial paper, short-term borrowings, current portion of "
         "long-term debt, current capital-lease & floor-plan/non-recourse "
         "/loaner-vehicle notes."),
    Slot("operating_lease_liability_current",
         "Operating lease liability (current)", Statement.BS,
         Section.CURRENT_LIABILITY, Balance.DEBIT, False, (),
         "ASC 842 operating-lease liability, current portion."),
    Slot("other_current_liabilities", "Other current liabilities",
         Statement.BS, Section.CURRENT_LIABILITY, Balance.DEBIT, False,
         (BANK, INSURANCE),
         "Accrued liabilities, deferred revenue current, taxes payable, "
         "other. Bank=deposits & short-term funding."),
    Slot("current_liabilities", "Total current liabilities", Statement.BS,
         Section.LIABILITY_TOTAL, Balance.DEBIT, True, (),
         "SUBTOTAL = Σ current-liability slots. Not a fan-out target."),
    # — non-current liabilities —
    Slot("long_term_debt", "Long-term debt (non-current)", Statement.BS,
         Section.NONCURRENT_LIABILITY, Balance.DEBIT, False, (BANK,),
         "Long-term debt & capital-lease obligations, non-current; "
         "bundled including-current-maturities total resolves here."),
    Slot("operating_lease_liability_noncurrent",
         "Operating lease liability (non-current)", Statement.BS,
         Section.NONCURRENT_LIABILITY, Balance.DEBIT, False, (),
         "ASC 842 operating-lease liability, non-current portion, or "
         "the bundled total."),
    Slot("deferred_tax_liability_noncurrent",
         "Deferred tax liabilities (non-current)", Statement.BS,
         Section.NONCURRENT_LIABILITY, Balance.DEBIT, False, (),
         "Non-current deferred income tax liabilities."),
    Slot("other_noncurrent_liabilities", "Other non-current liabilities",
         Statement.BS, Section.NONCURRENT_LIABILITY, Balance.DEBIT, False,
         (BANK, INSURANCE),
         "Pension/OPEB, other long-term liabilities. Insurance=policy "
         "reserves / separate-account liabilities; Bank=long-term funding."),
    Slot("total_liabilities", "Total liabilities", Statement.BS,
         Section.LIABILITY_TOTAL, Balance.DEBIT, True, (),
         "SUBTOTAL = Σ all liability slots. Not a fan-out target."),
    # — equity —
    Slot("common_stock_apic", "Common stock & additional paid-in capital",
         Statement.BS, Section.EQUITY, Balance.DEBIT, False, (),
         "Par value + APIC (preferred par when separately tagged too)."),
    Slot("retained_earnings", "Retained earnings / accumulated deficit",
         Statement.BS, Section.EQUITY, Balance.EITHER, False, (),
         "Retained earnings (credit) or accumulated deficit (debit)."),
    Slot("treasury_stock", "Treasury stock", Statement.BS,
         Section.EQUITY, Balance.CREDIT, False, (),
         "Contra-equity treasury shares at cost (debit-balance asset-"
         "side sign, reduces equity)."),
    Slot("accumulated_oci", "Accumulated other comprehensive income",
         Statement.BS, Section.EQUITY, Balance.EITHER, False, (),
         "AOCI, mixed sign."),
    Slot("noncontrolling_interest", "Non-controlling interest",
         Statement.BS, Section.EQUITY, Balance.EITHER, False, (),
         "Minority interest within equity."),
    Slot("total_equity", "Total equity", Statement.BS,
         Section.EQUITY_TOTAL, Balance.DEBIT, True, (),
         "SUBTOTAL = Σ equity slots (StockholdersEquity / "
         "PartnersCapital / incl-NCI). Not a fan-out target."),
    Slot("shares_outstanding", "Shares issued / outstanding",
         Statement.BS, Section.EQUITY, Balance.NA, False, (),
         "Common shares issued/outstanding. Non-monetary."),
)


# ── Cash-flow closed set ──────────────────────────────────────────────
# Order follows the indirect-method statement. Section subtotals (cfo,
# cfi, cff) and change_in_cash are present so the
# CFO + CFI + CFF + fx ≡ ΔCash identity is expressible from the closed
# set, but they are NOT valid fan-out targets.
_CF_SLOTS: tuple[Slot, ...] = (
    # — operating —
    Slot("cf_net_income", "Net income (indirect-method start)",
         Statement.CF, Section.OPERATING, Balance.EITHER, False, (),
         "Net income line that opens the indirect reconciliation."),
    Slot("cf_depreciation_amortization", "Depreciation & amortization",
         Statement.CF, Section.OPERATING, Balance.CREDIT, False, (),
         "Non-cash D&A add-back. CROSS-STATEMENT: must stay consistent "
         "with the IS D&A add-back chain (feeds EBITDA)."),
    Slot("cf_impairment", "Impairment & asset writedowns", Statement.CF,
         Section.OPERATING, Balance.CREDIT, False, (),
         "Non-cash goodwill/asset impairment add-back. CROSS-STATEMENT: "
         "must stay consistent with the IS impairment add-back chains."),
    Slot("cf_stock_based_comp", "Stock-based compensation", Statement.CF,
         Section.OPERATING, Balance.CREDIT, False, (),
         "Non-cash SBC add-back."),
    Slot("cf_deferred_taxes", "Deferred income taxes", Statement.CF,
         Section.OPERATING, Balance.EITHER, False, (),
         "Deferred tax provision/benefit reconciling item."),
    Slot("cf_working_capital_change", "Change in working capital",
         Statement.CF, Section.OPERATING, Balance.EITHER, False, (),
         "Changes in receivables/inventory/payables/other operating "
         "assets & liabilities. Mixed sign."),
    Slot("cf_other_operating", "Other operating activities", Statement.CF,
         Section.OPERATING, Balance.EITHER, False, (BANK, INSURANCE),
         "Operating reconciling items not in the slots above."),
    Slot("cfo", "Net cash from operating activities", Statement.CF,
         Section.OPERATING_TOTAL, Balance.EITHER, True, (),
         "SUBTOTAL. Not a fan-out target."),
    # — investing —
    Slot("cf_capex", "Capital expenditure", Statement.CF,
         Section.INVESTING, Balance.DEBIT, False, (),
         "Payments to acquire PP&E / capitalized software / real estate."),
    Slot("cf_acquisitions", "Acquisitions, net", Statement.CF,
         Section.INVESTING, Balance.DEBIT, False, (),
         "Cash paid for business acquisitions, net of cash acquired."),
    Slot("cf_asset_sales", "Proceeds from asset/business sales",
         Statement.CF, Section.INVESTING, Balance.CREDIT, False, (),
         "Proceeds from sale of PP&E / businesses / divestitures."),
    Slot("cf_investments", "Investment purchases & maturities",
         Statement.CF, Section.INVESTING, Balance.EITHER, False,
         (BANK, INSURANCE),
         "Purchases/sales/maturities of investment securities. Mixed "
         "sign. Bank/Insurance=loan & investment-portfolio flows."),
    Slot("cf_other_investing", "Other investing activities", Statement.CF,
         Section.INVESTING, Balance.EITHER, False, (),
         "Investing reconciling items not in the slots above."),
    Slot("cfi", "Net cash from investing activities", Statement.CF,
         Section.INVESTING_TOTAL, Balance.EITHER, True, (),
         "SUBTOTAL. Not a fan-out target."),
    # — financing —
    Slot("cf_debt_issuance", "Proceeds from debt", Statement.CF,
         Section.FINANCING, Balance.CREDIT, False, (),
         "Proceeds from issuance of long-term/short-term debt."),
    Slot("cf_debt_repayment", "Repayments of debt", Statement.CF,
         Section.FINANCING, Balance.DEBIT, False, (),
         "Repayments of long-term/short-term debt & finance leases."),
    Slot("cf_dividends_paid", "Dividends paid", Statement.CF,
         Section.FINANCING, Balance.DEBIT, False, (),
         "Dividends paid to common/preferred/NCI."),
    Slot("cf_share_repurchase", "Share repurchases", Statement.CF,
         Section.FINANCING, Balance.DEBIT, False, (),
         "Payments to repurchase common/preferred stock."),
    Slot("cf_share_issuance", "Proceeds from share issuance",
         Statement.CF, Section.FINANCING, Balance.CREDIT, False, (),
         "Proceeds from issuance of stock / option exercises."),
    Slot("cf_other_financing", "Other financing activities", Statement.CF,
         Section.FINANCING, Balance.EITHER, False, (),
         "Financing reconciling items not in the slots above."),
    Slot("cff", "Net cash from financing activities", Statement.CF,
         Section.FINANCING_TOTAL, Balance.EITHER, True, (),
         "SUBTOTAL. Not a fan-out target."),
    # — reconciliation —
    Slot("cf_fx_effect", "Effect of FX on cash", Statement.CF,
         Section.CASH_RECONCILIATION, Balance.EITHER, False, (),
         "Exchange-rate effect on cash & equivalents."),
    Slot("cf_change_in_cash", "Net change in cash", Statement.CF,
         Section.CASH_RECONCILIATION, Balance.EITHER, True, (),
         "SUBTOTAL ≡ CFO + CFI + CFF + FX. Not a fan-out target."),
)


SLOTS: tuple[Slot, ...] = _BS_SLOTS + _CF_SLOTS
SLOTS_BY_ID: dict[str, Slot] = {s.id: s for s in SLOTS}

BS_SLOTS_BY_ID: dict[str, Slot] = {s.id: s for s in _BS_SLOTS}
CF_SLOTS_BY_ID: dict[str, Slot] = {s.id: s for s in _CF_SLOTS}

#: IDs a classifier may emit, per statement. Subtotals excluded: a raw
#: EDGAR tag is always an input line; subtotals are computed.
BS_FANOUT_TARGETS: tuple[str, ...] = tuple(
    s.id for s in _BS_SLOTS if not s.subtotal
)
CF_FANOUT_TARGETS: tuple[str, ...] = tuple(
    s.id for s in _CF_SLOTS if not s.subtotal
)

#: Slots that carry an industry overlay variant, keyed by overlay and
#: statement. Membership only. BS overlay deltas are re-derived here
#: and intentionally NOT inherited from the income-statement overlay set.
OVERLAYS: dict[str, dict[str, tuple[str, ...]]] = {
    "BS": {
        BANK: tuple(s.id for s in _BS_SLOTS if BANK in s.overlays),
        INSURANCE: tuple(s.id for s in _BS_SLOTS if INSURANCE in s.overlays),
        REIT: tuple(s.id for s in _BS_SLOTS if REIT in s.overlays),
    },
    "CF": {
        BANK: tuple(s.id for s in _CF_SLOTS if BANK in s.overlays),
        INSURANCE: tuple(s.id for s in _CF_SLOTS if INSURANCE in s.overlays),
        REIT: tuple(s.id for s in _CF_SLOTS if REIT in s.overlays),
    },
}
