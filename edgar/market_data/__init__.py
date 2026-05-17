"""Non-EDGAR market data sources.

This package holds clients for external market data feeds (equity prices,
indices, etc.) that complement the SEC XBRL pulls in the rest of the
project. Kept separate so EDGAR data and market data have clear ownership
of their own cache namespaces and retry behavior.
"""
