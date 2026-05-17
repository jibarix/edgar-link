"""Fallback chains for us-gaap concept names.

Issuers vary in which concept they tag for the same logical line item
(e.g. revenue is `Revenues` for some, `RevenueFromContractWithCustomerExcludingAssessedTax`
for others). Each entry below is an ordered list — the resolver picks
the first match with non-null values.

Tuples are (category, concept_name). The category disambiguates when
the same concept name appears under different statement classifications
(rare but happens).
"""

from __future__ import annotations

# Each chain returns the first concept whose values are populated for
# the requested periods.

CONCEPT_CHAINS: dict[str, list[tuple[str, str]]] = {
    # ── Income statement ──
    "revenue": [
        ("Revenue", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("Revenue", "Revenues"),
        ("Revenue", "SalesRevenueNet"),
        ("Revenue", "SalesRevenueGoodsNet"),
        ("Revenue", "RevenueFromContractWithCustomerIncludingAssessedTax"),
    ],
    "cogs": [
        ("Income", "CostOfGoodsAndServicesSold"),
        ("Income", "CostOfRevenue"),
        ("Income", "CostOfGoodsSold"),
        ("Income", "CostOfServices"),
    ],
    "gross_profit": [
        ("Income", "GrossProfit"),
    ],
    "rd": [
        ("Income", "ResearchAndDevelopmentExpense"),
    ],
    "sga": [
        ("Income", "SellingGeneralAndAdministrativeExpense"),
    ],
    "sga_selling": [
        ("Income", "SellingAndMarketingExpense"),
    ],
    "sga_general": [
        ("Income", "GeneralAndAdministrativeExpense"),
    ],
    "total_opex": [
        ("Income", "OperatingExpenses"),
    ],
    "operating_income": [
        ("Income", "OperatingIncomeLoss"),
    ],
    "nonoperating_income": [
        ("Income", "NonoperatingIncomeExpense"),
    ],
    # The analyst convention strips goodwill impairment from Operating
    # Income into Unusual Items. Issuers tag it under Income or duplicate
    # it under OperatingCashFlow (non-cash addback).
    "goodwill_impairment": [
        ("Income", "GoodwillImpairmentLoss"),
        ("OperatingCashFlow", "GoodwillImpairmentLoss"),
    ],
    # The analyst convention reclassifies held-for-sale impairments (which
    # dealers take when divesting stores) into Unusual Items as part of
    # Gain/Loss on Sale of Assets. Empirically this broad us-gaap impairment
    # bucket lines up with the normalized Operating Income adjustment.
    "asset_impairment": [
        ("Income", "AssetImpairmentCharges"),
        ("OperatingCashFlow", "AssetImpairmentCharges"),
        ("Income", "TangibleAssetImpairmentCharges"),
        ("OperatingCashFlow", "TangibleAssetImpairmentCharges"),
        ("Income", "ImpairmentOfIntangibleAssetsExcludingGoodwill"),
        ("OperatingCashFlow", "ImpairmentOfIntangibleAssetsExcludingGoodwill"),
        ("Income", "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill"),
        ("OperatingCashFlow", "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill"),
        ("Income", "ImpairmentOfLongLivedAssetsHeldForUse"),
        ("OperatingCashFlow", "ImpairmentOfLongLivedAssetsHeldForUse"),
    ],
    "interest_expense": [
        ("Income", "InterestExpense"),
        ("Income", "InterestExpenseDebt"),
        # Hybrid-finance issuers (e.g. KMX) classify InterestExpense under
        # Revenue because their finance segment nets it into revenue.
        ("Revenue", "InterestExpense"),
        ("Revenue", "InterestExpenseDebt"),
        # Real-estate filers (e.g. NEN) tag mortgage interest as
        # InterestExpenseNonoperating. Harmless as a trailing fallback —
        # unambiguously interest expense regardless of issuer type.
        ("Income", "InterestExpenseNonoperating"),
    ],
    "pretax_income": [
        ("Income", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"),
        ("Income", "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    ],
    "tax_expense": [
        ("Income", "IncomeTaxExpenseBenefit"),
    ],
    "net_income": [
        ("Income", "NetIncomeLoss"),
        ("Income", "ProfitLoss"),
    ],
    "effective_tax_rate": [
        ("Income", "EffectiveIncomeTaxRateContinuingOperations"),
    ],

    # ── EPS / shares ──
    "eps_basic": [
        ("EPS", "EarningsPerShareBasic"),
    ],
    "eps_diluted": [
        ("EPS", "EarningsPerShareDiluted"),
    ],
    "shares_basic_wavg": [
        ("EPS", "WeightedAverageNumberOfSharesOutstandingBasic"),
    ],
    "shares_diluted_wavg": [
        ("EPS", "WeightedAverageNumberOfDilutedSharesOutstanding"),
    ],
    "shares_out": [
        ("Equity", "CommonStockSharesOutstanding"),
    ],
    "dividends_per_share": [
        ("EPS", "CommonStockDividendsPerShareDeclared"),
    ],

    # ── Balance sheet: assets ──
    "cash": [
        ("Assets", "CashAndCashEquivalentsAtCarryingValue"),
        ("Assets", "CashAndCashEquivalents"),
        ("Assets", "Cash"),
    ],
    "short_term_investments": [
        ("Assets", "MarketableSecuritiesCurrent"),
        ("Assets", "ShortTermInvestments"),
        ("Assets", "AvailableForSaleSecuritiesCurrent"),
    ],
    "long_term_investments": [
        ("Assets", "MarketableSecuritiesNoncurrent"),
        ("Assets", "LongTermInvestments"),
    ],
    "accounts_receivable": [
        ("Assets", "AccountsReceivableNetCurrent"),
        ("Assets", "AccountsReceivableNet"),
    ],
    "inventory": [
        ("Assets", "InventoryNet"),
        ("Assets", "Inventory"),
    ],
    "current_assets": [
        ("Assets", "AssetsCurrent"),
    ],
    "ppe_net": [
        ("Assets", "PropertyPlantAndEquipmentNet"),
        ("Assets", "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization"),
    ],
    "goodwill": [
        ("Assets", "Goodwill"),
    ],
    "intangible_assets_ex_goodwill": [
        ("Assets", "IntangibleAssetsNetExcludingGoodwill"),
        ("Assets", "FiniteLivedIntangibleAssetsNet"),
    ],
    "total_assets": [
        ("Assets", "Assets"),
    ],

    # ── Balance sheet: liabilities ──
    "accounts_payable": [
        ("Liabilities", "AccountsPayableCurrent"),
        ("Liabilities", "AccountsPayable"),
    ],
    "current_liabilities": [
        ("Liabilities", "LiabilitiesCurrent"),
    ],
    # Two reporting styles in dealer/retailer XBRL:
    #   (a) LongTermDebt[Current|Noncurrent]
    #   (b) LongTermDebtAndCapitalLeaseObligations[Current|<base>]
    # Issuers pick one or the other — listed in priority order so the
    # broader (capital-lease-inclusive) variant wins when both exist.
    "long_term_debt_current": [
        ("Liabilities", "LongTermDebtAndCapitalLeaseObligationsCurrent"),
        ("Liabilities", "LongTermDebtCurrent"),
    ],
    "long_term_debt_noncurrent": [
        ("Liabilities", "LongTermDebtAndCapitalLeaseObligations"),
        ("Liabilities", "LongTermDebtNoncurrent"),
        ("Liabilities", "LongTermDebt"),
    ],
    # Some issuers (e.g. ABG) only tag the combined current+noncurrent total.
    # Used as a last-resort fallback when neither split tag resolves.
    "long_term_debt_total": [
        ("Liabilities", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"),
    ],
    "commercial_paper": [
        ("Liabilities", "CommercialPaper"),
    ],
    "short_term_borrowings": [
        ("Liabilities", "ShortTermBorrowings"),
    ],
    # Synthesized from company-extension XBRL via edgar/_extension_mappings.py
    # — dealer floor-plan inventory financing, KMX auto-finance non-recourse
    # notes, ABG loaner-vehicle notes. Company Facts API doesn't expose these.
    "floor_plan_debt": [
        ("Liabilities", "FloorPlanNotesPayable"),
    ],
    "nonrecourse_debt": [
        ("Liabilities", "NonrecourseNotesPayable"),
    ],
    "loaner_vehicle_debt": [
        ("Liabilities", "LoanerVehicleNotesPayable"),
    ],
    # ASC 842 operating lease liabilities. Issuers tag either the bundled
    # total or a current/noncurrent split; resolver merges via the chain.
    "operating_lease_liability": [
        ("Liabilities", "OperatingLeaseLiability"),
    ],
    "operating_lease_liability_current": [
        ("Liabilities", "OperatingLeaseLiabilityCurrent"),
    ],
    "operating_lease_liability_noncurrent": [
        ("Liabilities", "OperatingLeaseLiabilityNoncurrent"),
    ],
    "total_liabilities": [
        ("Liabilities", "Liabilities"),
    ],

    # ── Balance sheet: equity ──
    "common_stock_apic": [
        ("Equity", "CommonStocksIncludingAdditionalPaidInCapital"),
    ],
    "retained_earnings": [
        ("Equity", "RetainedEarningsAccumulatedDeficit"),
    ],
    "total_equity": [
        ("Equity", "StockholdersEquity"),
        ("Equity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
        # Limited partnerships (e.g. NEN) report PartnersCapital instead of
        # StockholdersEquity. No corporate issuer tags this — safe globally.
        ("Equity", "PartnersCapital"),
    ],

    # ── Cash flow ──
    "cfo": [
        ("OperatingCashFlow", "NetCashProvidedByUsedInOperatingActivities"),
    ],
    "cfi": [
        ("InvestingCashFlow", "NetCashProvidedByUsedInInvestingActivities"),
    ],
    "cff": [
        ("FinancingCashFlow", "NetCashProvidedByUsedInFinancingActivities"),
    ],
    "capex": [
        ("InvestingCashFlow", "PaymentsToAcquirePropertyPlantAndEquipment"),
    ],
    "depreciation_amortization": [
        ("OperatingCashFlow", "DepreciationDepletionAndAmortization"),
        ("OperatingCashFlow", "DepreciationAndAmortization"),
        ("OperatingCashFlow", "Depreciation"),
    ],
    "stock_based_comp": [
        ("OperatingCashFlow", "ShareBasedCompensation"),
        ("OperatingCashFlow", "AllocatedShareBasedCompensationExpense"),
    ],
    "dividends_paid": [
        ("FinancingCashFlow", "PaymentsOfDividends"),
        ("FinancingCashFlow", "PaymentsOfDividendsCommonStock"),
    ],
    "share_repurchases": [
        ("FinancingCashFlow", "PaymentsForRepurchaseOfCommonStock"),
    ],
}


def chain(name: str) -> list[tuple[str, str]]:
    """Resolve a logical concept name to its (category, concept) fallback chain."""
    return CONCEPT_CHAINS[name]
