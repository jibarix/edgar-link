"""Parser for SEC XBRL Company Facts API data.

Classifies concepts into financial-statement categories via the
deterministic TagClassifier (builtin overrides + sec_tag_mapping.json).
"""
from __future__ import annotations

import copy
import re
import logging
from collections import Counter, defaultdict

from edgar._extension_mappings import ExtensionRule, apply_rules
from edgar.tag_classifier import TagClassifier
from edgar.xbrl_instance import parse_instance
from utils.cache import Cache

logger = logging.getLogger(__name__)
parser_cache = Cache("xbrl_parser")


class XBRLParser:
    """Parser for financial data from SEC APIs."""

    def __init__(self):
        self.classifier = TagClassifier()

    def parse_company_facts(self, facts_data, statement_type=None, period_type='annual', num_periods=1):
        """
        Parse company facts JSON data from SEC API.

        Args:
            facts_data: JSON data from SEC Company Facts API
            statement_type: Type of financial statement (BS, IS, CF, ALL)
            period_type: 'annual', 'quarterly', or 'ytd'
            num_periods: Number of most recent periods to include

        Returns:
            dict: Normalized financial data by period
        """
        if not facts_data or 'facts' not in facts_data:
            logger.error("Invalid company facts data")
            return {}

        taxonomies = []
        if 'us-gaap' in facts_data['facts']:
            taxonomies.append('us-gaap')
        if 'ifrs-full' in facts_data['facts']:
            taxonomies.append('ifrs-full')
        # Synthetic taxonomy populated by augment_with_extensions(); contains
        # canonical concepts derived from company-extension XBRL tags.
        if 'ext' in facts_data['facts']:
            taxonomies.append('ext')

        if not taxonomies:
            logger.error("No supported taxonomies found in company facts")
            return {}

        entity_name = facts_data.get('entityName', '')
        logger.info(f"Processing XBRL data for {entity_name}")

        # Collect ALL concept names from the data
        all_concept_names = set()
        for taxonomy in taxonomies:
            all_concept_names.update(facts_data['facts'].get(taxonomy, {}).keys())

        logger.info(f"Found {len(all_concept_names)} unique concepts in data")

        # Classify all concepts via the builtin + SEC tag mapping.
        classifications = self.classifier.classify_tags(
            list(all_concept_names),
            statement_type=statement_type
        )

        logger.info(f"Classified {len(classifications)} concepts for statement type '{statement_type}'")

        # Extract values for classified concepts
        concept_values = defaultdict(list)
        all_periods = []

        for taxonomy in taxonomies:
            for concept, concept_data in facts_data['facts'].get(taxonomy, {}).items():
                if concept not in classifications:
                    continue

                units = concept_data.get('units', {})
                for unit_type, facts in units.items():
                    if unit_type not in ['USD', 'USD/shares', 'pure', 'shares']:
                        continue

                    for fact in facts:
                        if 'val' not in fact or 'end' not in fact:
                            continue

                        all_periods.append(fact['end'])
                        concept_values[concept].append({
                            'val': fact['val'],
                            'end': fact['end'],
                            'start': fact.get('start'),
                            'unit': unit_type,
                            'filed': fact.get('filed'),
                            'accn': fact.get('accn'),
                        })

        # Build financial_data grouped by category
        financial_data = {}
        concept_units = {}
        for concept, info in classifications.items():
            if concept not in concept_values:
                continue
            category = info['category']
            if category not in financial_data:
                financial_data[category] = {}
            # Resolve the originating taxonomy (us-gaap / ifrs-full / ext-synthetic)
            taxonomy = next(
                (t for t in taxonomies if concept in facts_data['facts'].get(t, {})),
                taxonomies[0],
            )
            tag = f"{taxonomy}:{concept}"
            financial_data[category][tag] = concept_values[concept]
            # Track dominant unit for this concept
            units_seen = [v['unit'] for v in concept_values[concept]]
            concept_units[concept] = max(set(units_seen), key=units_seen.count) if units_seen else 'USD'

        # Normalize
        normalized = self._normalize_api_data(
            financial_data, period_type, all_periods,
            num_periods, classifications, concept_units
        )

        if normalized and normalized.get('periods'):
            logger.info(f"Retrieved data for {len(normalized['periods'])} periods: {normalized['periods']}")
            return normalized
        else:
            logger.warning("No periods found in normalized data")
            return {}

    def _normalize_api_data(self, financial_data, period_type, all_periods, num_periods, classifications, concept_units=None):
        """
        Normalize financial data into a structured format.

        Args:
            financial_data: Extracted financial data grouped by category
            period_type: 'annual', 'quarterly', or 'ytd'
            all_periods: All period end dates found in the data
            num_periods: Number of periods to return
            classifications: dict of concept_name -> classification info
            concept_units: dict of concept_name -> unit string (USD, USD/shares, etc.)

        Returns:
            dict: Normalized financial data by period
        """
        if concept_units is None:
            concept_units = {}
        category_order = {
            'Assets': 0, 'Liabilities': 1, 'Equity': 2,
            'Revenue': 3, 'Income': 4, 'EPS': 5,
            'OperatingCashFlow': 6, 'InvestingCashFlow': 7, 'FinancingCashFlow': 8,
        }

        # Collect all unique periods from the data
        periods = set()
        for category in financial_data:
            for tag in financial_data[category]:
                for fact in financial_data[category][tag]:
                    periods.add(fact['end'])

        fiscal_month = self._detect_fiscal_year_end(all_periods)
        logger.info(f"Detected fiscal year end month: {fiscal_month}")

        # Filter periods
        filtered_periods = []
        for period in periods:
            try:
                if period_type == 'annual' and period.split('-')[1] == fiscal_month:
                    filtered_periods.append(period)
                elif period_type == 'quarterly':
                    filtered_periods.append(period)
                elif period_type == 'ytd':
                    filtered_periods.append(period)
            except (ValueError, IndexError):
                continue

        if not filtered_periods:
            filtered_periods = list(periods)
            logger.warning(f"No periods matched fiscal year end month {fiscal_month}, using all periods")

        sorted_periods = sorted(filtered_periods, reverse=True)
        limited_periods = sorted_periods[:num_periods]

        if not limited_periods:
            logger.warning("No periods available after filtering")
            return None

        normalized_data = {
            'periods': limited_periods,
            'metrics': {},
        }

        for category in sorted(financial_data.keys(), key=lambda x: category_order.get(x, 99)):
            for tag in financial_data[category]:
                concept_name = tag.split(':')[-1]
                info = classifications.get(concept_name, {})
                display_name = info.get('display_name', self._format_concept_name(concept_name))
                order = info.get('order', 50)
                unit = concept_units.get(concept_name, 'USD')
                metric_key = f"{category}_{concept_name}"

                values = {}
                for period in limited_periods:
                    value = None
                    for fact in financial_data[category][tag]:
                        if fact['end'] == period:
                            value = fact['val']
                            break
                    values[period] = value

                # Skip metrics where ALL periods are None
                if all(v is None for v in values.values()):
                    continue

                # Skip metrics with data in less than half the periods (sparse/discontinued)
                non_null = sum(1 for v in values.values() if v is not None)
                if non_null < len(limited_periods) / 2:
                    continue

                source = info.get('source', 'sec')

                normalized_data['metrics'][metric_key] = {
                    'values': values,
                    'category': category,
                    'tag': tag,
                    'display_name': display_name,
                    'order': order,
                    'unit': unit,
                    'source': source,
                    'indent': info.get('indent', 0),
                    'is_subtotal': info.get('is_subtotal', False),
                    'section': info.get('section', ''),
                }

        normalized_data['metadata'] = {
            'fiscal_month': fiscal_month,
            'period_type': period_type,
        }

        return normalized_data

    def _format_concept_name(self, concept_name):
        """Fallback: convert CamelCase to human-readable label."""
        formatted = re.sub(r'([a-z])([A-Z])', r'\1 \2', concept_name)
        formatted = re.sub(r'([A-Z])([A-Z][a-z])', r'\1 \2', formatted)
        return formatted

    def _detect_fiscal_year_end(self, all_periods):
        """Detect fiscal year end month from period end dates."""
        months = []
        for period in all_periods:
            try:
                month = period.split('-')[1]
                if month.isdigit() and 1 <= int(month) <= 12:
                    months.append(month)
            except (IndexError, ValueError):
                continue

        month_counter = Counter(months)
        most_common = month_counter.most_common(1)
        return most_common[0][0] if most_common else '12'

    def augment_with_extensions(self, facts_data, filing_retrieval, cik,
                                filings, rules):
        """Merge company-extension XBRL facts into a Company Facts JSON blob.

        For each provided filing, this:
          1. Downloads the filing's standalone XBRL instance doc (`*_htm.xml`).
          2. Parses it for consolidated facts (skips dimensional breakdowns).
          3. Applies extension rules to map issuer-specific concepts
             (e.g. `abg:FloorPlanNotesPayableTrade`) to canonical names
             (`FloorPlanNotesPayable`).
          4. Aggregates per (canonical, period_end) — splits get summed.
          5. Injects the aggregated facts under a synthetic `ext` taxonomy
             in the same Company-Facts-JSON shape, so the existing
             `parse_company_facts` pipeline picks them up unchanged.

        Returns the augmented `facts_data` (mutated in place). Failures
        on individual filings log a warning and are skipped — partial
        coverage is better than none.

        Args:
            facts_data: Output of FilingRetrieval.get_company_facts()
            filing_retrieval: FilingRetrieval instance
            cik: Company CIK
            filings: list of filing-metadata dicts (from get_filing_metadata)
            rules: list of ExtensionRule (e.g. DEALER_RULES)
        """
        if not facts_data or 'facts' not in facts_data:
            return facts_data

        # Collect (canonical, period_end, period_type) -> aggregated fact dict
        # across all filings. Older filings can fill periods missing from
        # newer ones (5-year cash flow tables, etc.).
        combined: dict[tuple[str, str, str], dict] = {}

        for filing in filings:
            acc = filing.get('accession_number')
            filed = filing.get('filing_date')
            if not acc:
                continue
            try:
                xml = filing_retrieval.get_filing_instance_xml(cik, acc)
                if not xml:
                    continue
                facts = parse_instance(xml)
                # apply_rules already aggregates within one filing; we then
                # merge across filings, preferring the latest filed value.
                per_filing = apply_rules(facts, rules)
                for key, agg in per_filing.items():
                    agg = dict(agg)  # shallow copy so we can add 'filed'/'accn'
                    agg['filed'] = filed
                    agg['accn'] = acc
                    existing = combined.get(key)
                    if existing is None or (
                        filed and (existing.get('filed') or '') < filed
                    ):
                        combined[key] = agg
            except Exception as e:
                logger.warning(
                    f"Skipping extensions from filing {acc}: {e.__class__.__name__}: {e}"
                )
                continue

        if not combined:
            return facts_data

        # Group by canonical concept → list of fact dicts in Company-Facts API shape
        per_concept: dict[str, list[dict]] = defaultdict(list)
        per_concept_meta: dict[str, dict] = {}
        for (canonical, period_end, period_type), agg in combined.items():
            fact_dict = {
                'val': agg['value'],
                'end': period_end,
                'filed': agg.get('filed'),
                'accn': agg.get('accn'),
            }
            if period_type == 'duration':
                fact_dict['start'] = agg['period_start']
            per_concept[canonical].append(fact_dict)
            if canonical not in per_concept_meta:
                per_concept_meta[canonical] = {
                    'category': agg['category'],
                    'unit': agg['unit'],
                    'source_concepts': set(agg.get('source_concepts', [])),
                }
            else:
                per_concept_meta[canonical]['source_concepts'].update(
                    agg.get('source_concepts', [])
                )

        # Inject under synthetic 'ext' taxonomy in Company-Facts JSON shape
        ext_block = facts_data['facts'].setdefault('ext', {})
        for canonical, fact_list in per_concept.items():
            unit = per_concept_meta[canonical]['unit']
            src = sorted(per_concept_meta[canonical]['source_concepts'])
            ext_block[canonical] = {
                'label': canonical,
                'description': f"Aggregated from extension concepts: {', '.join(src)}",
                'units': {unit: fact_list},
            }

        logger.info(
            f"Injected {len(per_concept)} canonical extension concepts "
            f"({sum(len(v) for v in per_concept.values())} facts) "
            f"from {len(filings)} filings"
        )
        return facts_data

    def normalize_financial_data(self, financial_data, period_type='annual', num_periods=1):
        """Normalize financial data (compatibility method)."""
        all_periods = []
        found_concepts = set()

        for category in financial_data:
            for tag in financial_data[category]:
                concept_name = tag.split(':')[-1]
                found_concepts.add(concept_name)
                for fact in financial_data[category][tag]:
                    if 'end' in fact:
                        all_periods.append(fact['end'])

        classifications = self.classifier.classify_tags(list(found_concepts))
        return self._normalize_api_data(financial_data, period_type, all_periods, num_periods, classifications)
