"""Standalone, read-only Angel One market-data adapter."""
from .client import AngelOneClient
from .auth import AuthStatus
from .models import DataResponse
from .greeks import (
    CANONICAL_GREEK_FIELDS, normalize_greek_row, index_greek_rows,
    match_greek, merge_leg_greeks,
)
from .capability import adapter_capability_report, format_capability_report

__all__ = [
    "AngelOneClient", "AuthStatus", "DataResponse",
    "CANONICAL_GREEK_FIELDS", "normalize_greek_row", "index_greek_rows",
    "match_greek", "merge_leg_greeks",
    "adapter_capability_report", "format_capability_report",
]
