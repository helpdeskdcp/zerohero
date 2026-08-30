"""P0 replay/backtest harness — no-look-ahead, contract-lock, exit simulation.
Hermetic: builds a tiny oi_history-schema DB per test; also uses the fresh_db
fixture so scalp_signals rows are written to a throwaway canonical DB."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.backtest import oi_history_adapter as ad
from app.backtest import replay as rp

_DDL = """
CREATE TABLE cycles (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, ts TEXT, date TEXT,
  time TEXT, underlying_ltp REAL, atm INTEGER, pcr REAL, max_pain INTEGER, bias TEXT, note TEXT,
  signal_action TEXT, signal_strike INTEGER, signal_direction TEXT, signal_entry REAL,
  signal_target REAL, signal_sl REAL, signal_confidence INTEGER, signal_tradeable INTEGER);
CREATE TABLE strikes (cycle_id INTEGER, strike INTEGER, ce_oi INTEGER, ce_oi_chg INTEGER,
  ce_vol INTEGER, ce_ltp REAL, ce_chg_pct REAL, ce_signal TEXT, pe_oi INTEGER, pe_oi_chg INTEGER,
  pe_vol INTEGER, pe_ltp REAL, pe_chg_pct REAL, pe_signal TEXT, ce_iv REAL, pe_iv REAL,
  ce_delta REAL, ce_gamma REAL, ce_theta REAL, ce_vega REAL, pe_delta REAL, pe_gamma REAL,
  pe_theta REAL, pe_vega REAL, ce_trading_symbol TEXT, ce_token TEXT, pe_trading_symbol TEXT,
  pe_token TEXT, ce_contract_expiry TEXT, pe_contract_expiry TEXT);
"""


def _mkdb(tmp_path, monkeypatch, rows, symbol="NIFTY", date="2026-08-27"):
    """rows: list of (hhmm, index_ltp, atm, {strike: (ce_ltp, pe_ltp, expiry)})"""
    p = tmp_path / "oi_history.db"
    c = sqlite3.connect(p)
    c.executescript(_DDL)
    for i, (hhmm, ltp, atm, chain) in enumerate(rows, 1):
        ts = f"{date}T{hhmm}:00"
        c.execute("INSERT INTO cycles (id,symbol,ts,date,time,underlying_ltp,atm,pcr,max_pain,bias)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)", (i, symbol, ts, date, hhmm, ltp, atm, 1.0, atm, "NEUTRAL"))
        for strike, (ce, pe, exp) in chain.items():
            c.execute("INSERT INTO strikes (cycle_id,strike,ce_oi,ce_oi_chg,ce_vol,ce_ltp,ce_chg_pct,"
                      "pe_oi,pe_oi_chg,pe_vol,pe_ltp,ce_token,ce_contract_expiry,pe_token,pe_contract_expiry)"
                      " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (i, strike, 1000, 0, 100 * i, ce, 0.0, 1000, 0, 100 * i, pe,
                       f"T{strike}CE", exp, f"T{strike}PE", exp))
    c.commit(); c.close()
    monkeypatch.setenv("OI_HISTORY_DB", str(p))
    return p


def _enter_once(at_hhmm, decision="BUY_CE", **plan):
    """decide() that returns one entry at a given bar, else NO_TRADE."""
    fired = {"done": False}
    base = dict(strike=24000, stop_loss=90.0, target_1=110.0, target_2=None,
                trailing_stop=0.0, max_hold_sec=None, signal_type="RESISTANCE_BREAKOUT",
                direction="BULLISH", reason="test")
    base.update(plan)

    def decide(state, ctx):
        if not fired["done"] and state["ts"][11:16] == at_hhmm:
            fired["done"] = True
            return {**base, "decision": decision, "entry": state["chain"][0][
                "ce" if decision == "BUY_CE" else "pe"]["ltp"]}
        return {"decision": "NO_TRADE"}
    return decide


CH = lambda ce, pe, exp="2026-09-03": {24000: (ce, pe, exp)}


def test_target_exit_and_signal_row(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24010, 24000, CH(105.0, 76.0)),
        ("09:18", 24025, 24000, CH(115.0, 70.0)),   # CE >= t1 110 -> TARGET
        ("09:19", 24030, 24000, CH(120.0, 66.0)),
    ])
    res = rp.run_replay("NIFTY", _enter_once("09:17"), start="2026-08-27", end="2026-08-27")
    assert res.entries == 1 and len(res.trades) == 1
    t = res.trades[0]
    assert t.exit_reason == "TARGET" and t.entry == 105.0 and t.exit_price == 115.0
    assert t.points == 10.0 and t.outcome == "WIN"
    # persisted + graded
    rows = fresh_db.list_scalp_signals(source="BACKTEST")
    assert len(rows) == 1 and rows[0]["status"] == "CLOSED" and rows[0]["outcome"] == "WIN"
    assert rows[0]["opt_type"] == "CE" and rows[0]["opt_strike"] == 24000
    assert rows[0]["opt_expiry"] == "2026-09-03" and rows[0]["opt_token"] == "T24000CE"
    assert rows[0]["signal_id"].startswith("SCS-")


def test_stop_exit(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24000, 24000, CH(100.0, 80.0)),
        ("09:18", 23980, 24000, CH(85.0, 95.0)),    # CE <= sl 90 -> STOP
    ])
    res = rp.run_replay("NIFTY", _enter_once("09:17", target_1=200.0),
                        start="2026-08-27", end="2026-08-27")
    t = res.trades[0]
    assert t.exit_reason == "STOP" and t.points == -15.0 and t.outcome == "LOSS"


def test_trailing_stop_ratchets_and_exits(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24000, 24000, CH(100.0, 80.0)),
        ("09:18", 24040, 24000, CH(140.0, 60.0)),   # peak 140 -> cur_sl 130
        ("09:19", 24020, 24000, CH(128.0, 66.0)),   # 128 <= 130 -> TRAIL
    ])
    res = rp.run_replay("NIFTY", _enter_once("09:17", target_1=500.0, trailing_stop=10.0),
                        start="2026-08-27", end="2026-08-27")
    t = res.trades[0]
    assert t.exit_reason == "TRAIL" and t.exit_price == 128.0 and t.points == 28.0


def test_time_exit(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:20", 24000, 24000, CH(101.0, 80.0)),   # +240s >= max_hold 120 -> TIME
    ])
    res = rp.run_replay("NIFTY", _enter_once("09:16", target_1=999.0, max_hold_sec=120),
                        start="2026-08-27", end="2026-08-27")
    t = res.trades[0]
    assert t.exit_reason == "TIME" and t.exit_price == 101.0


def test_session_close_flatten(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("15:25", 24000, 24000, CH(100.0, 80.0)),
        ("15:29", 24005, 24000, CH(103.0, 78.0)),
        ("15:36", 24010, 24000, CH(108.0, 74.0)),   # after 15:30 -> SESSION_CLOSE
    ])
    res = rp.run_replay("NIFTY", _enter_once("15:25", target_1=999.0),
                        start="2026-08-27", end="2026-08-27")
    t = res.trades[0]
    assert t.exit_reason == "SESSION_CLOSE" and t.exit_price == 103.0   # last IN-SESSION mark


def test_contract_rollover_never_cross_prices(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0, "2026-09-03")),
        ("09:17", 24000, 24000, CH(102.0, 79.0, "2026-09-03")),
        ("09:18", 24000, 24000, CH(300.0, 10.0, "2026-09-10")),  # DIFFERENT expiry
    ])
    res = rp.run_replay("NIFTY", _enter_once("09:16"), start="2026-08-27", end="2026-08-27")
    t = res.trades[0]
    assert t.exit_reason == "CONTRACT_ROLLOVER"
    assert t.exit_price == 102.0                      # last mark on the ORIGINAL contract, not 300


def test_contract_disappears_holds_then_session_close(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("14:00", 24000, 24000, CH(100.0, 80.0)),
        ("14:01", 24000, 24000, CH(104.0, 78.0)),
        ("14:02", 24050, 24050, {24050: (60.0, 60.0, "2026-09-03")}),  # 24000 gone
        ("15:40", 24050, 24050, {24050: (61.0, 60.0, "2026-09-03")}),  # post-session
    ])
    res = rp.run_replay("NIFTY", _enter_once("14:00"), start="2026-08-27", end="2026-08-27")
    t = res.trades[0]
    assert t.exit_reason == "SESSION_CLOSE" and t.exit_price == 104.0   # never cross-priced


def test_no_lookahead_ctx_candles(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24005, 24000, CH(101.0, 80.0)),
        ("09:19", 24010, 24000, CH(102.0, 80.0)),
        ("09:20", 24012, 24000, CH(103.0, 80.0)),
        ("09:21", 24015, 24000, CH(104.0, 80.0)),
    ])
    seen = {}

    def decide(state, ctx):
        m = state["ts"][11:16]
        seen[m] = (len(ctx.candles("1m")), len(ctx.candles("5m")))
        return {"decision": "NO_TRADE"}

    rp.run_replay("NIFTY", decide, start="2026-08-27", end="2026-08-27", persist=False)
    # at 09:19 the 09:15 5m bar closes at 09:20 -> NOT yet visible
    assert seen["09:19"][1] == 0
    # at 09:20 the 09:15 5m bar (ends 09:20) IS closed and visible
    assert seen["09:20"][1] == 1
    # 1m bars: at 09:20, bars 09:16..09:19 are closed (09:20 bar ends 09:21)
    assert seen["09:20"][0] == 3


def test_max_concurrent_one(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24000, 24000, CH(100.0, 80.0)),
        ("09:18", 24000, 24000, CH(100.0, 80.0)),
        ("09:19", 24000, 24000, CH(100.0, 80.0)),
        ("09:25", 24000, 24000, CH(100.0, 80.0)),
    ])

    def always_enter(state, ctx):
        return {"decision": "BUY_CE", "strike": 24000, "entry": 100.0,
                "stop_loss": 90.0, "target_1": 999.0, "trailing_stop": 0.0,
                "max_hold_sec": 120, "signal_type": "X", "direction": "BULLISH"}

    res = rp.run_replay("NIFTY", always_enter, start="2026-08-27", end="2026-08-27",
                        max_concurrent=1)
    # entries: 09:16 (TIME@09:18), 09:18 (TIME@09:25), 09:25 (RUN_END) -> 3, never overlapping
    assert res.entries == 3
    assert all(t.status == "CLOSED" for t in res.trades)
    ordered = sorted(res.trades, key=lambda t: t.entry_ts)
    for a, b in zip(ordered, ordered[1:]):
        assert b.entry_ts >= (a.exit_ts or a.entry_ts)      # no two open at once


def test_fail_closed_on_missing_data(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24000, 24000, {}),                 # empty chain -> no decide, hold
        ("09:18", 24025, 24000, CH(115.0, 70.0)),    # back -> TARGET
    ])
    calls = []

    def decide(state, ctx):
        calls.append(state["ts"][11:16])
        if state["ts"][11:16] == "09:16":
            return {"decision": "BUY_CE", "strike": 24000, "entry": 100.0,
                    "stop_loss": 90.0, "target_1": 110.0, "trailing_stop": 0.0,
                    "signal_type": "X", "direction": "BULLISH"}
        return {"decision": "NO_TRADE"}

    res = rp.run_replay("NIFTY", decide, start="2026-08-27", end="2026-08-27")
    assert "09:17" not in calls                       # skipped: no data
    assert res.trades[0].exit_reason == "TARGET"


def test_summary_shape(fresh_db, tmp_path, monkeypatch):
    _mkdb(tmp_path, monkeypatch, [
        ("09:16", 24000, 24000, CH(100.0, 80.0)),
        ("09:17", 24000, 24000, CH(100.0, 80.0)),
        ("09:18", 24025, 24000, CH(115.0, 70.0)),
    ])
    res = rp.run_replay("NIFTY", _enter_once("09:16"), start="2026-08-27", end="2026-08-27")
    s = res.summary()
    assert s["entries"] == 1 and s["closed"] == 1 and s["wins"] == 1
    assert s["win_rate"] == 1.0 and s["net_points"] == 15.0
    assert "TARGET" in s["exit_reasons"]
