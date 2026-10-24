import sqlite3
from datetime import datetime, timezone

import numpy as np

from app.candle_ai import option_flow_quotes as Q

T = datetime(2026, 10, 22, 5, 0, 0, tzinfo=timezone.utc).timestamp()


def _db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE quote_snapshots (received_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT, bid REAL, ask REAL, ltp REAL, oi REAL)")
    def put(off, strike, ot, bid, ask, ltp, exp="27OCT2026", day="2026-10-22"):
        ts = datetime.fromtimestamp(T + off, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
        c.execute("INSERT INTO quote_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", (ts, "NIFTY", "OPTION", exp, strike, ot, day, bid, ask, ltp, 1000.0))
    return c, put


def test_nearest_expiry_and_quote_staleness_and_future_rows():
    c, put = _db(); put(-10, 22600, "CE", 100, 101, 100.5); put(-5, 22600, "PE", 80, 81, 80.5, exp="30OCT2026"); put(-5, 22600, "PE", 80, 81, 80.5, exp="21OCT2026")
    assert Q.nearest_expiry(c, "NIFTY", "2026-10-22") == "27OCT2026" or Q.nearest_expiry(c, "NIFTY", "2026-10-22") == "30OCT2026"           # 21OCT already expired
    assert Q.nearest_expiry(c, "NIFTY", "2026-10-22") == "27OCT2026"
    q = Q.quote_at(c, "NIFTY", "27OCT2026", 22600, "CE", T); assert q["bid"] == 100 and abs(q["age_s"] - 10) < 1e-6
    put(+30, 22600, "CE", 150, 151, 150.5)                                                         # a snapshot AFTER the decision time must never be used
    assert Q.quote_at(c, "NIFTY", "27OCT2026", 22600, "CE", T)["bid"] == 100
    assert Q.quote_at(c, "NIFTY", "27OCT2026", 22600, "CE", T + 500) is None                       # newer rows exist but the last usable one is >180 s old
    put(-3, 22600, "PE", 0, 0, 80.5); assert Q.quote_at(c, "NIFTY", "27OCT2026", 22600, "PE", T) is None or Q.quote_at(c, "NIFTY", "27OCT2026", 22600, "PE", T)["bid"] != 0


def test_parity_features_value_and_missing():
    c, put = _db(); K = 22600; S1, S0 = 22610.0, 22600.0
    for off, ce, pe in ((-900, (100, 102), (90, 92)), (0, (111, 113), (85, 87))):
        put(off - 20, K, "CE", *ce, sum(ce) / 2); put(off - 20, K, "PE", *pe, sum(pe) / 2)
    f = Q.parity_features(c, "NIFTY", "27OCT2026", 50, S1, S0, T)
    d1 = (K + 112 - 86) - S1; d0 = (K + 101 - 91) - S0
    assert abs(f["F3m"] - (d1 - d0) / S1 * 1e4) < 1e-9 and abs(f["spread_w"] - 4.0) < 1e-9 and abs(f["F3z"] - (d1 - d0) / 4.0) < 1e-9
    assert Q.parity_features(c, "NIFTY", "27OCT2026", 50, 22700.0, S0, T) is None                  # ATM moved to a strike with no quotes -> None, not a guess
