"""
Data formatter for presenting financial statement data.
"""

import os
import csv
import json
import logging
import webbrowser
from datetime import datetime
from tabulate import tabulate
import pandas as pd

from config.settings import DEFAULT_OUTPUT_FORMAT, DEFAULT_OUTPUT_DIR, TERMINAL_WIDTH
from utils.helpers import format_financial_number

# Initialize logger
logger = logging.getLogger(__name__)


class DataFormatter:
    """
    Formatter for financial statement data.
    """
    
    def __init__(self, output_format=DEFAULT_OUTPUT_FORMAT):
        """
        Initialize the data formatter.
        
        Args:
            output_format (str): Format to output data in ('csv', 'json', 'excel', 'console')
        """
        self.output_format = output_format.lower()
    
    def _get_statement_title(self, statement_type):
        """
        Get a human-readable title for a financial statement.
        
        Args:
            statement_type (str): Type of financial statement
            
        Returns:
            str: Human-readable title
        """
        statement_type = statement_type.upper()
        
        if statement_type == 'BS':
            return "Balance Sheet"
        elif statement_type == 'IS':
            return "Income Statement"
        elif statement_type == 'CF':
            return "Cash Flow Statement"
        elif statement_type == 'EQ':
            return "Statement of Stockholders' Equity"
        elif statement_type == 'CI':
            return "Statement of Comprehensive Income"
        elif statement_type == 'ALL':
            return "Financial Statements"
        else:
            return f"Financial Statement ({statement_type})"
    
    def _format_period_header(self, period, metadata=None):
        """
        Format a period date into a readable header.
        
        Args:
            period (str): Period date in ISO format (YYYY-MM-DD)
            metadata (dict): Optional metadata about the fiscal year
            
        Returns:
            str: Formatted period header
        """
        try:
            date_obj = datetime.strptime(period, "%Y-%m-%d")
            
            # If metadata is provided, check if this is a fiscal year end
            if metadata and 'fiscal_month' in metadata:
                fiscal_month = metadata['fiscal_month']
                period_type = metadata.get('period_type', 'annual')
                
                if period.split('-')[1] == fiscal_month:
                    # This is a fiscal year end date
                    if period_type == 'annual' or period_type == 'ytd':
                        return f"FY {date_obj.year}"
                    else:
                        # For quarterly periods
                        return f"Q{(date_obj.month % 3) or 4} {date_obj.year}"
                else:
                    # Not a fiscal year end - use the quarter if this is quarterly data
                    if period_type == 'quarterly':
                        return f"Q{(date_obj.month % 3) or 4} {date_obj.year}"
            
            # Default formatting - use FY for annual data
            if metadata and metadata.get('period_type') == 'annual':
                return f"FY {date_obj.year}"
                
            # Default formatting
            return date_obj.strftime("%b %d, %Y")
        except ValueError:
            # If date parsing fails, return the original
            return period
    
    def _create_dataframe(self, data):
        """
        Create a DataFrame from normalized financial data.
        
        Args:
            data (dict): Normalized financial statement data
            
        Returns:
            pandas.DataFrame: DataFrame with financial data
        """
        # Prepare data for DataFrame
        df_data = []
        
        # Format period headers
        metadata = data.get('metadata', {})
        formatted_periods = {period: self._format_period_header(period, metadata) 
                            for period in data['periods']}
        
        # Define proper accounting order for categories
        accounting_order = {
            'Assets': 0,
            'Liabilities': 1,
            'Equity': 2,
            'Revenue': 3,
            'Income': 4,
            'EPS': 5,
            'OCI': 6,
            'OperatingCashFlow': 7,
            'InvestingCashFlow': 8,
            'FinancingCashFlow': 9,
        }
        
        # Group metrics by category for better organization
        metrics_by_category = {}
        for metric_key, metric_data in data['metrics'].items():
            category = metric_data['category']
            if category not in metrics_by_category:
                metrics_by_category[category] = []
            metrics_by_category[category].append((metric_key, metric_data))
        
        # Use accounting_order to sort categories
        sorted_categories = sorted(metrics_by_category.keys(), 
                                  key=lambda x: accounting_order.get(x, 99))
        
        # Add each metric to the DataFrame data
        for category in sorted_categories:
            # Add category header
            df_data.append({
                '_metric_key': '',
                '_source': 'header',
                'Metric': f"--- {category} ---",
                **{formatted_periods[period]: "" for period in data['periods']}
            })

            # Sort metrics in this category by their order value
            sorted_metrics = sorted(metrics_by_category[category],
                                   key=lambda x: x[1].get('order', 50))

            # Add metrics in this category
            for metric_key, metric_data in sorted_metrics:
                # Use the display name if available, otherwise use the metric key
                display_name = metric_data.get('display_name')
                if not display_name:
                    # Clean up metric name for display
                    display_name = metric_key.split('_', 1)[1]
                    display_name = ' '.join(word.capitalize() for word in display_name.split())

                row_data = {
                    '_metric_key': metric_key,
                    '_source': metric_data.get('source', 'sec'),
                    'Metric': display_name,
                }

                for period in data['periods']:
                    value = metric_data['values'].get(period)
                    row_data[formatted_periods[period]] = value

                df_data.append(row_data)

        # Create DataFrame
        return pd.DataFrame(df_data)
    
    def _format_dataframe(self, df, data=None):
        """
        Apply formatting to the DataFrame.

        Args:
            df (pandas.DataFrame): DataFrame with financial data
            data (dict): Original normalized data with metric info

        Returns:
            pandas.DataFrame: Formatted DataFrame
        """
        # Build a lookup from unique metric_key -> unit so per-row formatting
        # is unambiguous (display_name collides between tags, e.g. "Basic").
        unit_lookup = {}
        if data and 'metrics' in data:
            for metric_key, metric_data in data['metrics'].items():
                unit_lookup[metric_key] = metric_data.get('unit', 'USD')

        # Create a copy to avoid modifying the original
        formatted_df = df.copy()

        has_key = '_metric_key' in formatted_df.columns
        # `_source` is kept on the df so the console formatter can split rows;
        # non-console output methods drop it just before write.
        skip_cols = {'Metric', '_metric_key', '_source'}

        # Format numeric columns
        for col in formatted_df.columns:
            if col in skip_cols:
                continue
            for idx in formatted_df.index:
                val = formatted_df.at[idx, col]
                if pd.notnull(val) and not isinstance(val, str):
                    metric_key = formatted_df.at[idx, '_metric_key'] if has_key else ''
                    unit = unit_lookup.get(metric_key, 'USD')
                    if unit == 'pure':
                        # Effective tax rate etc. — render as percentage.
                        formatted_df.at[idx, col] = f"{val * 100:.1f}%"
                    elif unit == 'USD/shares':
                        formatted_df.at[idx, col] = format_financial_number(val, decimals=2, use_scaling=False)
                    elif unit == 'shares':
                        formatted_df.at[idx, col] = format_financial_number(val, decimals=0, use_scaling=False)
                    else:
                        formatted_df.at[idx, col] = format_financial_number(val, decimals=0, use_scaling=False)

        # Drop only the per-row metric_key helper here; `_source` flows on.
        if has_key:
            formatted_df = formatted_df.drop(columns=['_metric_key'])

        return formatted_df
    
    def format_statement(self, data, statement_type, company_name=None, output_file=None):
        """
        Format financial statement data.
        
        Args:
            data (dict): Normalized financial statement data
            statement_type (str): Type of financial statement
            company_name (str): Name of the company
            output_file (str): Path to output file
            
        Returns:
            str: Path to the output file or formatted data for console
        """
        if not data or not data.get('periods') or not data.get('metrics'):
            logger.warning("No data to format")
            return None
        
        statement_title = self._get_statement_title(statement_type)
        
        # Log information about the periods being formatted
        period_info = ", ".join([self._format_period_header(p, data.get('metadata')) for p in data['periods']])
        logger.info(f"Formatting {statement_title} for periods: {period_info}")
        
        # Create a DataFrame from the data
        df = self._create_dataframe(data)
        
        # Apply formatting
        formatted_df = self._format_dataframe(df, data)
        
        # Add company name if not already present
        if company_name and 'Company' not in formatted_df.columns:
            formatted_df.insert(0, 'Company', company_name)
        
        # Add balance sheet reconciliation if this is a balance sheet
        if statement_type.upper() == 'BS':
            formatted_df = self._add_balance_sheet_reconciliation(formatted_df, data)
        
        # Output based on format
        if self.output_format == 'csv':
            return self._output_csv(formatted_df, statement_type, company_name, output_file)
        elif self.output_format == 'json':
            return self._output_json(formatted_df, statement_type, company_name, output_file)
        elif self.output_format == 'excel':
            return self._output_excel(formatted_df, statement_type, company_name, output_file, data)
        elif self.output_format == 'html':
            return self._output_html(formatted_df, statement_type, company_name, output_file, data)
        else:  # console
            return self._format_for_console(formatted_df, statement_title, company_name)
    
    def _output_csv(self, df, statement_type, company_name=None, output_file=None):
        """
        Output data as CSV file.
        
        Args:
            df (pandas.DataFrame): DataFrame to output
            statement_type (str): Type of financial statement
            company_name (str): Name of the company
            output_file (str): Path to output file
            
        Returns:
            str: Path to the output file
        """
        # Generate output filename if not provided
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            company_slug = company_name.replace(" ", "_").lower() if company_name else "company"
            statement_slug = statement_type.lower()
            
            filename = f"{company_slug}_{statement_slug}_{timestamp}.csv"
            output_file = os.path.join(DEFAULT_OUTPUT_DIR, filename)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        if '_source' in df.columns:
            df = df.drop(columns=['_source'])

        # Write to CSV
        df.to_csv(output_file, index=False, quoting=csv.QUOTE_NONNUMERIC)
        
        logger.info(f"CSV output saved to {output_file}")
        return output_file
    
    def _output_json(self, df, statement_type, company_name=None, output_file=None):
        """
        Output data as JSON file.
        
        Args:
            df (pandas.DataFrame): DataFrame to output
            statement_type (str): Type of financial statement
            company_name (str): Name of the company
            output_file (str): Path to output file
            
        Returns:
            str: Path to the output file
        """
        # Generate output filename if not provided
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            company_slug = company_name.replace(" ", "_").lower() if company_name else "company"
            statement_slug = statement_type.lower()
            
            filename = f"{company_slug}_{statement_slug}_{timestamp}.json"
            output_file = os.path.join(DEFAULT_OUTPUT_DIR, filename)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        if '_source' in df.columns:
            df = df.drop(columns=['_source'])

        # Convert DataFrame to JSON
        json_data = {
            "metadata": {
                "company": company_name,
                "statement_type": self._get_statement_title(statement_type),
                "generated_at": datetime.now().isoformat(),
                "fiscal_periods": [col for col in df.columns if col not in ['Company', 'Metric']]
            },
            "data": json.loads(df.to_json(orient="records"))
        }
        
        # Write to JSON file
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"JSON output saved to {output_file}")
        return output_file
    
    def _output_excel(self, df, statement_type, company_name=None, output_file=None, data=None):
        """
        Output data as Excel file.
        
        Args:
            df (pandas.DataFrame): DataFrame to output
            statement_type (str): Type of financial statement
            company_name (str): Name of the company
            output_file (str): Path to output file
            data (dict): Original normalized data
            
        Returns:
            str: Path to the output file
        """
        # Generate output filename if not provided
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            company_slug = company_name.replace(" ", "_").lower() if company_name else "company"
            statement_slug = statement_type.lower()
            
            filename = f"{company_slug}_{statement_slug}_{timestamp}.xlsx"
            output_file = os.path.join(DEFAULT_OUTPUT_DIR, filename)
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        if '_source' in df.columns:
            df = df.drop(columns=['_source'])

        # Write to Excel
        with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
            # Write DataFrame to Excel
            df.to_excel(writer, sheet_name=self._get_statement_title(statement_type), index=False)
            
            # Access the workbook and worksheet
            workbook = writer.book
            worksheet = writer.sheets[self._get_statement_title(statement_type)]
            
            # Add title
            title_format = workbook.add_format({
                'bold': True,
                'font_size': 14,
                'align': 'center'
            })
            
            statement_title = self._get_statement_title(statement_type)
            if company_name:
                title = f"{company_name} - {statement_title}"
            else:
                title = statement_title
                
            worksheet.merge_range(0, 0, 0, len(df.columns) - 1, title, title_format)
            
            # Add fiscal year info if available
            if data and 'metadata' in data and 'fiscal_month' in data['metadata']:
                fiscal_month = int(data['metadata']['fiscal_month'])
                month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                               'July', 'August', 'September', 'October', 'November', 'December']
                fiscal_note = f"Note: Company uses a fiscal year ending in {month_names[fiscal_month-1]}"
                
                note_format = workbook.add_format({
                    'italic': True,
                    'font_size': 10,
                    'align': 'center'
                })
                
                worksheet.merge_range(1, 0, 1, len(df.columns) - 1, fiscal_note, note_format)
                
                # Adjust the starting row for the data
                df.to_excel(writer, sheet_name=self._get_statement_title(statement_type), 
                            startrow=2, index=False)
                
                # Adjust header row for formats below
                header_row = 2
            else:
                # Move data down one row to make space for the title
                df.to_excel(writer, sheet_name=self._get_statement_title(statement_type), 
                            startrow=1, index=False)
                
                # Standard header row
                header_row = 1
            
            # Format headers
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'top',
                'border': 1
            })
            
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(header_row, col_num, value, header_format)
            
            # Format category rows
            category_format = workbook.add_format({
                'bold': True,
                'italic': True,
                'bg_color': '#E0E0E0'
            })
            
            # Format for financial data cells
            number_format = workbook.add_format({
                'num_format': '#,##0.00_);(#,##0.00)',
                'align': 'right'
            })
            
            # Apply formatting to rows
            for row_num, row_data in enumerate(df.values, start=header_row+1):
                for col_num, cell_value in enumerate(row_data):
                    # Skip company name column
                    if col_num == 0 and 'Company' in df.columns:
                        continue
                    
                    # Skip metric name column
                    metric_col = 0 if 'Company' not in df.columns else 1
                    if col_num == metric_col:
                        # Check if this is a category row
                        if isinstance(cell_value, str) and cell_value.startswith('---'):
                            for i in range(len(row_data)):
                                worksheet.write(row_num, i, row_data[i], category_format)
                            break
                        continue
                    
                    # Format numbers in other columns
                    if isinstance(cell_value, (int, float)) or (isinstance(cell_value, str) and 
                                                              any(c.isdigit() for c in cell_value)):
                        worksheet.write(row_num, col_num, cell_value, number_format)
            
            # Auto-adjust column widths
            for i, col in enumerate(df.columns):
                # Calculate the maximum length
                max_len = max(df[col].astype(str).apply(len).max(),
                              len(str(col)))
                # Set width with some extra padding
                worksheet.set_column(i, i, max_len + 2)
        
        logger.info(f"Excel output saved to {output_file}")
        return output_file
    
    def _output_html(self, df, statement_type, company_name=None, output_file=None, data=None):
        """
        Output data as a styled HTML report and open it in the browser.
        Separates core (builtin) metrics from supplementary (SEC-mapped) ones.
        """
        if not output_file:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            company_slug = company_name.replace(" ", "_").lower() if company_name else "company"
            statement_slug = statement_type.lower()
            filename = f"{company_slug}_{statement_slug}_{timestamp}.html"
            output_file = os.path.join(DEFAULT_OUTPUT_DIR, filename)

        os.makedirs(os.path.dirname(output_file), exist_ok=True)

        statement_title = self._get_statement_title(statement_type)
        title = f"{company_name} - {statement_title}" if company_name else statement_title

        # Fiscal year note
        fiscal_note = ""
        if data and 'metadata' in data and 'fiscal_month' in data['metadata']:
            fiscal_month = int(data['metadata']['fiscal_month'])
            month_names = ['January', 'February', 'March', 'April', 'May', 'June',
                           'July', 'August', 'September', 'October', 'November', 'December']
            fiscal_note = f"Fiscal year ends in {month_names[fiscal_month - 1]}"

        period_type_label = ""
        if data and 'metadata' in data:
            pt = data['metadata'].get('period_type', '')
            period_type_label = pt.capitalize() if pt else ""

        # Build HTML rows directly from data dict (preserves source info)
        metadata = data.get('metadata', {}) if data else {}
        periods = data.get('periods', []) if data else []
        formatted_periods = {p: self._format_period_header(p, metadata) for p in periods}
        period_labels = [formatted_periods[p] for p in periods]

        accounting_order = {
            'Assets': 0, 'Liabilities': 1, 'Equity': 2,
            'Revenue': 3, 'Income': 4, 'EPS': 5,
            'OperatingCashFlow': 6, 'InvestingCashFlow': 7, 'FinancingCashFlow': 8,
        }

        # Split metrics into core (builtin) and supplementary (sec-mapped)
        core_by_cat = {}
        supp_by_cat = {}
        for metric_key, metric_data in (data.get('metrics', {}) if data else {}).items():
            cat = metric_data['category']
            source = metric_data.get('source', 'sec')
            target = core_by_cat if source == 'builtin' else supp_by_cat
            if cat not in target:
                target[cat] = []
            target[cat].append(metric_data)

        def _format_val(val, unit):
            """Format a single value. USD amounts shown in millions."""
            if val is None:
                return None, ''
            if unit in ('USD/shares', 'pure'):
                return val, format_financial_number(val, decimals=2, use_scaling=False)
            elif unit == 'shares':
                # Shares shown in thousands
                val_k = val / 1_000
                return val, format_financial_number(val_k, decimals=0, use_scaling=False)
            else:
                # USD amounts: show in millions
                val_m = val / 1_000_000
                return val, format_financial_number(val_m, decimals=0, use_scaling=False)

        def _build_rows(metrics_by_cat, num_periods, is_statement_type=None):
            rows = []
            # Flatten all metrics with their full ordering, then sort globally
            all_metrics = []
            for cat in metrics_by_cat:
                for m in metrics_by_cat[cat]:
                    # Global sort key: category order * 1000 + metric order
                    cat_ord = accounting_order.get(cat, 99)
                    m_ord = m.get('order', 50)
                    all_metrics.append((cat_ord * 1000 + m_ord, cat, m))

            all_metrics.sort(key=lambda x: x[0])

            # For IS/BS: no category headers, use indent/subtotal/section instead
            # For CF: keep category headers
            use_hierarchy = is_statement_type in ('IS', 'BS')
            seen_cats = set()
            seen_sections = set()

            for _, cat, m in all_metrics:
                name = m.get('display_name', '')
                unit = m.get('unit', 'USD')
                indent = m.get('indent', 0)
                is_subtotal = m.get('is_subtotal', False)
                section = m.get('section', '')

                if use_hierarchy:
                    # Insert section headers (e.g., "Earnings per share:")
                    if section and section not in seen_sections:
                        seen_sections.add(section)
                        rows.append(
                            f'<tr class="section-row"><td colspan="{num_periods + 1}">{section}</td></tr>'
                        )
                else:
                    # Category headers for BS/CF
                    if cat not in seen_cats:
                        seen_cats.add(cat)
                        rows.append(
                            f'<tr class="category-row"><td colspan="{num_periods + 1}">{cat}</td></tr>'
                        )

                # Build value cells
                indent_px = indent * 24
                subtotal_cls = " subtotal" if is_subtotal else ""
                indent_style = f' style="padding-left:{16 + indent_px}px"' if indent > 0 else ""
                cells = [f'<td class="metric-name{subtotal_cls}"{indent_style}>{name}</td>']
                for p in periods:
                    raw, formatted = _format_val(m['values'].get(p), unit)
                    if raw is None:
                        cells.append('<td class="value na">&mdash;</td>')
                    else:
                        css = f"value{subtotal_cls}"
                        if str(formatted).startswith('-'):
                            # Accounting style: (1,234) instead of -1,234
                            formatted = f"({formatted[1:]})"
                            css += " negative"
                        cells.append(f'<td class="{css}">{formatted}</td>')
                rows.append(f'<tr>{"".join(cells)}</tr>')
            return rows

        core_rows = _build_rows(core_by_cat, len(periods), statement_type)
        supp_rows = _build_rows(supp_by_cat, len(periods), statement_type)

        # BS reconciliation: Assets = Liabilities + Equity
        recon_html = ""
        if statement_type.upper() == 'BS' and data:
            recon_html = self._build_bs_reconciliation(data, periods, period_labels)

        # Period headers
        period_headers = ''.join(f'<th class="period">{lbl}</th>' for lbl in period_labels)

        generated_at = datetime.now().strftime("%B %d, %Y at %I:%M %p")
        supp_count = sum(len(v) for v in supp_by_cat.values())

        supp_section = ""
        if supp_rows:
            supp_section = f"""
  <details class="supplementary">
    <summary>Additional Details ({supp_count} items from SEC filings)</summary>
    <table>
      <thead>
        <tr>
          <th>Metric</th>
          {period_headers}
        </tr>
      </thead>
      <tbody>
        {"".join(supp_rows)}
      </tbody>
    </table>
  </details>"""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<style>
  :root {{
    --bg: #f8f9fa;
    --card: #ffffff;
    --border: #dee2e6;
    --text: #212529;
    --muted: #6c757d;
    --cat-bg: #e9ecef;
    --hover: #f1f3f5;
    --accent: #0d6efd;
    --negative: #dc3545;
    --na: #adb5bd;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 2rem;
  }}
  .container {{
    max-width: 1200px;
    margin: 0 auto;
  }}
  .header {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 1.5rem 2rem;
    margin-bottom: 1.5rem;
  }}
  .header h1 {{
    font-size: 1.5rem;
    font-weight: 600;
    margin-bottom: 0.25rem;
  }}
  .header .subtitle {{
    color: var(--muted);
    font-size: 0.9rem;
  }}
  .header .badges {{
    margin-top: 0.75rem;
    display: flex;
    gap: 0.5rem;
    flex-wrap: wrap;
  }}
  .badge {{
    display: inline-block;
    padding: 0.25rem 0.6rem;
    border-radius: 4px;
    font-size: 0.78rem;
    font-weight: 500;
    background: #e7f1ff;
    color: #0a58ca;
  }}
  .badge.fiscal {{
    background: #fff3cd;
    color: #664d03;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    font-size: 0.88rem;
  }}
  thead th {{
    background: #343a40;
    color: #fff;
    padding: 0.75rem 1rem;
    text-align: left;
    font-weight: 600;
    position: sticky;
    top: 0;
  }}
  th.period {{
    text-align: right;
    min-width: 130px;
  }}
  .category-row td {{
    background: var(--cat-bg);
    font-weight: 700;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    padding: 0.6rem 1rem;
    border-top: 2px solid var(--border);
  }}
  tbody tr:not(.category-row):hover {{
    background: var(--hover);
  }}
  td {{
    padding: 0.5rem 1rem;
    border-bottom: 1px solid #f0f0f0;
  }}
  .metric-name {{
    font-weight: 500;
  }}
  .value {{
    text-align: right;
    font-variant-numeric: tabular-nums;
    font-family: 'SF Mono', 'Cascadia Code', 'Consolas', monospace;
  }}
  .value.negative {{
    color: var(--negative);
  }}
  .value.na {{
    color: var(--na);
    font-style: italic;
    font-family: inherit;
  }}
  .subtotal {{
    font-weight: 700;
  }}
  tr:has(.subtotal) {{
    border-top: 1px solid #999;
  }}
  .section-row td {{
    padding: 0.7rem 1rem 0.2rem;
    font-weight: 600;
    font-size: 0.85rem;
    color: var(--muted);
    border-bottom: none;
  }}
  .supplementary {{
    margin-top: 1.5rem;
  }}
  .supplementary summary {{
    cursor: pointer;
    padding: 0.75rem 1rem;
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 8px;
    font-weight: 600;
    font-size: 0.9rem;
    color: var(--muted);
    margin-bottom: 0.5rem;
  }}
  .supplementary summary:hover {{
    color: var(--text);
  }}
  .supplementary table {{
    opacity: 0.85;
  }}
  .footer {{
    margin-top: 1rem;
    text-align: center;
    font-size: 0.78rem;
    color: var(--muted);
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h1>{title}</h1>
    <div class="subtitle">SEC EDGAR Financial Data &middot; In millions, except per share amounts and shares in thousands</div>
    <div class="badges">
      <span class="badge">{statement_title}</span>
      {"<span class='badge'>" + period_type_label + "</span>" if period_type_label else ""}
      {"<span class='badge fiscal'>" + fiscal_note + "</span>" if fiscal_note else ""}
    </div>
  </div>
  <table>
    <thead>
      <tr>
        <th>Metric</th>
        {period_headers}
      </tr>
    </thead>
    <tbody>
      {"".join(core_rows)}
    </tbody>
  </table>
  {supp_section}
  {recon_html}
  <div class="footer">
    Generated on {generated_at} &middot; Source: SEC EDGAR
  </div>
</div>
</body>
</html>"""

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)

        logger.info(f"HTML output saved to {output_file}")

        # Open in browser (only when running standalone, not from web server)
        if not os.environ.get('EDGAR_WEB_MODE'):
            try:
                webbrowser.open(f'file:///{os.path.abspath(output_file).replace(os.sep, "/")}')
            except Exception:
                pass

        return output_file

    def _build_bs_reconciliation(self, data, periods, period_labels):
        """Build an HTML reconciliation check: Assets = Liabilities + Equity."""
        metrics = data.get('metrics', {})

        # Find raw values — check both core and supplementary metrics
        totals = {'assets': {}, 'liabilities': {}, 'equity': {}, 'l_and_se': {}}
        # Priority: builtin=2, sec=1 (higher wins)
        prio = {'assets': 0, 'liabilities': 0, 'equity': 0, 'l_and_se': 0}

        for m in metrics.values():
            dn = m.get('display_name', '').lower()
            tag = m.get('tag', '').lower()
            unit = m.get('unit', 'USD')
            src = 2 if m.get('source') == 'builtin' else 1
            if unit != 'USD':
                continue
            if dn == 'total assets' and src > prio['assets']:
                totals['assets'] = m['values']
                prio['assets'] = src
            elif dn == 'total liabilities' and src > prio['liabilities']:
                totals['liabilities'] = m['values']
                prio['liabilities'] = src
            elif 'liabilitiesandstockholdersequity' in tag.replace(':', '').replace('-', ''):
                if src > prio['l_and_se']:
                    totals['l_and_se'] = m['values']
                    prio['l_and_se'] = src
            elif dn == "total equity" and src * 10 + 2 > prio['equity']:
                totals['equity'] = m['values']
                prio['equity'] = src * 10 + 2  # prefer builtin + "total equity" over "shareholders' equity"
            elif dn == "total shareholders' equity" and src * 10 + 1 > prio['equity']:
                totals['equity'] = m['values']
                prio['equity'] = src * 10 + 1

        if not totals['assets'] or not (totals['liabilities'] or totals['equity']):
            return ""

        def _fmt(val):
            if val is None:
                return '&mdash;', ''
            v_m = val / 1_000_000
            formatted = format_financial_number(v_m, decimals=0, use_scaling=False)
            if formatted.startswith('-'):
                return f"({formatted[1:]})", ' class="negative"'
            return formatted, ''

        header_cells = ''.join(f'<th class="period">{lbl}</th>' for lbl in period_labels)

        rows = []

        # Row: Total Assets
        cells = '<td class="metric-name subtotal">Total assets (A)</td>'
        for p in periods:
            f, cls = _fmt(totals['assets'].get(p))
            cells += f'<td class="value subtotal"{cls}>{f}</td>'
        rows.append(f'<tr>{cells}</tr>')

        # Row: Total Liabilities
        cells = '<td class="metric-name">Total liabilities (B)</td>'
        for p in periods:
            f, cls = _fmt(totals['liabilities'].get(p))
            cells += f'<td class="value"{cls}>{f}</td>'
        rows.append(f'<tr>{cells}</tr>')

        # Row: Total Equity
        equity_label = "Total equity (C)" if prio['equity'] % 10 == 2 else "Total shareholders' equity (C)"
        cells = f'<td class="metric-name">{equity_label}</td>'
        for p in periods:
            f, cls = _fmt(totals['equity'].get(p))
            cells += f'<td class="value"{cls}>{f}</td>'
        rows.append(f'<tr>{cells}</tr>')

        # Compute gap: A - L - E
        gaps = {}
        for p in periods:
            a = totals['assets'].get(p)
            l = totals['liabilities'].get(p)
            e = totals['equity'].get(p)
            if a is not None and l is not None and e is not None:
                gaps[p] = a - l - e
            else:
                gaps[p] = None

        # If there's a gap, show it as "Other (redeemable equity, NCI, etc.)"
        has_gap = any(g is not None and abs(g) > 0.5 for g in gaps.values())
        if has_gap:
            cells = '<td class="metric-name" style="font-style:italic;color:#6c757d">Other items (redeemable/temporary equity)</td>'
            for p in periods:
                f, cls = _fmt(gaps[p])
                cells += f'<td class="value" style="font-style:italic;color:#6c757d"{cls}>{f}</td>'
            rows.append(f'<tr>{cells}</tr>')

        # Row: L + E + Other  (should equal A)
        cells = '<td class="metric-name subtotal">Liabilities + Equity + Other (B + C + D)</td>' if has_gap else '<td class="metric-name subtotal">Liabilities + Equity (B + C)</td>'
        for p in periods:
            l = totals['liabilities'].get(p)
            e = totals['equity'].get(p)
            g = gaps.get(p, 0) or 0
            if l is not None and e is not None:
                f, cls = _fmt(l + e + g)
            else:
                f, cls = '&mdash;', ''
            cells += f'<td class="value subtotal"{cls}>{f}</td>'
        rows.append(f'<tr>{cells}</tr>')

        # Row: Final difference (should be 0 now)
        cells = '<td class="metric-name">Difference</td>'
        all_zero = True
        for p in periods:
            a = totals['assets'].get(p)
            l = totals['liabilities'].get(p)
            e = totals['equity'].get(p)
            g = gaps.get(p, 0) or 0
            if a is not None and l is not None and e is not None:
                diff = a - l - e - g  # always 0 by construction
                # Cross-check with LiabilitiesAndStockholdersEquity if available
                l_se = totals['l_and_se'].get(p)
                if l_se is not None:
                    diff = a - l_se
                if abs(diff) > 0.5:
                    all_zero = False
                f, cls = _fmt(diff)
            else:
                f, cls = '&mdash;', ''
                all_zero = False
            cells += f'<td class="value"{cls}>{f}</td>'
        rows.append(f'<tr>{cells}</tr>')

        if all_zero:
            status = '<span style="color:#198754;font-weight:600">Balanced</span>'
        else:
            status = '<span style="color:#dc3545;font-weight:600">Imbalanced</span>'

        return f"""
  <div class="recon" style="margin-top:1.5rem">
    <div style="font-weight:600;font-size:0.9rem;margin-bottom:0.5rem;color:#6c757d">
      Reconciliation Check: Assets = Liabilities + Equity &nbsp; {status}
    </div>
    <table>
      <thead><tr><th>Item</th>{header_cells}</tr></thead>
      <tbody>{"".join(rows)}</tbody>
    </table>
  </div>"""

    def _format_for_console(self, df, title, company_name=None):
        """
        Format data for console output.

        Splits the table into a core (builtin overrides) section and a
        supplementary (SEC-mapped) section so the curated lines stay
        above the auto-derived noise. Falls back to a single table if
        no ``_source`` column is present.

        Args:
            df (pandas.DataFrame): DataFrame to output
            title (str): Statement title
            company_name (str): Name of the company

        Returns:
            str: Formatted string for console output
        """
        # Create a header
        if company_name:
            header = f"{company_name} - {title}"
        else:
            header = title

        # Format header with borders
        header_border = "=" * min(len(header) + 4, TERMINAL_WIDTH)
        formatted_header = f"\n{header_border}\n  {header}  \n{header_border}\n"

        display_df = df.copy()

        # Replace NaN/None numeric cells with an empty string so tabulate
        # doesn't render literal "nan" for periods a metric didn't report.
        skip_cols = {'Metric', '_metric_key', '_source'}
        for col in display_df.columns:
            if col in skip_cols:
                continue
            display_df[col] = display_df[col].where(display_df[col].notna(), '')

        # Replace category marker rows with prettier upper-cased labels.
        for i, row in display_df.iterrows():
            metric_val = row['Metric']
            if isinstance(metric_val, str) and metric_val.startswith('---'):
                category_name = metric_val.strip('- ')
                display_df.at[i, 'Metric'] = f"{category_name.upper()}:"

        if '_source' not in display_df.columns:
            # No source info — emit a single table.
            table = tabulate(display_df, headers="keys", tablefmt="grid", showindex=False)
            return f"{formatted_header}\n{table}"

        # Walk rows, partitioning into core/supplementary. A category header
        # is tracked as the "current" category and emitted once per section
        # when its first member arrives, so the same category can lead both
        # the core block and the supplementary block.
        core_rows: list[dict] = []
        supp_rows: list[dict] = []
        current_header: dict | None = None
        core_header_emitted: set[str] = set()
        supp_header_emitted: set[str] = set()

        for _, row in display_df.iterrows():
            src = row['_source']
            row_dict = row.drop(labels=['_source']).to_dict()
            if src == 'header':
                current_header = row_dict
                continue
            target = core_rows if src == 'builtin' else supp_rows
            seen = core_header_emitted if src == 'builtin' else supp_header_emitted
            if current_header is not None:
                hdr_label = current_header['Metric']
                if hdr_label not in seen:
                    target.append(current_header)
                    seen.add(hdr_label)
            target.append(row_dict)

        out = [formatted_header]
        col_order = [c for c in display_df.columns if c != '_source']

        if core_rows:
            core_df = pd.DataFrame(core_rows, columns=col_order)
            out.append(tabulate(core_df, headers="keys", tablefmt="grid", showindex=False))

        if supp_rows:
            supp_count = sum(
                1 for r in supp_rows
                if not (isinstance(r.get('Metric'), str) and r['Metric'].endswith(':'))
            )
            out.append("")
            out.append(
                f"  Additional details from SEC filings ({supp_count} items) "
                "-- auto-derived labels, may be redundant with the above:"
            )
            supp_df = pd.DataFrame(supp_rows, columns=col_order)
            out.append(tabulate(supp_df, headers="keys", tablefmt="grid", showindex=False))

        return "\n".join(out)
    
    def _add_balance_sheet_reconciliation(self, df, data):
        """
        Add a balance sheet reconciliation row to verify Assets = Liabilities + Equity.
        
        Args:
            df (pandas.DataFrame): DataFrame with financial data
            data (dict): The normalized financial data
            
        Returns:
            pandas.DataFrame: DataFrame with added reconciliation row
        """
        # Only add reconciliation for balance sheets
        if 'Assets' not in str(df['Metric'].values) or 'Liabilities' not in str(df['Metric'].values):
            return df
        
        # Create a copy of the dataframe
        new_df = df.copy()
        
        # For each period, calculate the reconciliation
        period_columns = [col for col in df.columns if col not in ['Company', 'Metric']]
        
        for period in period_columns:
            total_assets = None
            total_liabilities = None
            total_equity = None
            
            # Find the values
            for idx, row in df.iterrows():
                if row['Metric'] == 'Total Assets':
                    try:
                        # Handle formatted strings by removing commas
                        total_assets = float(str(row[period]).replace(',', ''))
                    except (ValueError, TypeError):
                        total_assets = None
                elif row['Metric'] == 'Total Liabilities':
                    try:
                        total_liabilities = float(str(row[period]).replace(',', ''))
                    except (ValueError, TypeError):
                        total_liabilities = None
                elif row['Metric'] == 'Stockholders\' Equity' or row['Metric'] == 'Total Equity':
                    try:
                        total_equity = float(str(row[period]).replace(',', ''))
                    except (ValueError, TypeError):
                        total_equity = None
        
            # Calculate difference if all values are available
            if total_assets is not None and total_liabilities is not None and total_equity is not None:
                difference = total_assets - total_liabilities - total_equity
                
                # Format the difference
                from utils.helpers import format_financial_number
                formatted_diff = format_financial_number(difference)
                
                # Create a reconciliation row
                reconciliation_data = {}
                for col in df.columns:
                    if col == 'Metric':
                        reconciliation_data[col] = "Reconciliation (Assets - Liabilities - Equity)"
                    elif col == 'Company':
                        reconciliation_data[col] = df['Company'].iloc[0] if 'Company' in df else ""
                    elif col == period:
                        reconciliation_data[col] = formatted_diff
                    else:
                        reconciliation_data[col] = ""
                
                # Append to the dataframe
                new_df = pd.concat([new_df, pd.DataFrame([reconciliation_data])], ignore_index=True)
        
        return new_df