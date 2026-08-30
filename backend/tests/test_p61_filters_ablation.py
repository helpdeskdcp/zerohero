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
    assert f["block_regimes"] == ["UNSTABLE", "RANGE"]
    assert f["regime_score_mult"] == {"RANGE": 0.5}
    assert f["block_signal_types"] == []            # default preserved
    # nothing hard-coded permanently: empty config -> only UNSTABLE blocked
    assert ss._filters({})["block_regimes"] == ["UNSTABLE"]


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
                                config={"filters": {"block_tod": ["AFTERNOON"]}})
    assert d3["decision"] == "NO_TRADE" and d3["filtered"] == "tod"


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
