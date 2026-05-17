"""EDGAR Statement Extractor - Extracts financial statements from SEC filings.

HTML/XML fallback used when XBRL Company Facts data is unavailable or
incomplete. Walks the FilingSummary to find statement files and parses
their tables.
"""
from __future__ import annotations

import os
import logging
import calendar

import httpx
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup

from config.constants import HTTP_HEADERS
from edgar.company_lookup import format_cik

# Initialize logger
logger = logging.getLogger(__name__)

# Mapping between statement types and their possible names in filings
STATEMENT_KEYS_MAP = {
    "BS": [
        "balance sheet",
        "balance sheets",
        "statement of financial position",
        "consolidated balance sheets",
        "consolidated balance sheet",
        "consolidated financial position",
        "consolidated balance sheets - southern",
        "consolidated statements of financial position",
        "consolidated statement of financial position",
        "consolidated statements of financial condition",
        "combined and consolidated balance sheet",
        "condensed consolidated balance sheets",
        "consolidated balance sheets, as of december 31",
        "dow consolidated balance sheets",
        "consolidated balance sheets (unaudited)",
        # Add more specific patterns for companies like NVIDIA
        "consolidated balance sheets nvidia corporation"
    ],
    "IS": [
        "income statement",
        "income statements",
        "statement of earnings (loss)",
        "statements of consolidated income",
        "consolidated statements of operations",
        "consolidated statement of operations",
        "consolidated statements of earnings",
        "consolidated statement of earnings",
        "consolidated statements of income",
        "consolidated statement of income",
        "consolidated income statements",
        "consolidated income statement",
        "condensed consolidated statements of earnings",
        "consolidated results of operations",
        "consolidated statements of income (loss)",
        "consolidated statements of income - southern",
        "consolidated statements of operations and comprehensive income",
        "consolidated statements of comprehensive income",
        # Add NVIDIA specific patterns
        "consolidated statements of income nvidia corporation"
    ],
    "CF": [
        "cash flows statement",
        "cash flows statements",
        "statement of cash flows",
        "statements of consolidated cash flows",
        "consolidated statements of cash flows",
        "consolidated statement of cash flows",
        "consolidated statement of cash flow",
        "consolidated cash flows statements",
        "consolidated cash flow statements",
        "condensed consolidated statements of cash flows",
        "consolidated statements of cash flows (unaudited)",
        "consolidated statements of cash flows - southern",
        # Add NVIDIA specific patterns
        "consolidated statements of cash flows nvidia corporation"
    ]
}


class StatementExtractor:
    """
    Class for extracting financial statements from SEC filings.
    """
    
    def __init__(self):
        """Initialize the statement extractor."""
        self.headers = HTTP_HEADERS.copy()
    
    def cik_matching_ticker(self, ticker):
        """
        Get CIK number for a ticker symbol.
        
        Args:
            ticker (str): Stock ticker symbol
            
        Returns:
            str: CIK number with leading zeros
        """
        ticker = ticker.upper().replace(".", "-")
        
        # Try to get from company_tickers.json
        try:
            response = httpx.get(
                "https://www.sec.gov/files/company_tickers.json",
                headers=self.headers,
                timeout=30,
            )
            response.raise_for_status()
            ticker_json = response.json()

            for company in ticker_json.values():
                if company["ticker"] == ticker:
                    return format_cik(company["cik_str"])

            logger.warning(f"Ticker {ticker} not found in SEC database")
            return None

        except httpx.HTTPError as e:
            logger.error(f"Error fetching company tickers: {e}")
            return None
    
    def _get_file_name(self, report):
        """
        Extracts the file name from an XML report tag.

        Args:
            report (Tag): BeautifulSoup tag representing the report.

        Returns:
            str: File name extracted from the tag.
        """
        html_file_name_tag = report.find("HtmlFileName")
        xml_file_name_tag = report.find("XmlFileName")
        # Return the appropriate file name
        if html_file_name_tag:
            return html_file_name_tag.text
        elif xml_file_name_tag:
            return xml_file_name_tag.text
        else:
            return ""

    def _is_statement_file(self, short_name_tag, long_name_tag, file_name):
        """
        Determines if a given file is a financial statement file.

        Args:
            short_name_tag (Tag): BeautifulSoup tag for the short name.
            long_name_tag (Tag): BeautifulSoup tag for the long name.
            file_name (str): Name of the file.

        Returns:
            bool: True if it's a statement file, False otherwise.
        """
        return (
            short_name_tag is not None
            and long_name_tag is not None
            and file_name  # Ensure file_name is not an empty string
            and "Statement" in long_name_tag.text
        )
    
    def get_statement_file_names_in_filing_summary(self, ticker, accession_number):
        """
        Retrieves file names of financial statements from a filing summary.

        Args:
            ticker (str): Stock ticker symbol.
            accession_number (str): SEC filing accession number.

        Returns:
            dict: Dictionary mapping statement types to their file names.
        """
        try:
            cik = self.cik_matching_ticker(ticker)
            if not cik:
                logger.error(f"Could not find CIK for ticker {ticker}")
                return {}

            accn_no_dashes = accession_number.replace("-", "")

            base_link = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_no_dashes}"
            filing_summary_link = f"{base_link}/FilingSummary.xml"

            logger.info(f"Fetching filing summary from: {filing_summary_link}")
            filing_summary_response = httpx.get(
                filing_summary_link, headers=self.headers, timeout=30
            )
            filing_summary_response.raise_for_status()
            filing_summary_content = filing_summary_response.content.decode("utf-8")

            # Parse the filing summary
            filing_summary_soup = BeautifulSoup(filing_summary_content, "lxml-xml")
            statement_file_names_dict = {}
            
            # Extract file names for statements
            for report in filing_summary_soup.find_all("Report"):
                file_name = self._get_file_name(report)
                short_name_tag = report.find("ShortName")
                long_name_tag = report.find("LongName")
                
                if self._is_statement_file(short_name_tag, long_name_tag, file_name):
                    logger.debug(f"Found statement file: {short_name_tag.text} -> {file_name}")
                    statement_file_names_dict[short_name_tag.text.lower()] = file_name
                    
            # Log all found statement files for debugging
            logger.info(f"Found statement files: {list(statement_file_names_dict.keys())}")
            return statement_file_names_dict
            
        except httpx.HTTPError as e:
            logger.error(f"An error occurred fetching filing summary: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error processing filing summary: {e}")
            return {}

    def extract_statement(self, ticker, accession_number, statement_type):
        """
        Extract a financial statement for a company filing.
        
        Args:
            ticker (str): Stock ticker symbol
            accession_number (str): SEC filing accession number
            statement_type (str): Type of statement to extract ("BS", "IS", "CF")
            
        Returns:
            pandas.DataFrame: Extracted financial statement or None if extraction fails
        """
        try:
            logger.info(f"Extracting {statement_type} statement for {ticker}, accession {accession_number}")
            
            # Get the statement soup
            soup = self.get_statement_soup(ticker, accession_number, statement_type)
            
            if soup:
                logger.info(f"Successfully found {statement_type} statement in filing {accession_number}")
                
                # Extract data from the statement
                columns, values, dates = self.extract_columns_values_and_dates_from_statement(soup)
                
                logger.info(f"Extracted {len(columns)} columns, {len(values)} values, and {len(dates)} dates")
                
                # Create DataFrame from the extracted data
                df = self.create_dataframe_of_statement_values_columns_dates(values, columns, dates)
                
                # Remove duplicate columns
                if not df.empty:
                    df = df.T.drop_duplicates()
                    logger.info(f"Successfully created DataFrame with shape {df.shape}")
                    return df
                else:
                    logger.warning(f"Created DataFrame is empty for {ticker}, accession {accession_number}")
            else:
                logger.warning(f"Could not find {statement_type} in filing {accession_number} for {ticker}")
            
            return None
            
        except Exception as e:
            logger.error(f"Error extracting statement: {e}", exc_info=True)
            return None
    
    def get_statement_soup(self, ticker, accession_number, statement_type):
        """
        Get BeautifulSoup object for a specific financial statement.
        
        Args:
            ticker (str): Stock ticker symbol
            accession_number (str): SEC filing accession number
            statement_type (str): Type of statement to extract ("BS", "IS", "CF")
            
        Returns:
            BeautifulSoup: Parsed HTML content of the statement or None if not found
        """
        try:
            cik = self.cik_matching_ticker(ticker)
            if not cik:
                logger.error(f"Could not find CIK for ticker {ticker}")
                return None
            
            # Format the accession number
            accn_no_dashes = accession_number.replace("-", "")
            
            # Base URL for the filing
            base_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{accn_no_dashes}"
            
            # Get the statement file names from the filing summary
            statement_file_name_dict = self.get_statement_file_names_in_filing_summary(ticker, accession_number)
            
            if not statement_file_name_dict:
                logger.warning(f"No statement files found in filing {accession_number}")
                return None
            
            # Find the statement file for the specified statement type
            statement_link = None
            
            for possible_key in STATEMENT_KEYS_MAP.get(statement_type, []):
                file_name = statement_file_name_dict.get(possible_key)
                if file_name:
                    statement_link = f"{base_url}/{file_name}"
                    logger.info(f"Found statement link for {statement_type}: {statement_link}")
                    break
            
            if not statement_link:
                # Try case-insensitive matching as a fallback
                for dict_key, file_name in statement_file_name_dict.items():
                    for possible_key in STATEMENT_KEYS_MAP.get(statement_type, []):
                        if possible_key.lower() in dict_key.lower():
                            statement_link = f"{base_url}/{file_name}"
                            logger.info(f"Found statement link using case-insensitive match: {statement_link}")
                            break
                    if statement_link:
                        break
            
            if not statement_link:
                logger.warning(f"Could not find statement file for {statement_type}")
                return None
            
            # Get the statement file
            response = httpx.get(statement_link, headers=self.headers, timeout=30)
            response.raise_for_status()

            # Parse the statement HTML/XML
            if statement_link.endswith(".xml"):
                return BeautifulSoup(response.content, "lxml-xml")
            else:
                return BeautifulSoup(response.content, "lxml")

        except httpx.HTTPError as e:
            logger.error(f"Request error fetching statement: {e}")
            return None
        except Exception as e:
            logger.error(f"Error getting statement soup: {e}", exc_info=True)
            return None
    
    def extract_columns_values_and_dates_from_statement(self, soup):
        """
        Extract columns, values, and dates from a financial statement.
        
        Args:
            soup (BeautifulSoup): Parsed HTML of the statement
            
        Returns:
            tuple: (columns, values_set, date_time_index)
        """
        columns = []
        values_set = []
        date_time_index = self.get_datetime_index_dates_from_statement(soup)
        
        for table in soup.find_all("table"):
            unit_multiplier = 1
            special_case = False
            
            # Check table headers for unit multipliers and special cases
            table_header = table.find("th")
            if table_header:
                header_text = table_header.get_text() if table_header.get_text() else ""
                # Determine unit multiplier based on header text
                if "in Thousands" in header_text:
                    unit_multiplier = 1000
                    logger.debug("Detected unit multiplier: thousands")
                elif "in Millions" in header_text:
                    unit_multiplier = 1000000
                    logger.debug("Detected unit multiplier: millions")
                # Check for special case scenario
                if "unless otherwise specified" in header_text:
                    special_case = True
                    logger.debug("Detected special case formatting")
            
            # Process each row of the table
            for row in table.select("tr"):
                onclick_elements = row.select("td.pl a, td.pl.custom a")
                if not onclick_elements:
                    continue
                
                try:
                    # Get column title
                    onclick_attr = onclick_elements[0].get("onclick", "")
                    if not onclick_attr:
                        continue
                        
                    column_title = onclick_attr.split("defref_")[-1].split("',")[0]
                    columns.append(column_title)
                
                    # Initialize values array with NaNs
                    values = [np.nan] * len(date_time_index)
                
                    # Process each cell in the row
                    for i, cell in enumerate(row.select("td.text, td.nump, td.num")):
                        if i >= len(values) or "text" in cell.get("class", []):
                            continue
                        
                        # Clean and parse cell value
                        cell_text = cell.text.strip()
                        value = self.keep_numbers_and_decimals_only_in_string(
                            cell_text.replace("$", "")
                            .replace(",", "")
                            .replace("(", "-")
                            .replace(")", "")
                            .strip()
                        )
                        
                        if value:
                            try:
                                value = float(value)
                                # Adjust value based on special case and cell class
                                if special_case:
                                    # Handle special case formatting
                                    pass
                                
                                if "nump" in cell.get("class", []):
                                    values[i] = value * unit_multiplier
                                else:
                                    # num class indicates negative values in accounting format
                                    if "num" in cell.get("class", []) and value > 0 and "(" in cell_text:
                                        values[i] = -value * unit_multiplier
                                    else:
                                        values[i] = value * unit_multiplier
                            except ValueError:
                                pass
                    
                    values_set.append(values)
                except Exception as e:
                    logger.warning(f"Error processing row: {e}")
                    continue
        
        return columns, values_set, date_time_index
    
    def get_datetime_index_dates_from_statement(self, soup):
        """
        Extract dates from a financial statement.
        
        Args:
            soup (BeautifulSoup): Parsed HTML of the statement
            
        Returns:
            pandas.DatetimeIndex: DatetimeIndex for the statement periods
        """
        table_headers = soup.find_all("th", {"class": "th"})
        dates = []
        
        for th in table_headers:
            div = th.find("div")
            if div and div.string:
                date_str = str(div.string).strip()
                # Standardize the date
                standardized_date = self.standardize_date(date_str).replace(".", "")
                dates.append(standardized_date)
        
        logger.debug(f"Extracted dates: {dates}")
        
        # Convert to DatetimeIndex
        try:
            return pd.to_datetime(dates)
        except:
            # Handle parsing errors
            logger.warning(f"Could not parse dates: {dates}")
            return pd.DatetimeIndex([])
    
    def standardize_date(self, date_str):
        """
        Standardize date format by replacing month abbreviations.
        
        Args:
            date_str (str): Original date string
            
        Returns:
            str: Standardized date string
        """
        # Replace month abbreviations with full month names
        for abbr, full in zip(calendar.month_abbr[1:], calendar.month_name[1:]):
            date_str = date_str.replace(abbr, full)
        
        return date_str
    
    def keep_numbers_and_decimals_only_in_string(self, mixed_string):
        """
        Filter a string to keep only numbers, decimals and negative sign.
        
        Args:
            mixed_string (str): Input string
            
        Returns:
            str: Filtered string
        """
        allowed_chars = "1234567890.-"
        return "".join(c for c in mixed_string if c in allowed_chars)
    
    def create_dataframe_of_statement_values_columns_dates(self, values_set, columns, index_dates):
        """
        Create a DataFrame from extracted statement data.
        
        Args:
            values_set (list): Set of values
            columns (list): Column names
            index_dates (pandas.DatetimeIndex): Date index
            
        Returns:
            pandas.DataFrame: Statement DataFrame
        """
        if not values_set or not columns or len(index_dates) == 0:
            logger.warning("Missing data for DataFrame creation")
            return pd.DataFrame()
        
        # Transpose the values set
        transposed_values = list(zip(*values_set))
        
        # Create DataFrame
        try:
            df = pd.DataFrame(transposed_values, columns=columns, index=index_dates)
            
            # Format display of large numbers
            pd.options.display.float_format = (
                lambda x: "{:,.0f}".format(x) if int(x) == x else "{:,.2f}".format(x)
            )
            
            return df
        except Exception as e:
            logger.error(f"Error creating DataFrame: {e}")
            return pd.DataFrame()
    
    def save_statement_to_csv(self, statement, output_path):
        """
        Save statement DataFrame to CSV.
        
        Args:
            statement (pandas.DataFrame): Statement DataFrame
            output_path (str): Output file path
            
        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            
            # Save to CSV
            statement.to_csv(output_path)
            logger.info(f"Statement saved to {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"Error saving statement to CSV: {e}")
            return False