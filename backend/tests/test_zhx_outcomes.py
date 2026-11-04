import json, os, tempfile
from datetime import datetime, timedelta, timezone
from app.arena.safe_ai import zhx_outcomes as O
IST = timezone(timedelta(hours=5, minutes=30))
def ts(d, h, m): return datetime(2026, 11, d, h, m, tzinfo=IST).timestamp()
def bar(d, h, m, o, hi, lo, c): return {"t": ts(d, h, m), "o": o, "h": hi, "l": lo, "c": c}
SIG = {"side": 1, "bar_time": ts(4, 10, 0), "entry": 100.0, "sl": 98.0, "t1": 102.0, "t2": 104.0, "t3": 106.0, "score": 7}
def test_long_stop_first_and_loss():
    r = O.replay(SIG, [bar(4, 10, 5, 100, 103, 97.9, 99)], ts(4, 11, 0))
    assert r["exit"] == "SL" and r["realised_R"] == -1.0 and not r["t1"]                       # stop wins a shared candle
def test_long_t1_then_stop_banks_leg_one():
    r = O.replay(SIG, [bar(4, 10, 5, 100, 102.5, 100, 102), bar(4, 10, 10, 102, 102.2, 97.5, 98)], ts(4, 11, 0))
    assert r["exit"] == "SL" and r["t1"] and abs(r["realised_R"] - (0.4 * 1.0 + 0.6 * -1.0)) < 1e-9
def test_long_full_target():
    r = O.replay(SIG, [bar(4, 10, 5, 100, 107, 100.5, 106)], ts(4, 11, 0))
    assert r["exit"] == "T3" and abs(r["realised_R"] - (0.4 * 1 + 0.3 * 2 + 0.3 * 3)) < 1e-9
def test_open_trade_stays_pending_until_session_over():
    assert O.replay(SIG, [bar(4, 10, 5, 100, 101, 99.5, 100.5)], ts(4, 11, 0)) is None
    r = O.replay(SIG, [bar(4, 10, 5, 100, 101, 99.5, 100.5), bar(4, 15, 25, 100.5, 101, 100, 101)], ts(4, 15, 45))
    assert r["exit"] == "EOD" and abs(r["realised_R"] - 0.5) < 1e-9                               # last close 101 = +0.5R on the whole position
def test_short_and_next_day_bar_closes_at_last_session_close():
    s = {**SIG, "side": -1, "entry": 100.0, "sl": 102.0, "t1": 98.0, "t2": 96.0, "t3": 94.0}
    r = O.replay(s, [bar(4, 10, 5, 100, 100.5, 97.5, 98.5), bar(4, 15, 25, 98.5, 99, 98, 98.8), bar(5, 9, 15, 98, 99, 97, 98)], ts(5, 9, 30))
    assert r["exit"] == "EOD" and r["t1"] and abs(r["realised_R"] - (0.4 * 1.0 + 0.6 * ((100 - 98.8) / 2))) < 1e-9
def test_resolve_all_is_idempotent_and_summary():
    d = tempfile.mkdtemp(); dp = os.path.join(d, "d.jsonl"); op = os.path.join(d, "o.jsonl")
    open(dp, "w").write(json.dumps({"symbol": "NIFTY", "signal": SIG, "decision": "SKIP"}) + "\n")
    bars = lambda sym, n: [bar(4, 10, 5, 100, 107, 100.5, 106)]
    assert O.resolve_all(ts(4, 11, 0), bars, dp, op)["resolved_now"] == 1 and O.resolve_all(ts(4, 11, 0), bars, dp, op)["resolved_now"] == 0
    s = O.summary(op); assert s["n"] == 1 and s["by_decision"]["SKIP"]["n"] == 1 and s["by_window"]["09:15-10:30"]["n"] == 1
