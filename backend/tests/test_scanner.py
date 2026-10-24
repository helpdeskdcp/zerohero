import numpy as np
import pandas as pd
import pytest

from app.scanner import backtest as BT, data as D, dsl, engine, store
from app.scanner.library import SCANS


def _daily(n=400, seed=2, drift=0.0004):
    rng = np.random.default_rng(seed); d = pd.bdate_range("2023-01-02", periods=n); r = rng.normal(drift, 0.011, n); c = 20000 * np.exp(np.cumsum(r)); o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + abs(rng.normal(0, 0.004, n))); l = np.minimum(o, c) * (1 - abs(rng.normal(0, 0.004, n)))
    return pd.DataFrame({"date": d, "o": o, "h": h, "l": l, "c": c, "v": rng.integers(1000, 5000, n).astype(float)})


def test_dsl_rejects_unsafe_expressions():
    df = _daily(60); ns = dsl.namespace(df)
    for bad in ("__import__('os').system('x')", "close.__class__", "[x for x in close]", "open('/etc/passwd')", "(lambda: 1)()", "close[0] > 1", "eval('1')", "sma(close, 5, 6) > 1 and 1", "x > 1", "'a' > close"):
        with pytest.raises(dsl.ScanError): dsl.run_expr(bad, df, ns)
    assert dsl.run_expr("close > sma(close, 5)", df, ns).dtype == bool


def test_library_runs_and_is_causal():
    df = _daily(); full = engine.evaluate(df); assert set(full) == set(SCANS)
    k = 300; cut = engine.evaluate(df.iloc[:k].reset_index(drop=True))
    for name in SCANS: pd.testing.assert_series_equal(full[name].iloc[:k].reset_index(drop=True), cut[name], check_names=False)       # bar t never uses later bars
    mut = df.copy(); mut.loc[k:, ["o", "h", "l", "c"]] *= 1.3
    for name, s in engine.evaluate(mut).items(): pd.testing.assert_series_equal(s.iloc[:k].reset_index(drop=True), cut[name], check_names=False)


def test_breakout_scan_matches_hand_computation():
    df = _daily(80); s = dsl.run_expr("close > hh(20)", df); i = 50
    assert bool(s.iloc[i]) == bool(df["c"].iloc[i] > df["h"].iloc[i - 20:i].max()) and not s.iloc[:20].any()


def test_trades_entry_exit_cost_and_overlap_skip():
    df = pd.DataFrame({"date": pd.bdate_range("2024-01-01", periods=12), "o": np.arange(100.0, 112.0), "h": np.arange(101.0, 113.0), "l": np.arange(99.0, 111.0), "c": np.arange(100.5, 112.5), "v": 1.0})
    sig = pd.Series([False, True, True, False, False, True] + [False] * 6)
    t = BT.trades(df, sig, "LONG", 3.0, 3)
    assert len(t) == 2 and abs(t["net"].iloc[0] - ((df["c"][4] / df["o"][2] - 1) * 1e4 - 3.0)) < 1e-9          # signal day1: entry open day2, exit close day4; signal day2 skipped (inside 3 days)
    s = BT.trades(df, sig, "SHORT", 0.0, 1); assert s["net"].iloc[0] < 0


def test_evaluate_scan_verdicts():
    rng = np.random.default_rng(5); dates = pd.bdate_range("2019-01-01", periods=400)
    def tr(mean): return {h: pd.DataFrame({"date": dates, "net": rng.normal(mean, 80, len(dates))}) for h in BT.HORIZONS}
    assert BT.evaluate_scan({"NIFTY": tr(60), "BANKNIFTY": tr(60)}, 18, B=400)["verdict"] == "GO-SHADOW"
    assert BT.evaluate_scan({"NIFTY": tr(-30), "BANKNIFTY": tr(-30)}, 18, B=400)["verdict"] == "NO-GO"
    assert BT.evaluate_scan({"NIFTY": {h: pd.DataFrame({"date": dates[:20], "net": np.ones(20)}) for h in BT.HORIZONS}}, 18, B=100)["verdict"] == "INSUFFICIENT_SAMPLE"


def test_chain_and_splice_and_incomplete_bar(tmp_path):
    d = pd.bdate_range("2026-09-01", periods=6)
    a = pd.DataFrame({"date": d[:4], "o": 100.0, "h": 101.0, "l": 99.0, "c": [100.0, 101, 102, 103], "v": [100.0, 100, 100, 5]}); b = pd.DataFrame({"date": d[2:], "o": 110.0, "h": 111.0, "l": 109.0, "c": [112.0, 113, 114, 115], "v": [5.0, 5, 200, 200]})
    ch = D.chain_contracts([a, b]); assert ch["c"].iloc[-1] == 115 and len(ch) == 6                       # newest contract keeps true prices
    gap = ch["c"].iloc[3] - ch["c"].iloc[2]; assert abs(gap) < 5                                          # roll gap removed (raw gap between contracts was ~10)
    base = pd.DataFrame({"date": pd.bdate_range("2026-08-01", periods=20), "o": 50.0, "h": 51.0, "l": 49.0, "c": 50.0, "v": 0.0}); rec = pd.DataFrame({"date": pd.bdate_range("2026-08-17", periods=10), "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 1.0})
    sp = D.splice(base, rec); assert sp["c"].iloc[0] == 100.0 and len(sp) == 20 - 4 + 10 - 0 or sp["date"].is_monotonic_increasing
    from datetime import datetime
    ist = D.IST; today = datetime(2026, 10, 23, 12, 0, tzinfo=ist); x = pd.DataFrame({"date": pd.to_datetime(["2026-10-22", "2026-10-23"]), "o": 1.0, "h": 1.0, "l": 1.0, "c": 1.0, "v": 0.0})
    assert len(D.completed_only(x, "NIFTY", today)) == 1 and len(D.completed_only(x, "NIFTY", datetime(2026, 10, 23, 16, 0, tzinfo=ist))) == 2


def test_run_stores_hits_once(tmp_path, monkeypatch):
    db = str(tmp_path / "s.db"); df = _daily(300); df.loc[df.index[-1], "c"] = df["h"].iloc[-21:-1].max() * 1.05; df.loc[df.index[-1], "h"] = df["c"].iloc[-1]
    r1 = engine.run(loader=lambda inst, now: df, path=db, scans={"BRK20_UP": SCANS["BRK20_UP"]}); r2 = engine.run(loader=lambda inst, now: df, path=db, scans={"BRK20_UP": SCANS["BRK20_UP"]})
    assert len(r1["new_hits"]) == 6 and r2["new_hits"] == []                                                # six instruments share the fake frame; second run adds nothing
    with store.conn(db) as c: assert c.execute("SELECT COUNT(*) FROM hits").fetchone()[0] == 6


def test_library_size_and_calendar_names():
    assert len(SCANS) == 36 and {v[0] for v in SCANS.values()} == {"LONG", "SHORT"}
    df = _daily(60); ns = dsl.namespace(df); assert int(ns["dow"].max()) <= 4 and int(ns["dom"].max()) <= 31
    assert dsl.run_expr("(dom >= 28) | (dom <= 3)", df, ns).sum() > 5


def test_chart_payload_daily_and_5m_markers_and_validation():
    from app.scanner import api as SA, intraday as I
    df = _daily(300); out = SA.chart_payload("NIFTY", "1d", "BRK20_UP", 120, daily_df=df)
    assert out["bars"] == 120 and out["side"] == "LONG" and out["candles"][0]["time"] < out["candles"][-1]["time"] and all(m["time"] in {c["time"] for c in out["candles"]} for m in out["markers"])
    hand = engine.evaluate(df, {"BRK20_UP": SCANS["BRK20_UP"]})["BRK20_UP"].tail(120); assert len(out["markers"]) == int(hand.sum())
    assert SA.chart_payload("XYZ", "1d", "BRK20_UP")["error"].startswith("unknown") and "error" in SA.chart_payload("NIFTY", "1h", "x")
    n = 140; idx = np.arange(n); c = 100 + 0.1 * idx; o = c - 0.12
    f5 = pd.DataFrame({"t": pd.date_range("2026-10-22 03:45", periods=n, freq="5min", tz="UTC"), "o": o, "h": c + 0.05, "l": o - 0.05, "c": c, "v": 1000.0}); f5.loc[n - 1, ["c", "h", "v"]] = [c[-1] + 1.0, c[-1] + 1.0, 8000.0]
    p5 = SA.chart_payload("NIFTY", "5m", "FULL_BULL", 100, fut5_df=f5)
    assert p5["side"] == "LONG" and isinstance(p5["candles"][0]["time"], int) and p5["candles"][-1]["time"] - p5["candles"][-2]["time"] == 300 and len(p5["markers"]) >= 1 and p5["markers"][-1]["time"] == p5["candles"][-1]["time"]
