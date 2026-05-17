"""Company lookup functionality for finding SEC CIK numbers."""
from __future__ import annotations

import re
from difflib import get_close_matches

import httpx

from config.constants import COMPANY_TICKERS_URL, HTTP_HEADERS, ERROR_MESSAGES
from config.settings import API_REQUEST_TIMEOUT, API_RETRY_COUNT, API_RETRY_DELAY
from utils.cache import Cache
from utils.helpers import retry_request

# Initialize cache for company lookup
company_cache = Cache("company_lookup")


def format_cik(cik):
    """
    Format CIK to 10-digit format with leading zeros.
    
    Args:
        cik (str or int): The CIK number
        
    Returns:
        str: Formatted 10-digit CIK
    """
    return str(cik).zfill(10)


def get_company_tickers():
    """Retrieve the SEC company tickers feed indexed for lookup.

    Returns a dict with two sub-indexes so callers can rank ticker and
    name hits independently:
        {
            "by_name":   normalized lowercase name -> company entry,
            "by_ticker": uppercase ticker          -> company entry,
        }
    Each entry has the shape {"cik", "ticker", "name"}.
    """
    cached_data = company_cache.get("company_tickers_v2")
    if cached_data:
        return cached_data

    try:
        response = retry_request(
            httpx.get,
            COMPANY_TICKERS_URL,
            headers=HTTP_HEADERS,
            timeout=API_REQUEST_TIMEOUT,
            max_retries=API_RETRY_COUNT,
            retry_delay=API_RETRY_DELAY
        )
        response.raise_for_status()
        data = response.json()

        by_name: dict[str, dict] = {}
        by_ticker: dict[str, dict] = {}
        for _, company_info in data.items():
            cik = company_info["cik_str"]
            ticker = company_info["ticker"]
            title = company_info["title"]
            normalized_title = re.sub(r'[^\w\s]', '', title.lower()).strip()

            entry = {
                "cik": format_cik(cik),
                "ticker": ticker,
                "name": title,
            }
            # First-wins on name collisions — SEC feed is mostly stable.
            by_name.setdefault(normalized_title, entry)
            by_ticker[ticker.upper()] = entry

        result = {"by_name": by_name, "by_ticker": by_ticker}
        company_cache.set("company_tickers_v2", result)
        return result

    except httpx.HTTPError as e:
        print(f"Error fetching company tickers: {e}")
        return {"by_name": {}, "by_ticker": {}}


# Common corporate suffixes we strip / try-appending when resolving names.
_CORP_SUFFIXES = ("inc", "corp", "corporation", "company", "co", "ltd",
                  "limited", "lp", "llc", "plc", "holdings", "group")


def search_company(query):
    """Search for a company by name or ticker.

    Ranking order (first non-empty rule wins):
        1. Exact ticker (case-insensitive)         -> 1 result
        2. Exact normalized name                   -> 1 result
        3. Name == query + common corporate suffix -> 1 result
           (catches "Apple" -> "Apple Inc")
        4. Names starting with query, shortest first (so "Apple" prefers
           "Apple Inc" over "Apple Hospitality REIT Inc")
        5. Fuzzy name match over names only — tickers excluded so short
           tickers like APLE don't outrank longer names by ratio.
    """
    q = query.strip()
    q_lower = q.lower()
    q_norm = re.sub(r'[^\w\s]', '', q_lower).strip()
    if not q_norm:
        return []

    data = get_company_tickers()
    by_name = data["by_name"]
    by_ticker = data["by_ticker"]

    # 1. Exact ticker
    if q.upper() in by_ticker:
        return [by_ticker[q.upper()]]

    # 2. Exact normalized name
    if q_norm in by_name:
        return [by_name[q_norm]]

    # 3. Query + standard suffix
    for suffix in _CORP_SUFFIXES:
        candidate = f"{q_norm} {suffix}"
        if candidate in by_name:
            return [by_name[candidate]]

    # 4. Prefix matches, shortest name first
    prefix_token = q_norm + " "
    prefix_hits = [
        entry for norm, entry in by_name.items()
        if norm.startswith(prefix_token)
    ]
    if prefix_hits:
        prefix_hits.sort(key=lambda e: len(e["name"]))
        return prefix_hits[:5]

    # 5. Fuzzy fallback over names only
    fuzzy = get_close_matches(q_norm, list(by_name.keys()), n=5, cutoff=0.6)
    return [by_name[m] for m in fuzzy]


def get_cik_by_company_name(company_name):
    """
    Get a company's CIK by its name.
    
    Args:
        company_name (str): The company name to look up
        
    Returns:
        str or None: The CIK if found, None otherwise
    """
    matches = search_company(company_name)
    
    if not matches:
        print(ERROR_MESSAGES["COMPANY_NOT_FOUND"])
        return None
    
    # If only one match, return it
    if len(matches) == 1:
        return matches[0]["cik"]
    
    # If multiple matches, let the user choose
    print(f"Found {len(matches)} matches for '{company_name}':")
    for i, match in enumerate(matches, 1):
        print(f"{i}. {match['name']} (Ticker: {match['ticker']}, CIK: {match['cik']})")
    
    while True:
        try:
            choice = int(input("Enter the number of the correct company (0 to cancel): "))
            if choice == 0:
                return None
            if 1 <= choice <= len(matches):
                return matches[choice-1]["cik"]
            print("Invalid choice, please try again.")
        except ValueError:
            print("Please enter a valid number.")


if __name__ == "__main__":
    # Simple test of the module
    test_query = input("Enter a company name to search: ")
    matches = search_company(test_query)
    
    if matches:
        print(f"Found {len(matches)} matches:")
        for match in matches:
            print(f"Name: {match['name']}, Ticker: {match['ticker']}, CIK: {match['cik']}")
    else:
        print("No matches found.")