"""
app/structural_break/feature_drift.py -- per-feature drift over
scalp_signals-shaped rows. No DB, pure function over an in-memory row list.
"""
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.feature_drift import evaluate_feature_drift  # noqa: E402

random.seed(11)


def _row(**overrides):
    base = {
        "atr": 20.0, "pcr": 0.9, "momentum": 0.0, "mtf_alignment": 50.0,
        "index_ltp": 24000.0, "vwap": 24000.0, "expected_premium_move": 5.0,
        "regime": "TRENDING_UP",
        "component_scores": json.dumps({"volume": 60.0, "oi": 55.0}),
    }
    base.update(overrides)
    return base


def _noisy_rows(n, **kwargs):
    rows = []
    for _ in range(n):
        rows.append(_row(
            atr=20.0 + random.gauss(0, 1), pcr=0.9 + random.gauss(0, 0.03),
            momentum=random.gauss(0, 0.15), mtf_alignment=50.0 + random.gauss(0, 2),
            index_ltp=24000 + random.gauss(0, 3), vwap=24000 + random.gauss(0, 2),
            expected_premium_move=5.0 + random.gauss(0, 0.2),
            component_scores=json.dumps({"volume": 60 + random.gauss(0, 2),
                                          "oi": 55 + random.gauss(0, 2)}),
            **kwargs,
        ))
    return rows


def test_insufficient_data_reports_clearly():
    out = evaluate_feature_drift(_noisy_rows(10))
    assert out["status"] == "INSUFFICIENT_DATA"


def test_all_named_features_are_covered():
    out = evaluate_feature_drift(_noisy_rows(200))
    assert out["status"] == "OK"
    expected = {"atr", "pcr", "momentum", "mtf_alignment", "vwap_distance_pct",
                "expected_premium_move", "regime", "volume_score", "oi_score"}
    assert set(out["features"]) == expected


def test_atr_shape_shift_detected_by_ks():
    rows = _noisy_rows(150)
    for _ in range(30):
        rows.append(_row(atr=20.0 + random.gauss(0, 6)))   # same mean, much wider spread
    out = evaluate_feature_drift(rows)
    assert out["features"]["atr"]["triggered"] is True
    assert out["features"]["atr"]["method"] == "ks"


def test_pcr_mean_shift_detected_by_cusum():
    rows = _noisy_rows(150)
    for _ in range(30):
        rows.append(_row(pcr=1.6 + random.gauss(0, 0.03)))   # sustained mean shift
    out = evaluate_feature_drift(rows)
    assert out["features"]["pcr"]["triggered"] is True
    assert out["features"]["pcr"]["method"] == "cusum"


def test_regime_mix_shift_detected_by_psi():
    rows = _noisy_rows(150, regime="TRENDING_UP")
    for _ in range(30):
        rows.append(_row(regime="HIGH_VOLATILITY"))   # complete mix change
    out = evaluate_feature_drift(rows)
    r = out["features"]["regime"]
    assert r["triggered"] is True and r["method"] == "psi"
    assert r["detail"]["level"] == "significant"


def test_stable_regime_mix_does_not_trigger_psi():
    rows = _noisy_rows(200, regime="TRENDING_UP")   # constant regime throughout
    out = evaluate_feature_drift(rows)
    assert out["features"]["regime"]["triggered"] is False


def test_component_score_zscore_shift():
    rows = _noisy_rows(150)
    for _ in range(30):
        rows.append(_row(component_scores=json.dumps({"volume": 90.0, "oi": 55.0})))
    out = evaluate_feature_drift(rows)
    assert out["features"]["volume_score"]["triggered"] is True
    assert out["features"]["volume_score"]["method"] == "zscore"


def test_missing_component_scores_json_does_not_crash():
    rows = _noisy_rows(150)
    for _ in range(30):
        rows.append(_row(component_scores=None))
    out = evaluate_feature_drift(rows)
    # not enough valid volume_score samples in current window -> insufficient, not a crash
    assert out["features"]["volume_score"]["status"] in ("insufficient_samples", "ok")


def test_baseline_and_current_windows_never_overlap():
    from app.structural_break.feature_drift import _split_baseline_current
    rows = [{"i": i} for i in range(200)]
    baseline, current = _split_baseline_current(rows, baseline_n=150, current_n=30)
    baseline_ids = {r["i"] for r in baseline}
    current_ids = {r["i"] for r in current}
    assert not (baseline_ids & current_ids)
    assert current == rows[-30:]
