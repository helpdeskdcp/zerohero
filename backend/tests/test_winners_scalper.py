import numpy as np, pandas as pd
from app.winners_scalper import strategy as WS, backtest as WB


def _bars(n=1500, seed=3):
    rng = np.random.default_rng(seed); ts = []; day = pd.Timestamp("2024-01-01 09:15", tz="Asia/Kolkata")
    while len(ts) < n:
        if day.weekday() < 5: ts += [day + pd.Timedelta(minutes=5 * k) for k in range(75)]
        day += pd.Timedelta(days=1)
    ts = ts[:n]; c = 20000 + np.cumsum(rng.normal(0, 10, n) + 0.8 * np.sin(np.arange(n) / 40)); o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) + abs(rng.normal(0, 4, n)); l = np.minimum(o, c) - abs(rng.normal(0, 4, n))
    return pd.DataFrame(dict(timestamp=ts, open=o, high=h, low=l, close=c, volume=1.0))


def test_atr_and_dmi_close_to_talib():
    import talib
    b = _bars(); h, l, c = (b[k].to_numpy(float) for k in ("high", "low", "close"))
    a = WS.atr_series(h, l, c, 14); ta = talib.ATR(h, l, c, 14); assert np.nanmax(abs(a[200:] - ta[200:])) < 1e-6
    p, m, adx = WS.dmi(h, l, c, 14); assert np.nanmax(abs(p[300:] - talib.PLUS_DI(h, l, c, 14)[300:])) < 1e-3 and np.nanmax(abs(m[300:] - talib.MINUS_DI(h, l, c, 14)[300:])) < 1e-3
    assert np.nanmax(abs(adx[400:] - talib.ADX(h, l, c, 14)[400:])) < 1.0


def test_no_signal_before_first_flip_and_signals_exist():
    s = WS.signals(_bars()); first_flip = int(np.argmax(s["dir"].values != s["dir"].shift(1).fillna(1).values))
    assert not (s["buy"] | s["sell"]).iloc[:first_flip].any() and (s["buy"] | s["sell"]).sum() >= 5


def test_signals_are_causal():
    b = _bars(); full = WS.signals(b); cut = 900; b2 = b.copy(); b2.loc[cut + 1:, ["open", "high", "low", "close"]] = np.random.default_rng(1).normal(30000, 900, (len(b) - cut - 1, 4))
    part = WS.signals(b2); cols = ["buy", "sell", "dir", "long_stop", "short_stop", "strong"]
    pd.testing.assert_frame_equal(full.loc[:cut, cols].reset_index(drop=True), part.loc[:cut, cols].reset_index(drop=True), check_exact=False, atol=1e-9)


def test_backtest_runs_both_variants_and_stays_intraday():
    b = _bars(3000)
    for v in ("X1", "X2"):
        t = WB.simulate(b, v); assert len(t) > 5 and (t["risk_bp"] > 0).all()
        assert (pd.DatetimeIndex(t["entry_ts"]).tz_convert("Asia/Kolkata").hour * 60 + pd.DatetimeIndex(t["entry_ts"]).tz_convert("Asia/Kolkata").minute < 15 * 60 + 15).all()


def test_live_load_bars_splices_a_contract_roll(tmp_path):
    import sqlite3
    from app.winners_scalper import live
    p = str(tmp_path / "mh.db"); c = sqlite3.connect(p)
    c.execute("CREATE TABLE market_candles (id INTEGER PRIMARY KEY, symbol TEXT, kind TEXT, tf TEXT, bar_start TEXT, session_date_ist TEXT, expiry TEXT, o REAL, h REAL, l REAL, c REAL, v REAL)")
    t0 = pd.Timestamp("2026-10-20 04:00", tz="UTC")
    for i in range(120): c.execute("INSERT INTO market_candles (symbol,kind,tf,bar_start,session_date_ist,expiry,o,h,l,c,v) VALUES ('NG','FUTURE','1m',?,?,?,?,?,?,?,1)", ((t0 + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-10-20", "27OCT2026", 250.0, 250.5, 249.5, 250.0))
    for i in range(120, 240): c.execute("INSERT INTO market_candles (symbol,kind,tf,bar_start,session_date_ist,expiry,o,h,l,c,v) VALUES ('NG','FUTURE','1m',?,?,?,?,?,?,?,1)", ((t0 + pd.Timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "2026-10-20", "2026-11-25", 262.0, 262.5, 261.5, 262.0))
    c.commit(); c.close(); b = live.load_bars("NG", (t0 + pd.Timedelta(hours=6)).timestamp(), path=p)
    assert len(b) >= 14 and b["close"].diff().abs().max() < 1.0                                                                       # the 12-point roll gap is gone
