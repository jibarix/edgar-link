"""Module for retrieving SEC EDGAR filings and data through SEC APIs."""
from __future__ import annotations

import json
import logging
import time
import threading

import httpx
from jsonschema import validate, ValidationError

from config.constants import (
    SEC_BASE_URL, SUBMISSIONS_URL, HTTP_HEADERS, ERROR_MESSAGES
)
from config.settings import (
    API_REQUEST_TIMEOUT, API_RETRY_COUNT, API_RETRY_DELAY,
    RATE_LIMIT_REQUESTS_PER_SECOND,
)
from utils.cache import Cache
from utils.helpers import retry_request, parse_date
from utils.validators import is_valid_cik, is_valid_filing_type

# Initialize logger
logger = logging.getLogger(__name__)

# Initialize cache for filing data
filing_cache = Cache("filing_data", expiry=3600)
# Immutable XBRL instance docs — cache for 30 days
instance_cache = Cache("xbrl_instance", expiry=30 * 86400)

# Define a basic JSON schema for validating EDGAR submissions.
EDGAR_SUBMISSIONS_SCHEMA = {
    "type": "object",
    "properties": {
        "filings": {
            "type": "object",
            "properties": {
                "recent": {"type": "object"},
                "files": {"type": "array"}
            },
            "required": ["recent", "files"]
        }
    },
    "required": ["filings"]
}


class FilingRetrieval:
    """
    Class for retrieving SEC EDGAR filings and data.
    """
    
    def __init__(self):
        """Initialize the filing retrieval object."""
        self.last_request_time = 0
        self.rate_limit_lock = threading.Lock()
        
    def _respect_rate_limit(self):
        """
        Ensure we respect SEC's rate limiting guidelines.
        This method is thread-safe.
        """
        with self.rate_limit_lock:
            current_time = time.time()
            time_since_last_request = current_time - self.last_request_time
            sleep_time = max(0, (1.0 / RATE_LIMIT_REQUESTS_PER_SECOND) - time_since_last_request)
            if sleep_time > 0:
                time.sleep(sleep_time)
            self.last_request_time = time.time()
    
    def validate_submissions_data(self, data):
        """
        Validate the JSON structure of EDGAR submissions data using jsonschema.
        """
        try:
            validate(instance=data, schema=EDGAR_SUBMISSIONS_SCHEMA)
        except ValidationError as e:
            logger.error(f"EDGAR submissions JSON validation error: {e}")
            return False
        return True
    
    def get_company_submissions(self, cik):
        """
        Get company submissions data from SEC.
        
        Args:
            cik (str): The company CIK
            
        Returns:
            dict: Company submissions data
        """
        if not is_valid_cik(cik):
            logger.error(ERROR_MESSAGES["INVALID_CIK"])
            return None
        
        formatted_cik = str(cik).zfill(10)
        cache_key = f"submissions_{formatted_cik}"
        cached_data = filing_cache.get(cache_key)
        if cached_data:
            return cached_data
        
        url = SUBMISSIONS_URL.format(cik=formatted_cik)
        try:
            self._respect_rate_limit()
            response = retry_request(
                httpx.get,
                url,
                headers=HTTP_HEADERS,
                timeout=API_REQUEST_TIMEOUT,
                max_retries=API_RETRY_COUNT,
                retry_delay=API_RETRY_DELAY
            )
            response.raise_for_status()
            submissions_data = response.json()
            if not self.validate_submissions_data(submissions_data):
                return None
            filing_cache.set(cache_key, submissions_data)
            return submissions_data
        except httpx.HTTPError as e:
            logger.error(f"Error fetching company submissions: {e}")
            return None
    
    def get_filing_metadata(self, cik, filing_type="10-K", start_date=None, end_date=None, limit=10):
        """
        Get metadata for company filings.
        
        Args:
            cik (str): The company CIK
            filing_type (str): Type of filing to retrieve
            start_date (datetime): Start date for filings
            end_date (datetime): End date for filings
            limit (int): Maximum number of filings to retrieve
            
        Returns:
            list: List of filing metadata
        """
        if not is_valid_cik(cik):
            logger.error(ERROR_MESSAGES["INVALID_CIK"])
            return []
            
        if not is_valid_filing_type(filing_type):
            logger.warning(f"Unsupported filing type: {filing_type}")
            # Continue anyway; SEC may have filing types we don't list
        
        formatted_cik = str(cik).zfill(10)
        submissions = self.get_company_submissions(formatted_cik)
        if not submissions:
            return []
        
        filings = []
        # Process recent filings first
        recent_filings = submissions.get("filings", {}).get("recent", {})
        if recent_filings:
            filings.extend(self._process_filings_data(
                recent_filings, 
                filing_type, 
                start_date, 
                end_date, 
                limit
            ))
        
        # If more filings are needed, process historical filings
        if len(filings) < limit:
            historical_filings = submissions.get("filings", {}).get("files", [])
            remaining_limit = limit - len(filings)
            for file_info in historical_filings:
                if len(filings) >= limit:
                    break
                file_url = f"{SEC_BASE_URL}{file_info.get('name')}"
                historical_data = self._get_historical_filings(file_url)
                if historical_data:
                    more_filings = self._process_filings_data(
                        historical_data, 
                        filing_type, 
                        start_date, 
                        end_date, 
                        remaining_limit
                    )
                    filings.extend(more_filings)
                    remaining_limit = limit - len(filings)
        
        if not filings:
            logger.warning(f"No {filing_type} filings found for CIK {cik} in the specified date range")
        
        return filings
    
    def _get_historical_filings(self, file_url):
        """
        Get historical filings data from SEC.
        
        Args:
            file_url (str): URL to the historical filings index
            
        Returns:
            dict: Historical filings data or None on error
        """
        cache_key = f"historical_{file_url}"
        cached_data = filing_cache.get(cache_key)
        if cached_data:
            return cached_data
        
        try:
            self._respect_rate_limit()
            response = retry_request(
                httpx.get,
                file_url,
                headers=HTTP_HEADERS,
                timeout=API_REQUEST_TIMEOUT,
                max_retries=API_RETRY_COUNT,
                retry_delay=API_RETRY_DELAY
            )
            response.raise_for_status()
            historical_data = response.json()
            filing_cache.set(cache_key, historical_data)
            return historical_data
        except httpx.HTTPError as e:
            logger.error(f"Error fetching historical filings: {e}")
            return None
    
    def _process_filings_data(self, filings_data, filing_type, start_date, end_date, limit):
        """
        Process filings data to extract relevant filings.
        
        Args:
            filings_data (dict): Filings data from SEC API
            filing_type (str): Type of filing to filter
            start_date (datetime): Start date for filtering
            end_date (datetime): End date for filtering
            limit (int): Maximum number of filings to extract
            
        Returns:
            list: List of filing metadata
        """
        processed_filings = []
        
        accession_numbers = filings_data.get("accessionNumber", [])
        form_types = filings_data.get("form", [])
        filing_dates = filings_data.get("filingDate", [])
        descriptions = filings_data.get("primaryDocument", [])
        urls = filings_data.get("primaryDocumentUrl", [])
        reporting_dates = filings_data.get("reportDate", [])
        
        for i in range(min(len(accession_numbers), len(form_types))):
            form = form_types[i] if i < len(form_types) else ""
            if filing_type.upper() != "ALL" and form.upper() != filing_type.upper():
                continue
            
            filing_date_str = filing_dates[i] if i < len(filing_dates) else ""
            filing_date = parse_date(filing_date_str)
            if start_date and filing_date and filing_date < start_date:
                continue
            if end_date and filing_date and filing_date > end_date:
                continue
            
            filing_info = {
                "accession_number": accession_numbers[i] if i < len(accession_numbers) else "",
                "form": form,
                "filing_date": filing_date_str,
                "description": descriptions[i] if i < len(descriptions) else "",
                "url": urls[i] if i < len(urls) else "",
                "reporting_date": reporting_dates[i] if i < len(reporting_dates) else "",
            }
            
            processed_filings.append(filing_info)
            if len(processed_filings) >= limit:
                break
        
        return processed_filings

    def get_company_facts(self, cik):
        """
        Get all XBRL facts for a company using the SEC's Company Facts API.
        
        Args:
            cik (str): The company CIK
            
        Returns:
            dict: Company facts data or None on error
        """
        if not is_valid_cik(cik):
            logger.error(ERROR_MESSAGES["INVALID_CIK"])
            return None
            
        formatted_cik = str(cik).zfill(10)
        cache_key = f"company_facts_{formatted_cik}"
        cached_data = filing_cache.get(cache_key)
        if cached_data:
            return cached_data
        
        url = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{formatted_cik}.json"
        
        try:
            self._respect_rate_limit()
            response = retry_request(
                httpx.get,
                url,
                headers=HTTP_HEADERS,
                timeout=API_REQUEST_TIMEOUT,
                max_retries=API_RETRY_COUNT,
                retry_delay=API_RETRY_DELAY
            )
            response.raise_for_status()
            facts_data = response.json()
            filing_cache.set(cache_key, facts_data)
            return facts_data
        except httpx.HTTPError as e:
            logger.error(f"Error fetching company facts: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing company facts JSON: {e}")
            return None

    def get_filing_instance_xml(self, cik, accession_number):
        """Fetch the standalone XBRL instance document (*_htm.xml) for a filing.

        The *_htm.xml file is the regulator-validated XBRL extracted from the
        filing's iXBRL HTML. It contains every reported fact INCLUDING
        company-extension concepts that the Company Facts API filters out.

        Args:
            cik: Company CIK (zero-padded or not).
            accession_number: SEC accession number, with or without dashes.

        Returns:
            bytes: XML content, or None on error / no instance doc found.
        """
        if not is_valid_cik(cik):
            logger.error(ERROR_MESSAGES["INVALID_CIK"])
            return None

        cik_int = int(str(cik).lstrip("0") or "0")
        acc_nodash = str(accession_number).replace("-", "")
        cache_key = f"instance_xml_{cik_int}_{acc_nodash}"

        # Instance docs are immutable once filed; use the long-TTL cache
        cached = instance_cache.get(cache_key)
        if cached is not None:
            return cached

        # Resolve the instance filename via the filing's index.json
        base = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{acc_nodash}"
        index_url = f"{base}/index.json"
        try:
            self._respect_rate_limit()
            r = retry_request(
                httpx.get, index_url, headers=HTTP_HEADERS,
                timeout=API_REQUEST_TIMEOUT,
                max_retries=API_RETRY_COUNT, retry_delay=API_RETRY_DELAY,
            )
            r.raise_for_status()
            items = r.json().get("directory", {}).get("item", [])
        except (httpx.HTTPError, json.JSONDecodeError) as e:
            logger.warning(f"Failed to fetch filing index {accession_number}: {e}")
            return None

        instance_name = next(
            (it["name"] for it in items if it.get("name", "").endswith("_htm.xml")),
            None,
        )
        if not instance_name:
            # Older filings may use a different naming convention; try the
            # primary .xml that isn't a linkbase
            for it in items:
                name = it.get("name", "")
                if (name.endswith(".xml")
                        and not name.endswith(("_cal.xml", "_def.xml",
                                               "_lab.xml", "_pre.xml",
                                               "FilingSummary.xml"))):
                    instance_name = name
                    break
        if not instance_name:
            logger.warning(f"No XBRL instance doc found in filing {accession_number}")
            return None

        instance_url = f"{base}/{instance_name}"
        try:
            self._respect_rate_limit()
            r = retry_request(
                httpx.get, instance_url, headers=HTTP_HEADERS,
                timeout=API_REQUEST_TIMEOUT,
                max_retries=API_RETRY_COUNT, retry_delay=API_RETRY_DELAY,
            )
            r.raise_for_status()
            content = r.content
            instance_cache.set(cache_key, content)
            return content
        except httpx.HTTPError as e:
            logger.warning(f"Failed to download instance {instance_url}: {e}")
            return None

    def get_company_concept(self, cik, taxonomy, concept):
        """
        Get all values for a specific concept from a company using the SEC's Company Concept API.
        
        Args:
            cik (str): The company CIK
            taxonomy (str): The taxonomy (e.g., 'us-gaap', 'ifrs-full')
            concept (str): The concept name (e.g., 'Assets', 'Liabilities')
            
        Returns:
            dict: Company concept data or None on error
        """
        if not is_valid_cik(cik):
            logger.error(ERROR_MESSAGES["INVALID_CIK"])
            return None
            
        formatted_cik = str(cik).zfill(10)
        cache_key = f"company_concept_{formatted_cik}_{taxonomy}_{concept}"
        cached_data = filing_cache.get(cache_key)
        if cached_data:
            return cached_data
        
        url = f"https://data.sec.gov/api/xbrl/companyconcept/CIK{formatted_cik}/{taxonomy}/{concept}.json"
        
        try:
            self._respect_rate_limit()
            response = retry_request(
                httpx.get,
                url,
                headers=HTTP_HEADERS,
                timeout=API_REQUEST_TIMEOUT,
                max_retries=API_RETRY_COUNT,
                retry_delay=API_RETRY_DELAY
            )
            response.raise_for_status()
            concept_data = response.json()
            filing_cache.set(cache_key, concept_data)
            return concept_data
        except httpx.HTTPError as e:
            logger.error(f"Error fetching company concept: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"Error parsing company concept JSON: {e}")
            return None