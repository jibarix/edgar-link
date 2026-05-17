"""Deterministic pre-filter for cash-flow tag classification.

The CF analog of the income-statement / balance-sheet pre-filters.
Splits the ~1,301 us-gaap CF tags (sec_tag_mapping.json,
statement == "CF") into ``confident`` / ``subtotal`` / ``ambiguous``,
same dispositions and same merge-guardrail discipline as the BS
pre-filter.

CF polarity convention (see _statement_taxonomy.Balance): CREDIT = a
cash *inflow* or non-cash add-back (proceeds, issuance, D&A/impairment/
SBC add-backs), DEBIT = a cash *outflow* (payments, purchases,
repurchases, repayments). Genuinely mixed reconciling items
(working-capital deltas, deferred taxes, the section subtotals) are
EITHER and exempt from the guardrail.

The decisive axis is the activity section (operating / investing /
financing / reconciliation), and the slot set is the analog of the
clean-EBIT closed set: it must let the
CFO + CFI + CFF + FX ≡ ΔCash identity be expressed. D&A and impairment
slots are CROSS-STATEMENT (they also feed the IS EBITDA/EBIT add-backs);
the cf_depreciation_amortization / cf_impairment rules below are kept
deliberately aligned with the IS concept chains so the two maps cannot
disagree.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from edgar.metrics._statement_taxonomy import CF_SLOTS_BY_ID, Balance

# ── Name-polarity proxy (inflow/add-back ⇒ CREDIT, outflow ⇒ DEBIT) ───
# Mixed/net markers veto a one-sided read first.
_CF_MIXED = re.compile(
    r"(IncreaseDecrease|PeriodIncreaseDecrease|NetCashProvidedByUsedIn"
    r"|ProvidedByUsedIn|GainLoss|NetIncomeLoss|ProfitLoss"
    r"|EffectOfExchangeRate)"
)
_OUTFLOW = re.compile(
    r"(^Payments|PaymentsTo|PaymentsFor|PaymentsOf|Repayments"
    r"|RepaymentsOf|Repurchase|PaymentsForRepurchase"
    r"|PaymentsToAcquire|PaymentsToDevelop)"
)
_INFLOW = re.compile(
    r"(^Proceeds|ProceedsFrom|Depreciation|Amortization|Depletion"
    r"|ShareBasedCompensation|AllocatedShareBasedCompensation"
    r"|Impairment|AssetImpairment|GoodwillImpairment"
    r"|ProvisionForDoubtfulAccounts)"
)


def name_polarity(tag: str) -> str:
    """'credit'(inflow/add-back) | 'debit'(outflow) | 'unknown'."""
    if _CF_MIXED.search(tag):
        return "unknown"
    o, i = bool(_OUTFLOW.search(tag)), bool(_INFLOW.search(tag))
    if o and not i:
        return "debit"
    if i and not o:
        return "credit"
    return "unknown"


def balance_contradiction(tag: str, slot_id: str) -> bool:
    """True if name polarity contradicts the slot's expected balance."""
    slot = CF_SLOTS_BY_ID.get(slot_id)
    if slot is None or slot.balance in (Balance.EITHER, Balance.NA):
        return False
    pol = name_polarity(tag)
    if pol == "unknown":
        return False
    return pol != slot.balance.value


# ── Anchored classification rules ─────────────────────────────────────
# (regex, slot_id). FIRST match wins, so order = specificity.
_RULES: tuple[tuple[re.Pattern, str], ...] = tuple(
    (re.compile(p), s) for p, s in [
        # --- reported subtotals (NOT fan-out / NOT input lines) -------
        (r"^NetCashProvidedByUsedInOperatingActivities"
         r"(ContinuingOperations)?$", "cfo"),
        (r"^NetCashProvidedByUsedInInvestingActivities"
         r"(ContinuingOperations)?$", "cfi"),
        (r"^NetCashProvidedByUsedInFinancingActivities"
         r"(ContinuingOperations)?$", "cff"),
        (r"^(CashCashEquivalentsRestrictedCashAndRestrictedCash"
         r"EquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect"
         r"|CashAndCashEquivalentsPeriodIncreaseDecrease"
         r"|CashCashEquivalentsRestrictedCashAndRestrictedCash"
         r"EquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect)$",
         "cf_change_in_cash"),
        # --- reconciliation ------------------------------------------
        (r"^EffectOfExchangeRateOn(Cash"
         r"(AndCashEquivalents|CashCashEquivalentsRestrictedCashAnd"
         r"RestrictedCashEquivalents)?)$", "cf_fx_effect"),
        # --- operating add-backs (CROSS-STATEMENT with the IS map) ----
        (r"^(DepreciationDepletionAndAmortization"
         r"|DepreciationAndAmortization|Depreciation"
         r"|DepreciationAmortizationAndAccretionNet"
         r"|AmortizationOfIntangibleAssets)$",
         "cf_depreciation_amortization"),
        (r"(GoodwillImpairmentLoss|AssetImpairmentCharges"
         r"|TangibleAssetImpairmentCharges"
         r"|ImpairmentOfIntangibleAssets"
         r"(Indefinitelived)?ExcludingGoodwill"
         r"|ImpairmentOfLongLivedAssetsHeldForUse"
         r"|ImpairmentOfRealEstate)$", "cf_impairment"),
        (r"^(ShareBasedCompensation"
         r"|AllocatedShareBasedCompensationExpense"
         r"|ShareBasedCompensationRequisiteServicePeriodRecognition)$",
         "cf_stock_based_comp"),
        (r"^DeferredIncomeTaxExpenseBenefit$", "cf_deferred_taxes"),
        (r"^(IncreaseDecreaseInOperatingCapital"
         r"|IncreaseDecreaseInAccountsReceivable"
         r"|IncreaseDecreaseInInventories"
         r"|IncreaseDecreaseInAccountsPayable"
         r"|IncreaseDecreaseInAccountsPayableAndAccruedLiabilities"
         r"|IncreaseDecreaseInOtherOperatingCapitalNet)$",
         "cf_working_capital_change"),
        # --- investing -----------------------------------------------
        (r"^PaymentsToAcquire(PropertyPlantAndEquipment"
         r"|ProductiveAssets|RealEstate|MachineryAndEquipment"
         r"|OtherProductiveAssets)$", "cf_capex"),
        (r"^PaymentsToAcquireBusinessesNetOfCashAcquired$",
         "cf_acquisitions"),
        (r"^(ProceedsFromSaleOfPropertyPlantAndEquipment"
         r"|ProceedsFromDivestitureOfBusinesses"
         r"|ProceedsFromSaleOfProductiveAssets)$", "cf_asset_sales"),
        (r"^(PaymentsToAcquireInvestments"
         r"|ProceedsFromSaleMaturityAndCollectionsOfInvestments"
         r"|PaymentsToAcquireMarketableSecurities"
         r"|ProceedsFromSaleOfAvailableForSaleSecurities"
         r"(Debt)?)$", "cf_investments"),
        # --- financing -----------------------------------------------
        (r"^ProceedsFrom(IssuanceOfLongTermDebt|LongTermLinesOfCredit"
         r"|IssuanceOfDebt|NotesPayable"
         r"|IssuanceOfSeniorLongTermDebt)$", "cf_debt_issuance"),
        (r"^RepaymentsOf(LongTermDebt|DebtAndCapitalLeaseObligations"
         r"|SecuredDebt|UnsecuredDebt|SeniorDebt|LinesOfCredit"
         r"|LongTermLinesOfCredit|NotesPayable)$", "cf_debt_repayment"),
        (r"^(PaymentsOfDividends|PaymentsOfDividendsCommonStock"
         r"|PaymentsOfDividendsPreferredStockAndPreferenceStock"
         r"|PaymentsOfDividendsMinorityInterest)$", "cf_dividends_paid"),
        (r"^PaymentsForRepurchaseOf(CommonStock|PreferredStock"
         r"|EquityInstruments)$", "cf_share_repurchase"),
        (r"^ProceedsFromIssuanceOf(CommonStock|PreferredStock"
         r"AndPreferenceStock|SharesUnderIncentiveAndShareBased"
         r"CompensationPlansIncludingStockOptions)$", "cf_share_issuance"),
        (r"^ProceedsFromStockOptionsExercised$", "cf_share_issuance"),
    ]
)


@dataclass(frozen=True)
class Classification:
    tag: str
    slot: str | None
    disposition: str
    rule: str | None
    polarity: str


_SUBTOTAL_SLOTS = {s.id for s in CF_SLOTS_BY_ID.values() if s.subtotal}


def classify(tag: str) -> Classification:
    """Deterministically classify one us-gaap CF tag."""
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
