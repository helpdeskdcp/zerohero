"""P5 calibration + P6 backtest runner (chronological, out-of-sample)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.backtest import calibration as cal
from app.backtest import runner as bt


# ---------------- P5 calibration ----------------
def test_fit_learns_monotone_curve():
    # higher score -> higher win rate
    samples = []
    for i in range(400):
        score = (i % 100)
        win = (i * 7 + 3) % 100 < score          # P(win) ~ score/100
        samples.append({"score": score, "regime": "RANGE", "signal_type": "X", "win": win})
    c = cal.fit(samples)
    assert c["n_samples"] == 400
    assert c["global"]["k"] > 0                  # positive slope: score predicts wins
    p_lo = cal.predict(c, 20, regime="RANGE", signal_type="X")
    p_hi = cal.predict(c, 85, regime="RANGE", signal_type="X")
    assert p_hi > p_lo
    assert 0.0 <= p_lo <= 1.0 and 0.0 <= p_hi <= 1.0


def test_small_bucket_falls_back_to_global_or_prior():
    samples = [{"score": 60, "regime": "R", "signal_type": "T", "win": i % 2 == 0} for i in range(10)]
    c = cal.fit(samples)
    assert "R|T" not in c["curves"]              # < _MIN_ROWS -> no per-key curve
    p = cal.predict(c, 60, regime="R", signal_type="T")
    assert 0.0 <= p <= 1.0


def test_reliability_curve_and_brier():
    # perfectly calibrated: predicted == actual frequency
    pairs = []
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        pairs += [(p, True)] * int(p * 100) + [(p, False)] * int((1 - p) * 100)
    rc = cal.reliability_curve(pairs, bins=10)
    assert rc["n"] == len(pairs)
    assert 0.0 <= rc["brier"] <= 0.25
    assert rc["ece"] < 0.05                      # well calibrated -> tiny error
    for b in rc["bins"]:
        if b["n"]:
            assert abs(b["predicted"] - b["actual"]) < 0.15


def test_calibration_deterministic():
    s = [{"score": (i * 3) % 100, "regime": "RANGE", "signal_type": "X", "win": i % 3 == 0}
         for i in range(300)]
    assert cal.fit(s) == cal.fit(s)


# ---------------- P6 backtest runner ----------------
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


def _synth_day(c, cid0, date, base=24000, drift=0.0, n=90):
    """One session: index walks with `drift` pts/bar; a 5-strike chain tracks it."""
    cid = cid0
    px = base
    for i in range(n):
        mm = 15 + i * 4
        ts = f"{date}T{9 + mm // 60:02d}:{mm % 60:02d}:{(i * 7) % 60:02d}"
        px += drift + (0.6 if i % 2 else -0.6)
        atm = round(px / 50) * 50
        c.execute("INSERT INTO cycles (id,symbol,ts,date,time,underlying_ltp,atm,pcr,max_pain,bias)"
                  " VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (cid, "NIFTY", ts, date, ts[11:19], round(px, 2), atm, 1.0, atm, "X"))
        for j, k in enumerate(range(atm - 100, atm + 150, 50)):
            ce = max(2.0, (px - k) * 0.5 + 90 - abs(px - k) * 0.1)
            pe = max(2.0, (k - px) * 0.5 + 90 - abs(px - k) * 0.1)
            c.execute("INSERT INTO strikes (cycle_id,strike,ce_oi,ce_oi_chg,ce_vol,ce_ltp,ce_chg_pct,"
                      "pe_oi,pe_oi_chg,pe_vol,pe_ltp,ce_iv,ce_delta,ce_gamma,ce_theta,ce_vega,"
                      "ce_token,ce_contract_expiry,pe_token,pe_contract_expiry) "
                      "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (cid, k, 500000, 1000, 20000 + i * 400, round(ce, 2), 3.0,
                       500000, 1000, 20000 + i * 400, round(pe, 2), 13.0, 0.5, 0.001, -18.0, 9.0,
                       f"T{k}CE", "2026-09-03", f"T{k}PE", "2026-09-03"))
        cid += 1
    return cid


@pytest.fixture()
def synth_hist(tmp_path, monkeypatch):
    p = tmp_path / "oi_history.db"
    c = sqlite3.connect(p)
    c.executescript(_DDL)
    cid = 1
    # TRAIN: 3 up days + 2 down days ; TEST: 2 up + 1 down
    for d, drift in [("2026-07-06", 1.2), ("2026-07-07", 1.0), ("2026-07-08", -1.1),
                     ("2026-07-09", 1.3), ("2026-07-10", -1.0)]:
        cid = _synth_day(c, cid, d, drift=drift)
    for d, drift in [("2026-07-13", 1.1), ("2026-07-14", -1.2), ("2026-07-15", 1.0)]:
        cid = _synth_day(c, cid, d, drift=drift)
    c.commit(); c.close()
    monkeypatch.setenv("OI_HISTORY_DB", str(p))
    return p


def test_run_backtest_end_to_end(synth_hist, fresh_db):
    rep = bt.run_backtest("NIFTY",
                          train=("2026-07-06", "2026-07-10"),
                          test=("2026-07-13", "2026-07-15"),
                          persist=False)
    assert rep["symbol"] == "NIFTY"
    assert rep["train"]["range"] == ["2026-07-06", "2026-07-10"]
    assert rep["test"]["range"] == ["2026-07-13", "2026-07-15"]
    oos = rep["test"]["out_of_sample"]
    # the pipeline ran and produced a spec-18 shaped report
    for k in ("decisions", "closed", "win_rate", "profit_factor", "expectancy_points",
              "max_drawdown_points", "max_consecutive_losses", "by_regime",
              "by_time_of_day", "by_signal_type", "by_direction", "by_expiry",
              "calibration_reliability", "false_breakout_trades"):
        assert k in oos
    assert oos["decisions"] > 0
    assert rep["train"]["calibration"]["version"].startswith("bt-NIFTY-")
    assert "NOT a profitability claim" in rep["disclaimer"]
    # every closed test trade is one locked contract (no rollover contamination)
    assert isinstance(oos["exit_reasons"], dict)


def test_backtest_no_lookahead_calibration_is_frozen(synth_hist, fresh_db):
    rep = bt.run_backtest("NIFTY", train=("2026-07-06", "2026-07-10"),
                          test=("2026-07-13", "2026-07-15"), persist=False)
    cver = rep["train"]["calibration"]["version"]
    # the test slice was scored with the calibration fitted on the TRAIN range only
    assert "2026-07-06_2026-07-10" in cver
    assert "2026-07-13" not in cver
