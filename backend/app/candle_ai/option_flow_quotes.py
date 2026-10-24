"""Quote-level (bid/ask mid) put-call-parity features from market_history.db quote_snapshots, shared by the pilot study and the live shadow feed.
Rule: data/research/option_flow/SPEC_BIDASK_PILOT_2026-10-24.md. Everything uses snapshots with received_ts <= the decision time only."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone

import numpy as np

MAX_AGE = 180            # seconds a quote may be older than the decision time
LOOK = 900               # 15 minutes
_MON = "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_expiry(s: str) -> date:
    return date(int(s[-4:]), _MON.index(s[2:5]) + 1, int(s[:2]))


def nearest_expiry(conn: sqlite3.Connection, und: str, day: str) -> str | None:
    """Nearest not-yet-expired weekly expiry captured for `und` on session date `day` (YYYY-MM-DD), as the snapshot string (e.g. 27OCT2026)."""
    ex = [r[0] for r in conn.execute("SELECT DISTINCT expiry FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND expiry IS NOT NULL", (day, und))]
    d0 = date.fromisoformat(day); live = sorted((parse_expiry(e), e) for e in ex if parse_expiry(e) >= d0)
    return live[0][1] if live else None


def quote_at(conn: sqlite3.Connection, und: str, expiry: str, strike: float, otype: str, x_epoch: float, max_age: int = MAX_AGE):
    """Latest usable snapshot (bid > 0, ask > bid) with x - max_age <= received_ts <= x. Returns dict(bid, ask, ltp, oi, age_s) or None."""
    r = conn.execute("SELECT received_ts, bid, ask, ltp, oi FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND received_ts>=? AND received_ts<=? AND bid>0 AND ask>bid ORDER BY received_ts DESC LIMIT 1",
                     (und, expiry, float(strike), otype, _iso(x_epoch - max_age), _iso(x_epoch) + "Z")).fetchone()
    if not r: return None
    ts = datetime.fromisoformat(r[0].replace("Z", "+00:00")).timestamp()
    return {"bid": float(r[1]), "ask": float(r[2]), "ltp": float(r[3]) if r[3] is not None else np.nan, "oi": r[4], "age_s": x_epoch - ts}


def parity_features(conn, und: str, expiry: str, step: int, spot_now: float, spot_prev: float, x_epoch: float) -> dict | None:
    """F3m / F3c / F3z at decision time x (epoch seconds) from quotes; None if a needed quote is missing."""
    K, Kp = round(spot_now / step) * step, round(spot_prev / step) * step
    c1, p1 = quote_at(conn, und, expiry, K, "CE", x_epoch), quote_at(conn, und, expiry, K, "PE", x_epoch)
    c0, p0 = quote_at(conn, und, expiry, Kp, "CE", x_epoch - LOOK), quote_at(conn, und, expiry, Kp, "PE", x_epoch - LOOK)
    if None in (c1, p1, c0, p0): return None
    mid = lambda q: (q["bid"] + q["ask"]) / 2
    d1 = (K + mid(c1) - mid(p1)) - spot_now; d0 = (Kp + mid(c0) - mid(p0)) - spot_prev
    l1 = (K + c1["ltp"] - p1["ltp"]) - spot_now; l0 = (Kp + c0["ltp"] - p0["ltp"]) - spot_prev
    w = (c1["ask"] - c1["bid"]) + (p1["ask"] - p1["bid"])
    return {"K": K, "F3m": (d1 - d0) / spot_now * 1e4, "F3c": (l1 - l0) / spot_now * 1e4, "F3z": (d1 - d0) / w if w > 0 else np.nan, "spread_w": w, "D_mid": d1,
            "c_mid": mid(c1), "p_mid": mid(p1), "quote_age_s": max(c1["age_s"], p1["age_s"], c0["age_s"], p0["age_s"])}
