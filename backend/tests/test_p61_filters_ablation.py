"""P6.1 configurable filters + ablation study (no test leakage, block = subset)."""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines import scalp_strategy as ss
from app.backtest import runner as bt
from tests.test_calibration_backtest import _DDL, _synth_day  # reuse the synth history


# ---------------- filter config plumbing ----------------
def test_filters_merge_defaults_and_overrides():
    f = ss._filters({"filters": {"block_regimes": ["UNSTABLE", "RANGE"],
                                 "regime_score_mult": {"RANGE": 0.5}}})
    assert f["block_regimes"] == ["UNSTABLE", "RANGE"]      # override wins
    assert f["regime_score_mult"] == {"RANGE": 0.5}         # override wins
    assert f["block_tod"] == ["AFTERNOON"]                  # non-overridden default preserved
    # validated defaults (P6.1 ablation): breakout + afternoon blocked, range down-weighted
    d = ss._filters({})
    assert d["block_signal_types"] == ["RESISTANCE_BREAKOUT"]
    assert d["block_tod"] == ["AFTERNOON"]
    assert d["regime_score_mult"] == {"RANGE": 0.7}
    # every default is still overridable to empty
    assert ss._filters({"filters": {"block_signal_types": []}})["block_signal_types"] == []


def test_block_regime_short_circuits(monkeypatch):
    # stub the sub-engines so we control regime/state without crafting candles
    monkeypatch.setattr(ss, "compute_sr", lambda *a, **k: {"status": "OK", "price": 100.0,
                                                           "atr": 1.0, "support": {"level": 98},
                                                           "resistance": {"level": 102}})
    monkeypatch.setattr(ss, "detect_regime", lambda *a, **k: {"regime": "RANGE", "confidence": 0.6})
    monkeypatch.setattr(ss, "mtf_alignment", lambda *a, **k: {"alignment": 0.0, "magnitude": 0.0,
                                                              "conflict": False, "htf_dominant": False})
    monkeypatch.setattr(ss, "classify", lambda *a, **k: {"state": "RESISTANCE_BREAKOUT",
                                                         "direction": "BULLISH", "state_score": 70.0,
                                                         "anchor": {"level": 102, "side": "RESISTANCE"},
                                                         "components": {}, "false_risk": {"verdict": "CLEAN", "score": 100},
                                                         "reason": ["x"], "roc_pct": 0.1})
    d = ss.decide_from_context({"5m": []}, [], atm=100.0,
                               config={"filters": {"block_regimes": ["RANGE"]}})
    assert d["decision"] == "NO_TRADE" and d["filtered"] == "regime"

    d2 = ss.decide_from_context({"5m": []}, [], atm=100.0,
                                config={"filters": {"block_signal_types": ["RESISTANCE_BREAKOUT"]}})
    assert d2["decision"] == "NO_TRADE" and d2["filtered"] == "signal_type"

    d3 = ss.decide_from_context({"5m": []}, [], atm=100.0, tod_bucket="AFTERNOON",
                                config={"filters": {"block_signal_types": [],
                                                    "block_tod": ["AFTERNOON"]}})
    assert d3["decision"] == "NO_TRADE" and d3["filtered"] == "tod"


def test_filtered_no_trade_still_carries_market_context(monkeypatch):
    # A gate-blocked NO_TRADE must still report the live market read the engine
    # already computed (dashboard shows the picture, not blanks) -- but must NOT
    # fabricate decision math it never ran.
    monkeypatch.setattr(ss, "compute_sr", lambda *a, **k: {
        "status": "OK", "price": 24000.0, "atr": 18.5, "vwap": 24010.0,
        "support": {"level": 23950.0}, "resistance": {"level": 24075.0},
        "support_strength": 61.0, "resistance_strength": 55.0})
    monkeypatch.setattr(ss, "detect_regime", lambda *a, **k: {"regime": "UNSTABLE", "confidence": 0.5})
    monkeypatch.setattr(ss, "mtf_alignment", lambda *a, **k: {"alignment": -22.0, "magnitude": 30.0,
                                                              "conflict": False, "htf_dominant": False})
    monkeypatch.setattr(ss, "classify", lambda *a, **k: {"state": "SUPPORT_BREAKDOWN",
                                                         "direction": "BEARISH", "state_score": 68.0,
                                                         "anchor": {"level": 23950.0, "side": "SUPPORT"},
                                                         "components": {}, "reason": ["x"],
                                                         "false_risk": {"verdict": "CLEAN", "score": 100},
                                                         "roc_pct": -0.3})
    d = ss.decide_from_context({"5m": []}, [], atm=24000.0, config={})   # default filters block UNSTABLE
    assert d["decision"] == "NO_TRADE" and d["filtered"] == "regime"
    # market context present
    assert d["atr"] == 18.5 and d["vwap"] == 24010.0
    assert d["support"] == 23950.0 and d["resistance"] == 24075.0
    assert d["support_strength"] == 61.0 and d["resistance_strength"] == 55.0
    assert d["mtf_alignment"] == -22.0
    assert d["regime"] == "UNSTABLE" and d["signal_type"] == "SUPPORT_BREAKDOWN"
    assert d["state_score"] == 68.0 and d["momentum"] == -0.3
    # decision math NOT fabricated for a signal the engine never scored
    assert d.get("signal_score") is None and d.get("probability") is None
    assert d.get("ev") is None and d.get("rr") is None


# ---------------- ablation on synthetic history ----------------
@pytest.fixture()
def synth_hist(tmp_path, monkeypatch):
    p = tmp_path / "oi_history.db"
    c = sqlite3.connect(p)
    c.executescript(_DDL)
    cid = 1
    # TRAIN: up / down / chop ; TEST: up / down / chop  -> all regimes + tod buckets
    for d, drift in [("2026-07-06", 1.2), ("2026-07-07", -1.1), ("2026-07-08", 0.05),
                     ("2026-07-09", 1.1), ("2026-07-10", -1.2)]:
        cid = _synth_day(c, cid, d, drift=drift, n=110)
    for d, drift in [("2026-07-13", 1.1), ("2026-07-14", -1.0), ("2026-07-15", 0.03)]:
        cid = _synth_day(c, cid, d, drift=drift, n=110)
    c.commit(); c.close()
    monkeypatch.setenv("OI_HISTORY_DB", str(p))
    return p


def test_run_ablation_shape_and_blocked_buckets(synth_hist, fresh_db):
    rep = bt.run_ablation("NIFTY", train=("2026-07-06", "2026-07-10"),
                          test=("2026-07-13", "2026-07-15"), decide_every_sec=60.0)
    v = rep["variants"]
    assert set(v) == {"baseline", "block_RANGE", "block_RESISTANCE_BREAKOUT",
                      "block_AFTERNOON", "combined"}
    for name, d in v.items():
        oos = d["oos"]
        for k in ("closed", "win_rate", "profit_factor", "expectancy_points", "net_points",
                  "max_drawdown_points", "max_consecutive_losses", "by_regime",
                  "by_signal_type", "by_time_of_day", "by_direction",
                  "calibration_reliability"):
            assert k in oos, f"{name} missing {k}"
    # the block filters must remove their bucket from the OOS breakdown
    assert "RANGE" not in v["block_RANGE"]["oos"]["by_regime"]
    assert "RESISTANCE_BREAKOUT" not in v["block_RESISTANCE_BREAKOUT"]["oos"]["by_signal_type"]
    assert "AFTERNOON" not in v["block_AFTERNOON"]["oos"]["by_time_of_day"]
    combo = v["combined"]["oos"]
    assert "RANGE" not in combo["by_regime"]
    assert "RESISTANCE_BREAKOUT" not in combo["by_signal_type"]
    assert "AFTERNOON" not in combo["by_time_of_day"]


def test_ablation_deterministic(synth_hist, fresh_db):
    a = bt.run_ablation("NIFTY", train=("2026-07-06", "2026-07-10"),
                        test=("2026-07-13", "2026-07-15"), decide_every_sec=60.0)
    b = bt.run_ablation("NIFTY", train=("2026-07-06", "2026-07-10"),
                        test=("2026-07-13", "2026-07-15"), decide_every_sec=60.0)
    assert a == b
