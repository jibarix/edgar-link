"""
Settings for the EDGAR Financial Tool.
"""

import os
from pathlib import Path

# Project base directory
BASE_DIR = Path(__file__).resolve().parent.parent

# API settings
API_REQUEST_TIMEOUT = 30  # seconds
API_RETRY_COUNT = 3
API_RETRY_DELAY = 2  # seconds

# Rate limiting to comply with SEC guidelines
# https://www.sec.gov/os/accessing-edgar-data
RATE_LIMIT_REQUESTS_PER_SECOND = 10

# Cache settings
CACHE_DIR = os.path.join(BASE_DIR, "cache")
CACHE_ENABLED = True
CACHE_EXPIRY = 24 * 60 * 60  # 24 hours in seconds

# Default number of periods to retrieve
DEFAULT_ANNUAL_PERIODS = 3
DEFAULT_QUARTERLY_PERIODS = 4

# Output settings
DEFAULT_OUTPUT_FORMAT = "csv"
SUPPORTED_OUTPUT_FORMATS = ["csv", "json", "excel", "html", "console"]
DEFAULT_OUTPUT_DIR = os.path.join(BASE_DIR, "output")

# User interface settings
TERMINAL_WIDTH = 80
SHOW_PROGRESS_BAR = True

# Logging settings
LOG_LEVEL = "INFO"
LOG_FILE = os.path.join(BASE_DIR, "logs", "edgar_tool.log")
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Create necessary directories if they don't exist
os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(DEFAULT_OUTPUT_DIR, exist_ok=True)
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)