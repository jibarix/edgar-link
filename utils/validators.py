"""
Input validation utilities for the EDGAR Financial Tool.
"""

import re
from datetime import datetime

from config.constants import FILING_TYPES, FINANCIAL_STATEMENT_TYPES, REPORTING_PERIODS


def is_valid_cik(cik):
    """
    Validate if a string is a valid CIK number.
    
    Args:
        cik (str): CIK number to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not cik:
        return False
    
    # Remove any non-digit characters
    cik_digits = re.sub(r'\D', '', cik)
    
    # CIK should be a 10-digit number (with potential leading zeros)
    return len(cik_digits) <= 10 and cik_digits.isdigit()


def is_valid_company_name(name):
    """
    Validate if a string is a valid company name.
    
    Args:
        name (str): Company name to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not name or not isinstance(name, str):
        return False
    
    # Company name should be at least 2 characters
    return len(name.strip()) >= 2


def is_valid_filing_type(filing_type):
    """
    Validate if a string is a valid SEC filing type.
    
    Args:
        filing_type (str): Filing type to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not filing_type or not isinstance(filing_type, str):
        return False
    
    # Check if the filing type is in our list of supported types
    return filing_type.upper() in FILING_TYPES


def is_valid_statement_type(statement_type):
    """
    Validate if a string is a valid financial statement type.
    
    Args:
        statement_type (str): Statement type to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not statement_type or not isinstance(statement_type, str):
        return False
    
    # Check if the statement type is in our list of supported types
    return statement_type.upper() in FINANCIAL_STATEMENT_TYPES


def is_valid_reporting_period(period):
    """
    Validate if a string is a valid reporting period.
    
    Args:
        period (str): Reporting period to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not period or not isinstance(period, str):
        return False
    
    # Check if the period is in our list of supported periods
    return period.lower() in REPORTING_PERIODS


def is_valid_date_range(start_date, end_date):
    """
    Validate if a date range is valid.
    
    Args:
        start_date (datetime): Start date
        end_date (datetime): End date
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not isinstance(start_date, datetime) or not isinstance(end_date, datetime):
        return False
    
    # End date should be after start date
    if end_date < start_date:
        return False
    
    # Date range should not be in the future
    now = datetime.now()
    return end_date <= now


def is_valid_number_of_periods(num_periods, period_type="annual"):
    """
    Validate if the number of periods is valid.
    
    Args:
        num_periods (int): Number of periods
        period_type (str): Type of period ('annual' or 'quarterly')
        
    Returns:
        bool: True if valid, False otherwise
    """
    try:
        num = int(num_periods)
    except (ValueError, TypeError):
        return False
    
    # Number of periods should be positive
    if num <= 0:
        return False
    
    # Set reasonable limits based on period type
    if period_type.lower() == "annual":
        return num <= 10  # Up to 10 years
    elif period_type.lower() == "quarterly":
        return num <= 40  # Up to 10 years (40 quarters)
    else:
        return False


def is_valid_output_format(output_format, supported_formats=None):
    """
    Validate if an output format is supported.
    
    Args:
        output_format (str): Output format to validate
        supported_formats (list): List of supported formats
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not output_format or not isinstance(output_format, str):
        return False
    
    if supported_formats is None:
        from config.settings import SUPPORTED_OUTPUT_FORMATS
        supported_formats = SUPPORTED_OUTPUT_FORMATS
    
    return output_format.lower() in map(str.lower, supported_formats)


def is_valid_url(url):
    """
    Validate if a string is a valid URL.
    
    Args:
        url (str): URL to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not url or not isinstance(url, str):
        return False
    
    # Simple URL pattern validation
    url_pattern = re.compile(
        r'^(https?:\/\/)'  # http:// or https://
        r'(([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])\.)*'  # domain segments
        r'([a-zA-Z0-9]|[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9])'  # last domain segment
        r'(:\d+)?'  # optional port
        r'(\/[-a-zA-Z0-9@:%._\+~#=&?]*)*'  # path
    )
    
    return bool(url_pattern.match(url))


def is_valid_ticker(ticker):
    """
    Validate if a string is a valid stock ticker symbol.
    
    Args:
        ticker (str): Ticker symbol to validate
        
    Returns:
        bool: True if valid, False otherwise
    """
    if not ticker or not isinstance(ticker, str):
        return False
    
    # Ticker symbols are typically 1-5 uppercase letters
    ticker_pattern = re.compile(r'^[A-Z]{1,5}$')
    
    return bool(ticker_pattern.match(ticker.upper()))