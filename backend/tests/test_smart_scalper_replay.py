"""
SLICE 5 — strict-causal historical replay / backtest.

Offline: a tiny synthetic market_history.db (2 tables) is built in a temp dir and
`historical_context._HIST_DB` is pointed at it. The replay reuses the real
MathematicalConfluenceEngine, but the trade-lifecycle test swaps in a stub
engine that forces BUY_CE so entries/exits/metrics are exercised deterministically.

Nothing here touches the broker or ai_paper_trades.
"""
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.smart_index_scalper import historical_context as hc
from app.smart_index_scalper import replay_metrics as rm
from app.smart_index_scalper import replay_price_action as pa
from app.smart_index_scalper.replay import SmartScalperReplay

UTC = timezone.utc
_CANDLE_DDL = """CREATE TABLE market_candles (
  instrument_key TEXT, symbol TEXT, kind TEXT, exchange TEXT, token TEXT,
  expiry TEXT, strike REAL, option_type TEXT, tf TEXT, bar_start TEXT,
  session_date_ist TEXT, o REAL, h REAL, l REAL, c REAL, v REAL, oi REAL,
  oi_change REAL, received_ts TEXT, source TEXT)"""
_QUOTE_DDL = """CREATE TABLE quote_snapshots (
  instrument_key TEXT, symbol TEXT, kind TEXT, exchange TEXT, token TEXT,
  expiry TEXT, strike REAL, option_type TEXT, session_date_ist TEXT,
  received_ts TEXT, snap_key TEXT, ltp REAL, oi REAL, volume REAL, oi_change REAL,
  source TEXT)"""


def _iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _build_db(path, *, with_chain=True, symbol="NIFTY", day="2026-09-03"):
    c = sqlite3.connect(path)
    c.executescript("DROP TABLE IF EXISTS market_candles; DROP TABLE IF EXISTS quote_snapshots; "
                    + _CANDLE_DDL + ";" + _QUOTE_DDL)
    base = datetime(2026, 9, 3, 3, 45, tzinfo=UTC)
    prev = "2026-09-02"
    # previous session: 3 daily-ish 5m bars so prev_day agg = H 100 / L 90 / C 95
    for i, (o, h, l, cl) in enumerate([(92, 100, 91, 96), (96, 99, 90, 94), (94, 98, 92, 95)]):
        ts = _iso(datetime(2026, 9, 2, 4, tzinfo=UTC) + timedelta(minutes=5 * i))
        c.execute("INSERT INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("NSE:1", symbol, "INDEX", "NSE", "1", None, None, None, "5m", ts,
                   prev, o, h, l, cl, 1000, None, None, ts, "T"))
    # today: 40 five-minute bars, gently trending up through the prev-day high
    px = 95.0
    for i in range(40):
        ts = _iso(base + timedelta(minutes=5 * i))
        o = px
        px += 0.4
        row = (o, px + 0.3, o - 0.3, px, 1200 + i)
        c.execute("INSERT INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("NSE:1", symbol, "INDEX", "NSE", "1", None, None, None, "5m", ts,
                   day, *row, None, None, ts, "T"))
        c.execute("INSERT INTO market_candles VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("NSE:1", symbol, "INDEX", "NSE", "1", None, None, None, "1m", ts,
                   day, *row, None, None, ts, "T"))
    if with_chain:
        strikes = [90, 95, 100, 105, 110, 115]
        for i in range(45):
            ts = _iso(base + timedelta(minutes=4 * i))
            for k in strikes:
                for side, ltp, oi in (("CE", max(1.0, 8 - abs(k - 103) * 0.5 + i * 0.05), 500000 + i * 1000 + k),
                                      ("PE", max(1.0, 8 - abs(k - 103) * 0.5 + i * 0.03), 400000 + i * 900 + k)):
                    c.execute("INSERT INTO quote_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                              (f"NFO:{k}", symbol, "OPTION", "NFO", str(k), "08SEP2026", float(k), side,
                               day, ts, ts, round(ltp, 2), float(oi), 10000.0 + i * 100, None, "T"))
    c.commit()
    c.close()


# --------------------------------------------------------------- causality
def test_historical_context_is_strictly_causal(monkeypatch):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mh.db")
    _build_db(p)
    monkeypatch.setattr(hc, "_HIST_DB", p)

    sd = hc.SessionData("NIFTY", "2026-09-03", "08SEP2026")
    assert sd.prev_day["high"] == 100 and sd.prev_day["low"] == 90 and sd.prev_day["close"] == 95

    T = datetime(2026, 9, 3, 5, 0, tzinfo=UTC)          # ~75 min into the session
    ctx = sd.context_at(T)
    # no bar that closes after T may appear
    for b in ctx["bars"]:
        assert hc._dt(b["bar_start"]) + timedelta(minutes=5) <= T
    # day_high is only over the visible bars, strictly less than the full-session high
    full = sd.context_at(datetime(2026, 9, 3, 9, 30, tzinfo=UTC))
    assert ctx["day_high"] < full["day_high"]
    # chain marks never look ahead
    later = sd.context_at(datetime(2026, 9, 3, 6, 0, tzinfo=UTC))
    a = next(r for r in ctx["chain"] if r["strike"] == 105.0)
    b = next(r for r in later["chain"] if r["strike"] == 105.0)
    assert b["ce_ltp"] >= a["ce_ltp"]                   # premium rises over time in the fixture
    # greeks are never fabricated
    assert a["ce_greeks_source"] == "UNAVAILABLE" and a["ce_delta"] is None


def test_available_sessions_needs_both_candles_and_a_chain(monkeypatch):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mh.db")
    _build_db(p, with_chain=False)
    monkeypatch.setattr(hc, "_HIST_DB", p)
    assert hc.available_sessions("NIFTY") == []          # candles but no option chain
    _build_db(p)                                          # rebuild WITH a chain
    ss = hc.available_sessions("NIFTY")
    assert len(ss) == 1 and ss[0]["session_date"] == "2026-09-03"


# --------------------------------------------------------------- trade lifecycle
class _StubEngine:
    """Forces a directional BUY_CE with a clean plan so the replay opens a sim
    position; flips to NO_TRADE late so an exit path is taken too."""
    def evaluate(self, **kw):
        spot = kw.get("current_price")
        if spot is None or not kw.get("chain"):
            return {"status": "DATA_INSUFFICIENT", "signal_type": "NO_TRADE", "missing": ["x"]}
        as_of = kw.get("timestamp") or ""
        late = as_of >= "2026-09-03T06:30"
        return {
            "engine": "STUB", "status": "OK", "spot": spot,
            "direction": "NONE" if late else "CE",
            "signal_type": "NO_TRADE" if late else "BUY_CE",
            "confidence": 40 if late else 82,
            "confluence_score": 70,
            "risk_reward": [2.0, 3.2, 4.1],
            "stop_loss": spot - 6, "target_1": spot + 12, "target_2": spot + 20,
            "market_regime": "BREAKOUT_ATTEMPT",
            "nearest_support": {"center": spot - 6, "evidence_count": 4, "distance_pct": 0.06},
            "nearest_resistance": {"center": spot + 12, "evidence_count": 3, "distance_pct": 0.12},
            "reason_codes": ["nearest zone strength 71 (4 families)", "PUT support wall @ 95",
                             "volume 1.5x recent average", "candle structure: hammer"],
            "no_trade_reason": "stub flip" if late else None,
        }


def test_replay_opens_and_closes_sim_trades_and_gates_sample(monkeypatch):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "mh.db")
    _build_db(p)
    monkeypatch.setattr(hc, "_HIST_DB", p)

    r = SmartScalperReplay(engine=_StubEngine(),
                           filters={"min_rr1": 0.5, "min_confidence": 30, "min_confluence_evidence": 1})
    out = r.run("NIFTY", step_min=5, profiles=["AGGRESSIVE"], warmup_min=20, max_hold_min=30,
                profile_overrides={"min_confidence": 30, "min_selection_score": 0, "min_rr1": 0.5})
    assert out["params"]["gate_mode"].startswith("DIAGNOSTIC_SWEEP")

    assert out["live_trading"] is False
    assert out["status"] == "INSUFFICIENT_SAMPLE"        # one synthetic session
    assert out["data_source"].startswith("HISTORICAL_REPLAY")
    tr = out["trades"]
    assert len(tr) >= 1
    t0 = tr[0]
    assert t0["option_type"] == "CE" and t0["sim"] is True
    assert t0["exit_reason"] in ("STOP", "TARGET_2", "MAX_HOLD", "SESSION_END",
                                 "SM_STOPPED", "SM_INVALIDATED", "SM_EXIT_WARNING")
    assert t0["entry"] is not None and t0["exit"] is not None
    assert set(("pnl", "r_multiple", "mfe", "mae", "hold_min")).issubset(t0)
    m = out["metrics"]["overall"]
    assert m["n"] == len(tr) and m.get("descriptive_only") is True
    assert "BREAKOUT_ATTEMPT" in out["metrics"]["by_market_regime"]
    assert out["calibration"]["status"] == "INSUFFICIENT_SAMPLE"
    assert "no profitability claim" in out["note"].lower()


def test_replay_writes_nothing_to_paper_trades(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CHANAKYA_DB_PATH", os.path.join(d, "app.db"))
    import importlib
    import app.db as db
    importlib.reload(db)
    db.init_db()
    n0 = len(db.list_trades(limit=9999))

    p = os.path.join(d, "mh.db")
    _build_db(p)
    monkeypatch.setattr(hc, "_HIST_DB", p)
    SmartScalperReplay(engine=_StubEngine(),
                       filters={"min_rr1": 0.5, "min_confidence": 30}).run(
        "NIFTY", step_min=5, profiles=["AGGRESSIVE"])
    assert len(db.list_trades(limit=9999)) == n0        # replay never persists a trade


def test_replay_module_has_no_order_path():
    src = Path(__file__).parents[1] / "app" / "smart_index_scalper"
    joined = "\n".join(p.read_text() for p in src.glob("replay*.py") if p.name.startswith("replay") or p.name == "historical_context.py")
    for banned in ("place_order", "placeOrder", "OrderManager", "open_trade(", "close_trade(",
                   "update_trade_price", "live_trading=true", "live_trading = true"):
        assert banned not in joined, banned


# --------------------------------------------------------------- metrics math
def test_replay_metrics_math():
    trades = [
        {"pnl": 10, "r_multiple": 2.0, "mfe": 12, "mae": -2, "hold_min": 8, "exit_reason": "TARGET_2",
         "confidence": 82, "profile": "BALANCED", "symbol": "NIFTY", "market_regime": "BULLISH_EXPANSION"},
        {"pnl": -5, "r_multiple": -1.0, "mfe": 3, "mae": -5, "hold_min": 12, "exit_reason": "STOP",
         "confidence": 74, "profile": "BALANCED", "symbol": "NIFTY", "market_regime": "RANGE"},
        {"pnl": 6, "r_multiple": 1.2, "mfe": 7, "mae": -1, "hold_min": 5, "exit_reason": "TARGET_2",
         "confidence": 66, "profile": "BALANCED", "symbol": "NIFTY", "market_regime": "RANGE"},
        {"pnl": -8, "r_multiple": -1.6, "mfe": 1, "mae": -8, "hold_min": 20, "exit_reason": "STOP",
         "confidence": 78, "profile": "BALANCED", "symbol": "NIFTY", "market_regime": "REVERSAL"},
    ]
    m = rm.trade_metrics(trades)
    assert m["n"] == 4 and m["wins"] == 2 and m["losses"] == 2 and m["win_rate"] == 0.5
    assert m["gross_profit"] == 16 and m["gross_loss"] == 13
    assert m["profit_factor"] == round(16 / 13, 3)
    assert m["expectancy"] == round(3 / 4, 4)
    # equity path 10, 5, 11, 3 -> peak 11 -> max drawdown -8
    assert m["max_drawdown"] == -8.0
    assert m["exit_reason_mix"] == {"TARGET_2": 2, "STOP": 2}

    s = rm.summarize(trades, session_keys={"a", "b"})
    assert s["status"] == "INSUFFICIENT_SAMPLE"
    assert s["overall"]["descriptive_only"] is True
    assert s["calibration"]["status"] == "INSUFFICIENT_SAMPLE"
    # with the gate met, a reliability table appears
    big = trades * 6
    s2 = rm.summarize(big, session_keys=set("abcdefgh"))
    assert s2["status"] == "OK"
    assert "buckets" in s2["calibration"] and s2["calibration"]["ece"] is not None


# --------------------------------------------------------------- price action
def test_price_action_is_causal_and_safe_on_short_input():
    assert pa.derive([], pdh=100, pdl=90, day_high=99, day_low=91) == {
        "breakout_state": None, "retest_state": None, "reversal_candidate": False, "candle_signals": []}
    # a clean breakout of the prior-day high
    bars = [{"open": 95, "high": 96, "low": 94, "close": 95.5},
            {"open": 95.5, "high": 97, "low": 95, "close": 96.5},
            {"open": 96.5, "high": 99, "low": 96, "close": 98},
            {"open": 98, "high": 103, "low": 97.5, "close": 102}]
    d = pa.derive(bars, pdh=100, pdl=90, day_high=103, day_low=94)
    assert d["breakout_state"] == "BREAKOUT_CONFIRMED"
    # derive only reads the bars it is handed (no DB / no clock)
    import inspect
    srctxt = inspect.getsource(pa)
    assert "import sqlite3" not in srctxt and "datetime.now" not in srctxt
