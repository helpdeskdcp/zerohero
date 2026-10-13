"""Platform constants and safe defaults (G1). No environment switch can enable live trading here."""
from __future__ import annotations

import os

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PLATFORM_DB = os.path.abspath(os.path.join(_HERE, "..", "..", "data", "platform.db"))

EXECUTION_MODES = ("PAPER", "LIVE_MANUAL", "LIVE_AUTO")
DEFAULT_EXECUTION_MODE = "PAPER"
# Live execution adapters do not exist until a separately approved gate (G6). These are
# deliberate constants, not env flags: changing them requires a code change + review.
LIVE_MANUAL_IMPLEMENTED = False
LIVE_AUTO_IMPLEMENTED = False

# Provider policy (documented defaults consumed by later gates; no behaviour in G1).
PROVIDER_PRIORITY = ("ANGELONE", "DHAN")
STALE_TICK_AFTER_SEC = 12.0          # same freshness rule the live execution path already uses


def platform_db_path() -> str:
    return os.environ.get("CHANAKYA_PLATFORM_DB") or DEFAULT_PLATFORM_DB
