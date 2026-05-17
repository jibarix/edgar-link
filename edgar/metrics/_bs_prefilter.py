"""Deterministic pre-filter for balance-sheet tag classification.

The BS analog of the income-statement pre-filter. Splits the ~1,434
us-gaap BS tags (sec_tag_mapping.json, statement == "BS") into:

  * ``confident``  — a single anchored rule matched AND the name-
    polarity proxy agrees with the target slot's expected balance.
    Auto-assigned; never sent to the LLM fan-out.
  * ``subtotal``   — the tag IS a reported subtotal (Assets,
    LiabilitiesCurrent, StockholdersEquity, …). Mapped to its subtotal
    slot for provenance but it is NOT an input line and NOT a fan-out
    target (the engine sums inputs; it does not classify a raw subtotal
    tag into the buildup).
  * ``ambiguous``  — everything else. Handed to the fan-out.

Balance polarity here is a NAME-DERIVED PROXY, not the authoritative
us-gaap ``balance`` attribute. Under this module's convention (see
_statement_taxonomy.Balance) an asset-side line is CREDIT and a
liability/equity-side line is DEBIT, chosen so a single enum + a single
guardrail works across BS and CF. A tag whose name polarity contradicts
its assigned slot's expected balance is flagged, never silently merged.
``EITHER`` / ``NA`` slots are exempt (legitimately mixed-sign /
non-monetary).

The decisive axis is the classified-balance-sheet section: is the line
an asset, a liability, or equity, and current vs non-current? Rule
ordering encodes that boundary, and the slot set is the analog of the
clean-EBIT closed set: it must let the Assets ≡ Liabilities + Equity
identity be expressed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from edgar.metrics._statement_taxonomy import BS_SLOTS_BY_ID, Balance

# ── Name-polarity proxy (asset ⇒ CREDIT, liab/equity ⇒ DEBIT) ─────────
# Mixed/net markers veto a one-sided read first.
_BS_MIXED = re.compile(
    r"(NetAssets|AssetsLiabilities|DerivativeAsset|DerivativeLiability"
    r"|RightOfUseAsset.*Liability|FundedStatus)"
)
_ASSET = re.compile(
    r"(Receivable|Inventory|Prepaid|Goodwill|IntangibleAsset"
    r"|PropertyPlantAndEquipment|AssetsCurrent|AssetsNoncurrent"
    r"|^Assets$|CashAndCash|^Cash|RestrictedCash|MarketableSecurities"
    r"|AvailableForSaleSecurities|HeldToMaturitySecurities|DeferredTaxAssets"
    r"|OperatingLeaseRightOfUseAsset|FinanceLeaseRightOfUseAsset"
    r"|InvestmentsAndOtherNoncurrentAssets|EquityMethodInvestments"
    r"|DeferredCosts|FundsHeld|DepositsAssets)"
)
_LIAB_EQ = re.compile(
    r"(Payable|Debt|Borrowings|CommercialPaper|NotesPayable"
    r"|LiabilitiesCurrent|LiabilitiesNoncurrent|^Liabilities$"
    r"|AccruedLiabilities|DeferredRevenue|ContractWithCustomerLiability"
    r"|Obligation|LeaseLiability|PensionAndOtherPostretirement"
    r"|StockholdersEquity|PartnersCapital|RetainedEarnings"
    r"|AdditionalPaidInCapital|CommonStock|PreferredStock|TreasuryStock"
    r"|MinorityInterest|NoncontrollingInterest"
    r"|AccumulatedOtherComprehensiveIncome|DeferredTaxLiabilities"
    r"|Policyholder" r"|InsuranceReserve|SeparateAccount|Deposits$|Deposit)"
)


def name_polarity(tag: str) -> str:
    """'credit'(asset) | 'debit'(liab/equity) | 'unknown' from morphology."""
    if _BS_MIXED.search(tag):
        return "unknown"
    a, l = bool(_ASSET.search(tag)), bool(_LIAB_EQ.search(tag))
    if a and not l:
        return "credit"
    if l and not a:
        return "debit"
    return "unknown"


def balance_contradiction(tag: str, slot_id: str) -> bool:
    """True if name polarity contradicts the slot's expected balance.

    Reusable merge guardrail. EITHER/NA slots and 'unknown' polarity are
    never contradictions.
    """
    slot = BS_SLOTS_BY_ID.get(slot_id)
    if slot is None or slot.balance in (Balance.EITHER, Balance.NA):
        return False
    pol = name_polarity(tag)
    if pol == "unknown":
        return False
    return pol != slot.balance.value


# ── Anchored classification rules ─────────────────────────────────────
# (regex, slot_id). FIRST match wins, so order = specificity. Rules fire
# only on anchored, unambiguous us-gaap morphemes; anything not matched
# is 'ambiguous' and goes to the fan-out (low recall, high precision).
_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(p), s) for p, s in [
        # --- reported subtotals (NOT fan-out / NOT input lines) -------
        (r"^AssetsCurrent$", "current_assets"),
        (r"^Assets$", "total_assets"),
        (r"^LiabilitiesCurrent$", "current_liabilities"),
        (r"^Liabilities$", "total_liabilities"),
        (r"^(StockholdersEquity"
         r"|StockholdersEquityIncludingPortionAttributableToNoncontrolling"
         r"Interest|PartnersCapital|MembersEquity"
         r"|LiabilitiesAndStockholdersEquity)$", "total_equity"),
        # --- current assets ------------------------------------------
        (r"^(CashAndCashEquivalentsAtCarryingValue"
         r"|CashAndCashEquivalents|Cash"
         r"|CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
         r"|RestrictedCashCurrent|RestrictedCashAndCashEquivalents)$",
         "cash"),
        (r"^(MarketableSecuritiesCurrent|ShortTermInvestments"
         r"|AvailableForSaleSecuritiesCurrent"
         r"|AvailableForSaleSecuritiesDebtSecuritiesCurrent"
         r"|TradingSecuritiesCurrent)$", "short_term_investments"),
        (r"^(AccountsReceivableNetCurrent|AccountsReceivableNet"
         r"|ReceivablesNetCurrent|AccountsAndOtherReceivablesNetCurrent"
         r"|NotesAndLoansReceivableNetCurrent)$", "accounts_receivable"),
        (r"^(InventoryNet|Inventory|InventoryFinishedGoodsNetOfReserves"
         r"|RetailRelatedInventoryMerchandise)$", "inventory"),
        # --- non-current assets --------------------------------------
        (r"^(PropertyPlantAndEquipmentNet"
         r"|PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfter"
         r"AccumulatedDepreciationAndAmortization"
         r"|RealEstateInvestmentPropertyNet)$", "ppe_net"),
        (r"^Goodwill$", "goodwill"),
        (r"^(IntangibleAssetsNetExcludingGoodwill"
         r"|FiniteLivedIntangibleAssetsNet"
         r"|IndefiniteLivedIntangibleAssetsExcludingGoodwill)$",
         "intangible_assets"),
        (r"^(MarketableSecuritiesNoncurrent|LongTermInvestments"
         r"|AvailableForSaleSecuritiesNoncurrent"
         r"|InvestmentsAndOtherNoncurrentAssets)$",
         "long_term_investments"),
        (r"^AssetsNoncurrent$", "other_noncurrent_assets"),
        (r"OperatingLeaseRightOfUseAsset$", "other_noncurrent_assets"),
        # --- current liabilities -------------------------------------
        (r"^(AccountsPayableCurrent|AccountsPayable"
         r"|AccountsPayableAndAccruedLiabilitiesCurrent"
         r"|AccountsPayableTradeCurrent)$", "accounts_payable"),
        (r"^(CommercialPaper|ShortTermBorrowings"
         r"|LongTermDebtAndCapitalLeaseObligationsCurrent"
         r"|LongTermDebtCurrent|DebtCurrent"
         r"|SecuredDebtCurrent|LinesOfCreditCurrent"
         r"|FloorPlanNotesPayable|NonrecourseNotesPayable"
         r"|LoanerVehicleNotesPayable)$", "short_term_debt"),
        (r"^OperatingLeaseLiabilityCurrent$",
         "operating_lease_liability_current"),
        # --- non-current liabilities ---------------------------------
        (r"^(LongTermDebtAndCapitalLeaseObligations"
         r"|LongTermDebtNoncurrent|LongTermDebt"
         r"|LongTermDebtAndCapitalLeaseObligationsIncludingCurrent"
         r"Maturities|SecuredDebt|UnsecuredDebt|SeniorNotes"
         r"|ConvertibleDebtNoncurrent)$", "long_term_debt"),
        (r"^(OperatingLeaseLiabilityNoncurrent"
         r"|OperatingLeaseLiability)$",
         "operating_lease_liability_noncurrent"),
        (r"^(DeferredTaxLiabilitiesNoncurrent"
         r"|DeferredIncomeTaxLiabilitiesNet"
         r"|DeferredTaxLiabilitiesNet)$",
         "deferred_tax_liability_noncurrent"),
        (r"^LiabilitiesNoncurrent$", "other_noncurrent_liabilities"),
        # --- equity ---------------------------------------------------
        (r"^(CommonStocksIncludingAdditionalPaidInCapital"
         r"|AdditionalPaidInCapital"
         r"|AdditionalPaidInCapitalCommonStock"
         r"|CommonStockValue|CommonStockSharesValue)$",
         "common_stock_apic"),
        (r"^RetainedEarningsAccumulatedDeficit$", "retained_earnings"),
        (r"^TreasuryStock(Value|CommonValue)?$", "treasury_stock"),
        (r"^AccumulatedOtherComprehensiveIncomeLossNetOfTax$",
         "accumulated_oci"),
        (r"^(MinorityInterest"
         r"|StockholdersEquityIncludingPortionAttributableToNon"
         r"controllingInterest)$", "noncontrolling_interest"),
        (r"^(CommonStockSharesOutstanding|CommonStockSharesIssued"
         r"|SharesOutstanding|SharesIssued)$", "shares_outstanding"),
    ]
)


@dataclass(frozen=True)
class Classification:
    tag: str
    slot: str | None          # None => ambiguous
    disposition: str          # 'confident' | 'subtotal' | 'ambiguous'
    rule: str | None
    polarity: str


_SUBTOTAL_SLOTS = {s.id for s in BS_SLOTS_BY_ID.values() if s.subtotal}


def classify(tag: str) -> Classification:
    """Deterministically classify one us-gaap BS tag."""
    pol = name_polarity(tag)
    for rx, slot_id in _RULES:
        if rx.search(tag):
            if slot_id in _SUBTOTAL_SLOTS:
                return Classification(tag, slot_id, "subtotal",
                                      rx.pattern, pol)
            if balance_contradiction(tag, slot_id):
                return Classification(tag, None, "ambiguous",
                                      rx.pattern, pol)
            return Classification(tag, slot_id, "confident",
                                  rx.pattern, pol)
    return Classification(tag, None, "ambiguous", None, pol)
