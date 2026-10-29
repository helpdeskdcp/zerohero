"""The one place that says which underlyings the contest trades and on which exchange (it used to be repeated in submit, channel_parse, channel_ingest and api: adding an underlying meant four edits), plus the IST zone."""
from __future__ import annotations

from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
EXCH = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "SENSEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "GOLDM": "MCX"}
ALLOWED = tuple(EXCH)
MCX_UNDERLYINGS = tuple(u for u, x in EXCH.items() if x == "MCX")
