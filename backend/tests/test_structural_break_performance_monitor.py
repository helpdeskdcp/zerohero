"""
app/structural_break/performance_monitor.py -- rolling-window metrics over
the REAL scalp_signals table (fresh_db fixture, never the live DB -- see
tests/conftest.py's guard).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.performance_monitor import PerformanceMonitor  # noqa: E402


def _seed(db, n, *, win_rate=0.5, symbol="NIFTY", regime="TRENDING_UP",
          probability=0.6, points_win=10.0, points_loss=-8.0, mfe_win=12.0,
          mfe_loss=2.0, start_id=0, source="LIVE"):
    """Insert n resolved scalp_signals rows, oldest-first by insertion order
    (id ASC matches insertion order for a fresh autoincrement table)."""
    for i in range(n):
        is_win = (i % 10) < round(win_rate * 10)
        db.insert_scalp_signal({
            "signal_id": f"sig-{symbol}-{start_id + i}",
            "source": source, "status": "CLOSED", "symbol": symbol, "regime": regime,
            "probability": probability, "outcome": "WIN" if is_win else "LOSS",
            "points": points_win if is_win else points_loss,
            "mfe": mfe_win if is_win else mfe_loss,
            "created_ts": f"2026-09-01T00:{i:02d}:00Z",
            "resolved": 1,
        })


def test_insufficient_data_reports_status_clearly(fresh_db):
    _seed(fresh_db, 2)
    mon = PerformanceMonitor()
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["status"] == "INSUFFICIENT_DATA"
    assert out["short"]["n"] == 2


def test_stable_regime_all_windows_agree(fresh_db):
    _seed(fresh_db, 150, win_rate=0.5)
    mon = PerformanceMonitor()
    out = mon.evaluate(symbol="NIFTY")
    for label in ("short", "medium", "long"):
        w = out[label]
        assert w["status"] == "OK"
        assert abs(w["win_rate"] - 0.5) < 0.15, f"{label}: {w['win_rate']}"


def test_windows_are_sized_by_signal_count_not_all_history(fresh_db):
    _seed(fresh_db, 150)
    mon = PerformanceMonitor()
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["n"] == 20
    assert out["medium"]["n"] == 60
    assert out["long"]["n"] == 150


def test_recent_deterioration_shows_in_short_but_not_long(fresh_db):
    # long history of a healthy 60% win rate, then a recent rough patch
    _seed(fresh_db, 130, win_rate=0.6, start_id=0)
    _seed(fresh_db, 20, win_rate=0.1, start_id=130)   # last 20 signals are bad
    mon = PerformanceMonitor()
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["win_rate"] < 0.25          # short window = the bad patch
    assert out["long"]["win_rate"] > 0.45           # long window still mostly reflects the good history
    assert out["short"]["win_rate"] < out["long"]["win_rate"]


def test_consecutive_losses_current_vs_max(fresh_db):
    # WWWLLLLLW LL  (9 then a final win, then 2 more losses) — max streak 4, current streak 2
    outcomes = ["WIN", "WIN", "WIN", "LOSS", "LOSS", "LOSS", "LOSS", "WIN", "LOSS", "LOSS"]
    for i, oc in enumerate(outcomes):
        fresh_db.insert_scalp_signal({
            "signal_id": f"seq-{i}", "source": "LIVE", "status": "CLOSED", "symbol": "NIFTY",
            "regime": "RANGE", "probability": 0.55, "outcome": oc,
            "points": 5.0 if oc == "WIN" else -5.0, "mfe": 5.0 if oc == "WIN" else 0.0,
            "resolved": 1,
        })
    mon = PerformanceMonitor(windows={"short": 10, "medium": 10, "long": 10}, min_n=5)
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["consecutive_losses_max"] == 4
    assert out["short"]["consecutive_losses_current"] == 2


def test_directional_accuracy_uses_mfe_when_available(fresh_db):
    # a LOSS with mfe>0 was directionally right but lost to decay/timing --
    # directional_accuracy should be HIGHER than win_rate in that case
    for i in range(20):
        is_win = i % 2 == 0
        fresh_db.insert_scalp_signal({
            "signal_id": f"da-{i}", "source": "LIVE", "status": "CLOSED", "symbol": "NIFTY",
            "regime": "RANGE", "probability": 0.5, "outcome": "WIN" if is_win else "LOSS",
            "points": 8.0 if is_win else -6.0,
            "mfe": 8.0 if is_win else 3.0,   # even the losses had positive MFE at some point
            "resolved": 1,
        })
    mon = PerformanceMonitor(windows={"short": 20, "medium": 20, "long": 20}, min_n=5)
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["win_rate"] == 0.5
    assert out["short"]["directional_accuracy"] == 1.0   # every trade had mfe>0 or was a WIN
    assert out["short"]["directional_accuracy_method"] == "win_or_mfe_positive"


def test_directional_accuracy_falls_back_to_win_rate_without_mfe(fresh_db):
    for i in range(20):
        is_win = i % 2 == 0
        fresh_db.insert_scalp_signal({
            "signal_id": f"nomfe-{i}", "source": "LIVE", "status": "CLOSED", "symbol": "NIFTY",
            "regime": "RANGE", "probability": 0.5, "outcome": "WIN" if is_win else "LOSS",
            "points": 8.0 if is_win else -6.0,
            "resolved": 1,   # no mfe field at all
        })
    mon = PerformanceMonitor(windows={"short": 20, "medium": 20, "long": 20}, min_n=5)
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["directional_accuracy"] == out["short"]["win_rate"]
    assert out["short"]["directional_accuracy_method"] == "fallback_win_rate_no_mfe_captured"


def test_symbol_and_regime_filters_isolate_the_right_rows(fresh_db):
    _seed(fresh_db, 30, symbol="NIFTY", regime="TRENDING_UP", win_rate=0.7, start_id=0)
    _seed(fresh_db, 30, symbol="NIFTY", regime="RANGE", win_rate=0.2, start_id=100)
    mon = PerformanceMonitor(windows={"short": 30, "medium": 30, "long": 30})
    trending = mon.evaluate(symbol="NIFTY", regime="TRENDING_UP")
    ranging = mon.evaluate(symbol="NIFTY", regime="RANGE")
    assert trending["short"]["win_rate"] > 0.5
    assert ranging["short"]["win_rate"] < 0.35


def test_calibration_fields_reuse_existing_reliability_binning(fresh_db):
    # perfectly-calibrated synthetic data: predicted prob == actual frequency
    for i in range(100):
        p = 0.7
        is_win = (i % 10) < 7   # exactly 70% win rate matching the predicted probability
        fresh_db.insert_scalp_signal({
            "signal_id": f"cal-{i}", "source": "LIVE", "status": "CLOSED", "symbol": "NIFTY",
            "regime": "RANGE", "probability": p, "outcome": "WIN" if is_win else "LOSS",
            "points": 5.0 if is_win else -5.0, "resolved": 1,
        })
    mon = PerformanceMonitor(windows={"short": 100, "medium": 100, "long": 100})
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["ece"] < 0.05, "well-calibrated synthetic data should show tiny ECE"
    assert abs(out["short"]["mean_predicted"] - 0.7) < 0.01


def test_source_defaults_to_live_and_excludes_other_sources(fresh_db):
    _seed(fresh_db, 30, symbol="NIFTY", win_rate=0.9, source="LIVE")
    _seed(fresh_db, 30, symbol="NIFTY", win_rate=0.1, source="BACKTEST", start_id=100)
    mon = PerformanceMonitor(windows={"short": 30, "medium": 30, "long": 30})
    out = mon.evaluate(symbol="NIFTY")
    assert out["short"]["win_rate"] > 0.7, "BACKTEST rows must not leak into the LIVE monitor"
