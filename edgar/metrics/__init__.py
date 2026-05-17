"""Derived financial metrics on top of normalized XBRL pulls.

Public surface:
    REGISTRY                — slug -> MetricSpec
    register                — decorator to add a metric
    compute                 — slug + normalized statement -> {period: value}
    NormalizedStatement     — thin wrapper over parser output with concept resolver
    list_slugs              — registered slugs, optionally filtered by category
"""

from edgar.metrics.registry import (
    REGISTRY,
    MetricSpec,
    NormalizedStatement,
    compute,
    list_slugs,
    register,
)

# Importing submodules registers their metrics via @register side effects.
from edgar.metrics import derived_lines  # noqa: F401
from edgar.metrics import ratios  # noqa: F401
from edgar.metrics import margins  # noqa: F401
from edgar.metrics import returns  # noqa: F401
from edgar.metrics import working_capital  # noqa: F401
from edgar.metrics import growth  # noqa: F401

__all__ = [
    "REGISTRY",
    "MetricSpec",
    "NormalizedStatement",
    "compute",
    "list_slugs",
    "register",
]
