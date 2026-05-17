"""
EDGAR Financial Tool - Main Module

A Python-based tool that interacts with the SEC's EDGAR database to retrieve
financial statements for public companies.
"""

import os
import sys
import logging
import argparse
from datetime import datetime

from config.constants import FINANCIAL_STATEMENT_TYPES, REPORTING_PERIODS
from config.settings import (
    DEFAULT_OUTPUT_FORMAT, SUPPORTED_OUTPUT_FORMATS, 
    DEFAULT_ANNUAL_PERIODS, DEFAULT_QUARTERLY_PERIODS,
    LOG_LEVEL, LOG_FILE, LOG_FORMAT, LOG_DATE_FORMAT
)

from edgar.company_lookup import search_company, get_cik_by_company_name
from edgar.filing_retrieval import FilingRetrieval
from edgar.xbrl_parser import XBRLParser
from edgar.data_formatter import DataFormatter
from edgar.statement_extractor import StatementExtractor

# Configure logging
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format=LOG_FORMAT,
    datefmt=LOG_DATE_FORMAT,
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)


def interactive_mode():
    """Run the tool in interactive mode with user prompts."""
    print("\n" + "=" * 60)
    print("  EDGAR Financial Tool - Interactive Mode")
    print("=" * 60 + "\n")
    
    # Step 1: Company Selection
    company_name = input("Enter company name or ticker symbol: ")
    
    # Search for the company
    matches = search_company(company_name)
    
    if not matches:
        print(f"No matches found for '{company_name}'.")
        return None
    
    # If multiple matches, let user choose
    if len(matches) > 1:
        print(f"\nFound {len(matches)} matches for '{company_name}':")
        for i, match in enumerate(matches, 1):
            print(f"{i}. {match['name']} (Ticker: {match['ticker']}, CIK: {match['cik']})")
        
        choice = int(input("\nSelect the correct company (or 0 to cancel): ") or "0")
        if choice == 0:
            return None
        if 1 <= choice <= len(matches):
            selected = matches[choice-1]
        else:
            print("Invalid selection.")
            return None
    else:
        selected = matches[0]
    
    cik = selected["cik"]
    company_name = selected["name"]
    ticker = selected["ticker"]
    
    print(f"\nSelected: {company_name} (Ticker: {ticker}, CIK: {cik})")
    
    # Step 2: Financial Statement Selection
    print("\nSelect financial statement type:")
    print("1. Balance Sheet (BS)")
    print("2. Income Statement (IS)")
    print("3. Cash Flow Statement (CF)")
    print("4. All Financial Statements (ALL)")
    
    statement_choice = input("\nEnter choice [1-4] (default: 4): ") or "4"
    
    statement_map = {
        "1": "BS",
        "2": "IS",
        "3": "CF",
        "4": "ALL"
    }
    
    statement_type = statement_map.get(statement_choice, "ALL")
    
    # Step 3: Report Type Selection
    print("\nSelect report type:")
    print("1. Annual Reports (10-K)")
    print("2. Quarterly Reports (10-Q)")
    
    report_type_choice = input("\nEnter choice [1-2] (default: 1): ") or "1"
    period_type = "annual" if report_type_choice == "1" else "quarterly"
    
    # Step 4: Number of Periods
    default_periods = DEFAULT_ANNUAL_PERIODS if period_type == "annual" else DEFAULT_QUARTERLY_PERIODS
    num_periods = int(input(f"\nNumber of periods to retrieve (default: {default_periods}): ") or default_periods)
    
    # Step 5: Output Format
    print("\nSelect output format:")
    for i, fmt in enumerate(SUPPORTED_OUTPUT_FORMATS, 1):
        print(f"{i}. {fmt.capitalize()}")
    
    format_choice = input(f"\nEnter choice [1-{len(SUPPORTED_OUTPUT_FORMATS)}] (default: 1): ") or "1"
    
    # Handle both numeric choices and direct text input
    try:
        choice_idx = int(format_choice) - 1
        if 0 <= choice_idx < len(SUPPORTED_OUTPUT_FORMATS):
            output_format = SUPPORTED_OUTPUT_FORMATS[choice_idx]
        else:
            output_format = SUPPORTED_OUTPUT_FORMATS[0]  # Default to first option
    except ValueError:
        # If user entered the format name directly
        format_choice = format_choice.lower()
        if format_choice in SUPPORTED_OUTPUT_FORMATS:
            output_format = format_choice
        else:
            output_format = SUPPORTED_OUTPUT_FORMATS[0]  # Default to first option
    
    output_file = None
    if output_format != "console":
        default_file = f"{ticker.lower()}_{statement_type.lower()}_{period_type}_{datetime.now().strftime('%Y%m%d')}"
        if output_format == "excel":
            default_file += ".xlsx"
        elif output_format == "csv":
            default_file += ".csv"
        elif output_format == "json":
            default_file += ".json"
        elif output_format == "html":
            default_file += ".html"
            
        file_input = input(f"\nOutput file (default: {default_file}): ")
        output_file = file_input if file_input else default_file
    
    return {
        "company_name": company_name,
        "ticker": ticker,
        "cik": cik,
        "statement_type": statement_type,
        "period_type": period_type,
        "num_periods": num_periods,
        "output_format": output_format,
        "output_file": output_file
    }


def setup_args():
    """Set up command-line arguments."""
    parser = argparse.ArgumentParser(
        description="EDGAR Financial Tool - Retrieve and analyze financial statements from SEC EDGAR"
    )
    
    # Company information
    company_group = parser.add_argument_group("Company Information")
    company_group.add_argument(
        "--company", "-c", type=str, help="Company name or ticker"
    )
    company_group.add_argument(
        "--cik", type=str, help="Company CIK number (overrides --company if provided)"
    )
    
    # Filing selection
    filing_group = parser.add_argument_group("Filing Selection")
    filing_group.add_argument(
        "--statement-type", "-s", type=str, default="ALL",
        choices=FINANCIAL_STATEMENT_TYPES.keys(),
        help="Financial statement type to extract (default: ALL)"
    )
    filing_group.add_argument(
        "--period-type", "-p", type=str, default="annual",
        choices=REPORTING_PERIODS.keys(),
        help="Reporting period type (default: annual)"
    )
    filing_group.add_argument(
        "--num-periods", "-n", type=int,
        help=f"Number of periods to retrieve (default: {DEFAULT_ANNUAL_PERIODS} for annual, "
             f"{DEFAULT_QUARTERLY_PERIODS} for quarterly)"
    )
    
    # Output options
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "--output-format", "-f", type=str, default=DEFAULT_OUTPUT_FORMAT,
        choices=SUPPORTED_OUTPUT_FORMATS,
        help=f"Output format (default: {DEFAULT_OUTPUT_FORMAT})"
    )
    output_group.add_argument(
        "--output-file", "-o", type=str,
        help="Output file path (default: auto-generated)"
    )
    
    return parser.parse_args()


def extract_financial_statements(params):
    """
    Extract financial statements based on user parameters.
    
    Args:
        params (dict): User-provided parameters
    
    Returns:
        dict: Extracted financial data
    """
    try:
        # Initialize components
        filing_retrieval = FilingRetrieval()
        xbrl_parser = XBRLParser()
        statement_extractor = StatementExtractor()
        
        print(f"\nRetrieving {params['statement_type']} for {params['company_name']} ({params['cik']})...")
        
        # Determine filing type based on period type
        filing_type = "10-K" if params["period_type"] == "annual" else "10-Q"
        
        # Get filing metadata
        filings = filing_retrieval.get_filing_metadata(
            params["cik"], 
            filing_type=filing_type, 
            limit=params["num_periods"]
        )
        
        if not filings:
            print(f"No {filing_type} filings found for {params['company_name']}.")
            return None
        
        print(f"Found {len(filings)} {filing_type} filings.")
        
        # First try to use the XBRL API which is more reliable
        print("Retrieving XBRL data for financial statements...")
        facts_data = filing_retrieval.get_company_facts(params["cik"])
        
        if facts_data:
            financial_data = xbrl_parser.parse_company_facts(
                facts_data,
                params["statement_type"],
                params["period_type"],
                params["num_periods"]
            )
            
            if financial_data and financial_data.get('metrics'):
                print(f"Successfully retrieved {len(financial_data.get('metrics', {}))} metrics from XBRL data.")
                return financial_data
            else:
                print("XBRL data extraction returned no metrics, falling back to statement extraction...")
        else:
            print("Failed to retrieve XBRL data, falling back to statement extraction...")
        
        # Fall back to statement extractor if XBRL fails
        financial_data = {}
        
        # Process each filing to extract statements
        for filing in filings[:params["num_periods"]]:
            print(f"Processing filing from {filing['filing_date']}...")
            if params["statement_type"] == "ALL":
                # Process all statement types if ALL is selected
                for statement_type in ["BS", "IS", "CF"]:
                    filing_data = statement_extractor.extract_statement(
                        params["ticker"], 
                        filing["accession_number"], 
                        statement_type
                    )
                    if filing_data is not None and not filing_data.empty:
                        if statement_type not in financial_data:
                            financial_data[statement_type] = {}
                        financial_data[statement_type][filing["filing_date"]] = filing_data
                        print(f"Successfully extracted {statement_type} from filing dated {filing['filing_date']}")
            else:
                # Process only the selected statement type
                filing_data = statement_extractor.extract_statement(
                    params["ticker"], 
                    filing["accession_number"], 
                    params["statement_type"]
                )
                if filing_data is not None and not filing_data.empty:
                    financial_data[filing["filing_date"]] = filing_data
                    print(f"Successfully extracted {params['statement_type']} from filing dated {filing['filing_date']}")
        
        if financial_data:
            # If we successfully extracted data using the statement extractor,
            # convert it to the format expected by the data formatter
            return format_statement_data(financial_data, params["statement_type"])
        
        print("Failed to extract financial statements from both XBRL data and filing documents.")
        return None
        
    except Exception as e:
        logger.error(f"Error extracting financial statements: {e}", exc_info=True)
        print(f"Error: {str(e)}")
        return None


def format_statement_data(extracted_data, statement_type):
    """
    Format the extracted statement data into the structure expected by the DataFormatter
    
    Args:
        extracted_data (dict): Data extracted from filings
        statement_type (str): Type of financial statement
        
    Returns:
        dict: Formatted financial data
    """
    if not extracted_data:
        return None
        
    # If statement_type is not "ALL", we have a simpler structure
    if statement_type != "ALL":
        periods = list(extracted_data.keys())
        metrics = {}
        
        # Create a combined set of all column names
        all_columns = set()
        for period, df in extracted_data.items():
            all_columns.update(df.columns)
        
        # For each metric (column), collect values across periods
        for column in all_columns:
            metric_key = f"{statement_type}_{column}"
            values = {}
            for period, df in extracted_data.items():
                if column in df.columns:
                    values[period] = df[column].iloc[0] if len(df) > 0 else None
                else:
                    values[period] = None
                    
            metrics[metric_key] = {
                "values": values,
                "category": statement_type,
                "tag": column,
                "display_name": column,
                "order": 50  # Default order
            }
            
        return {
            "periods": periods,
            "metrics": metrics,
            "metadata": {
                "period_type": "annual" if statement_type == "annual" else "quarterly"
            }
        }
    
    # For "ALL", we have a nested structure
    else:
        # Collect all periods across all statement types
        all_periods = set()
        for statement_type, periods_data in extracted_data.items():
            all_periods.update(periods_data.keys())
            
        periods = sorted(list(all_periods))
        metrics = {}
        
        # Process each statement type
        for statement_type, periods_data in extracted_data.items():
            # Create a combined set of all column names for this statement type
            statement_columns = set()
            for period, df in periods_data.items():
                statement_columns.update(df.columns)
                
            # For each metric (column), collect values across periods
            for column in statement_columns:
                metric_key = f"{statement_type}_{column}"
                values = {}
                for period in periods:
                    if period in periods_data and column in periods_data[period].columns:
                        values[period] = periods_data[period][column].iloc[0] if len(periods_data[period]) > 0 else None
                    else:
                        values[period] = None
                        
                metrics[metric_key] = {
                    "values": values,
                    "category": statement_type,
                    "tag": column,
                    "display_name": column,
                    "order": 50  # Default order
                }
                
        return {
            "periods": periods,
            "metrics": metrics,
            "metadata": {
                "period_type": "annual"  # Default to annual for combined statements
            }
        }


def main():
    """Main entry point for the EDGAR Financial Tool."""
    args = setup_args()
    
    # If no command-line arguments provided, use interactive mode
    if not args.company and not args.cik:
        params = interactive_mode()
        if not params:
            print("\nOperation cancelled.")
            return
    else:
        # Process command-line arguments
        if args.cik:
            cik = args.cik
            company_name = f"CIK {cik}"
            # Try to find ticker from CIK
            ticker = "UNKNOWN"
        elif args.company:
            cik = get_cik_by_company_name(args.company)
            if not cik:
                return
            company_name = args.company
            ticker = args.company  # Simplified, would need to look up actual ticker
        
        # Determine default number of periods
        if args.num_periods:
            num_periods = args.num_periods
        else:
            num_periods = DEFAULT_ANNUAL_PERIODS if args.period_type == "annual" else DEFAULT_QUARTERLY_PERIODS
        
        params = {
            "company_name": company_name,
            "ticker": ticker,
            "cik": cik,
            "statement_type": args.statement_type,
            "period_type": args.period_type,
            "num_periods": num_periods,
            "output_format": args.output_format,
            "output_file": args.output_file
        }
    
    # Display summary of selected options
    print("\n" + "=" * 60)
    print("  EDGAR Financial Tool - Request Summary")
    print("=" * 60)
    print(f"Company: {params['company_name']} (CIK: {params['cik']})")
    print(f"Statement Type: {FINANCIAL_STATEMENT_TYPES.get(params['statement_type'], params['statement_type'])}")
    print(f"Period Type: {REPORTING_PERIODS.get(params['period_type'], params['period_type'])}")
    print(f"Number of Periods: {params['num_periods']}")
    print(f"Output Format: {params['output_format']}")
    print("=" * 60 + "\n")
    
    # Extract and process financial data
    financial_data = extract_financial_statements(params)
    
    if not financial_data:
        print("\nFailed to extract financial statements. See error messages above.")
        return
    
    # Format and output the data
    formatter = DataFormatter(params["output_format"])
    output = formatter.format_statement(
        financial_data,
        params["statement_type"],
        params["company_name"],
        params["output_file"]
    )
    
    if params["output_format"] == "console" and output:
        # Print the formatted console output directly
        print(output)
    elif output and params["output_format"] != "console":
        print(f"\nOutput saved to: {output}")
    
    print("\nOperation completed successfully.")


if __name__ == "__main__":
    main()