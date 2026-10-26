"""Fail-closed data validation: a symbol is usable only with a fresh, sane quote. Any failure removes it from the scoring set and is reported."""
from __future__ import annotations

import math
import os

MAX_AGE_SEC = float(os.environ.get("NIFTY_DIR_MAX_QUOTE_AGE_SEC", "5"))
MIN_COVERAGE = float(os.environ.get("NIFTY_DIR_MIN_WEIGHT_COVERAGE", "0.90"))     # share of index weight with valid quotes required to score


def check_quote(q, now: float, max_age: float | None = None) -> str | None:
    """None if usable, else a reason code."""
    max_age = MAX_AGE_SEC if max_age is None else max_age
    if q is None: return "NO_QUOTE"
    if not isinstance(q.ltp, (int, float)) or math.isnan(q.ltp) or q.ltp <= 0: return "BAD_LTP"
    if q.prev_close is None or q.prev_close <= 0: return "NO_PREV_CLOSE"
    if now - q.ts > max_age: return "STALE"
    if q.ts - now > 2: return "FUTURE_TS"
    if abs(q.ltp / q.prev_close - 1) > 0.25: return "IMPLAUSIBLE_MOVE"
    return None


def validate_universe(master: dict, quotes: dict, now: float, max_age: float | None = None) -> dict:
    ok, bad, cov = {}, {}, 0.0
    for m in master["members"]:
        r = check_quote(quotes.get(m["symbol"]), now, max_age)
        if r: bad[m["symbol"]] = r
        else: ok[m["symbol"]] = quotes[m["symbol"]]; cov += (m.get("weight_pct") or 0) / 100
    return {"valid": ok, "invalid": bad, "weight_coverage": round(cov, 4), "tradable": cov >= MIN_COVERAGE and master.get("weights_status") == "COMPLETE"}
