"""Half-spread (fraction of price) per underlying from MEASURED captured quotes (usable bid/ask rows in a premium band), else a stated fallback. Applied identically to both sides
whenever a snapshot has no usable bid/ask. Fallbacks are assumptions and are labelled as such in every result."""
from __future__ import annotations

import os
import sqlite3
import statistics

DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "market_history.db")
BANDS = {"NIFTY": (40, 400), "BANKNIFTY": (100, 900), "FINNIFTY": (30, 300), "SENSEX": (60, 700), "BANKEX": (60, 700), "NATURALGAS": (3, 60), "CRUDEOIL": (80, 800)}
PROXY = {"SENSEX": "NIFTY", "BANKEX": "BANKNIFTY"}            # no bid/ask captured for these: use the closest index-option liquidity class (ASSUMPTION)
FALLBACK_HALF = 0.02                                             # 2% per leg when nothing measurable exists (deliberately conservative)
MIN_ROWS = 200


def measure(underlying: str, days: list[str], path: str | None = None) -> tuple[float | None, int]:
    lo, hi = BANDS.get(underlying, (20, 500))
    c = sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=30)
    try:
        vals = []
        for d in days:
            for bid, ask in c.execute("SELECT bid,ask FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND bid>0 AND ask>bid AND ltp BETWEEN ? AND ? LIMIT 20000", (d, underlying, lo, hi)):
                m = (ask + bid) / 2.0
                if (ask - bid) / m <= 0.25: vals.append((ask - bid) / m / 2.0)
    finally:
        c.close()
    return (statistics.median(vals), len(vals)) if vals else (None, 0)


def half_spread(underlying: str, days: list[str], path: str | None = None) -> dict:
    h, n = measure(underlying, days, path)
    if h is not None and n >= MIN_ROWS: return {"half_spread": h, "source": f"MEASURED({n} rows)"}
    p = PROXY.get(underlying)
    if p:
        h2, n2 = measure(p, days, path)
        if h2 is not None and n2 >= MIN_ROWS: return {"half_spread": h2, "source": f"PROXY_{p}_MEASURED({n2} rows) [assumption]"}
    return {"half_spread": FALLBACK_HALF, "source": f"FALLBACK {FALLBACK_HALF:.0%} [assumption; measured rows={n}]"}
