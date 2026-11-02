

def test_entry_features_are_actually_produced_for_a_live_trade_regression_of_the_trial_week():
    """0 of 10 trial-week paper trades had stored features: the current trade's exit_ts was a tz-naive NaT compared with tz-aware timestamps (TypeError swallowed at debug level)."""
    import sqlite3

    import numpy as np
    import pandas as pd

    from app.zone_breakout import live as L
    rng = np.random.default_rng(3); rows = []
    for d in pd.bdate_range("2026-10-26", periods=4, tz="Asia/Kolkata"):
        ts = pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=75, freq="5min"); p = 100 + np.cumsum(rng.normal(0, .3, 75)) + 0
        rows += [dict(timestamp=t, open=x, high=x + .3, low=x - .3, close=x + .1, volume=1000.0) for t, x in zip(ts, p)]
    d = pd.DataFrame(rows); cfg = L.ZB.Config(); di = L.ZB.indicators(d, cfg); i = len(di) - 10
    c = sqlite3.connect(":memory:"); c.execute("CREATE TABLE trades (symbol, side, entry, entry_ts, exit_ts, reason, status)")
    c.execute("INSERT INTO trades VALUES ('X','BUY',100.0,?,?, 'sl','CLOSED')", (str(di['timestamp'].iloc[i - 20]), str(di['timestamp'].iloc[i - 15])))      # an earlier closed trade of the same session
    out = L._entry_features(c, "X", di, i, di["timestamp"].iloc[i], "BUY", float(di["close"].iloc[i]), float(di["close"].iloc[i]) - 0.5)
    assert out is not None and "adv_wick" in out
