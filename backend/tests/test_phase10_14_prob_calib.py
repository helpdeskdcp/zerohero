"""PHASE 10 (probability transparency + data-aware confidence) + PHASE 14 (metrics)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.engines.scalp_strategy import _calib_meta, effective_confidence


def test_calib_meta_prior_vs_fitted():
    assert _calib_meta(None, regime="X", signal_type="Y")["calibration_status"] == "prior"
    assert _calib_meta({}, regime="X", signal_type="Y")["calibration_status"] == "prior"
    fitted = {"version": "v1", "curves": {"TRENDING_UP|MOMO": {"k": 3.0, "b": 0.1, "n": 55}}}
    m = _calib_meta(fitted, regime="TRENDING_UP", signal_type="MOMO")
    assert m["calibration_status"] == "fitted" and m["calibration_samples"] == 55
    assert m["prob_source"].startswith("curve:")


def test_effective_confidence_only_lowers():
    # good data + fitted + big sample -> unchanged
    c, _ = effective_confidence("HIGH", data_quality_score=0.95,
                                calibration_status="fitted", calibration_samples=200)
    assert c == "HIGH"
    # thin data -> capped to LOW
    c, why = effective_confidence("HIGH", data_quality_score=0.5,
                                  calibration_status="fitted", calibration_samples=200)
    assert c == "LOW" and "data_quality" in why
    # uncalibrated -> capped to MEDIUM even with perfect data
    c, why = effective_confidence("HIGH", data_quality_score=1.0,
                                  calibration_status="prior", calibration_samples=0)
    assert c == "MEDIUM" and "prior" in why
    # never raises
    c, _ = effective_confidence("LOW", data_quality_score=1.0,
                                calibration_status="fitted", calibration_samples=500)
    assert c == "LOW"


def test_calibration_report_shapes_insufficient(monkeypatch):
    import app.autoscalp.calibration_report as cr
    monkeypatch.setattr(cr, "_resolved_rows", lambda limit=5000: [])
    rep = cr.calibration_report()
    assert rep["n_resolved"] == 0 and rep["status"] == "INSUFFICIENT_DATA"


def test_calibration_report_metrics(monkeypatch):
    import app.autoscalp.calibration_report as cr
    # 40 synthetic resolved trades: p=0.6 wins 60% -> well calibrated
    rows = []
    for i in range(40):
        win = 1 if i % 10 < 6 else 0
        rows.append((0.6, win, (5.0 if win else -4.0), None))
    monkeypatch.setattr(cr, "_resolved_rows", lambda limit=5000: rows)
    rep = cr.calibration_report()
    assert rep["status"] == "OK" and rep["n_resolved"] == 40
    assert 0.0 <= rep["brier"] <= 1.0 and rep["log_loss"] > 0
    assert abs(rep["win_rate"] - 0.6) < 1e-9
    assert rep["profit_factor"] is not None
    assert rep["verdict"] in ("WELL_CALIBRATED", "MILD_MISCALIBRATION", "OVERCONFIDENT")
