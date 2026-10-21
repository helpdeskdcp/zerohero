import pytest

from app import sl_detector as D


def test_estimate_rule_is_clipped_and_deterministic():
    assert D.estimate(100, [90, 80, 70, 95])["sl"] == 67.9
    assert D.estimate(100, [60, 58])["sl"] == 56.25                                                                       # 0.97*58 = 56.26 -> 0.05 grid
    assert D.estimate(100, [30, 120])["sl"] == 55.0                                                                       # far below -> floor 0.55E
    assert D.estimate(100, [99, 98.5])["sl"] == 85.0                                                                      # too tight -> cap 0.85E
    assert D.estimate(100, [140, 150])["method"] == "SWING_LOW_CLIPPED" and D.estimate(100, [140, 150])["low"] == 150      # all above entry -> last price
    assert D.estimate(100, []) == {"sl": 70.0, "method": "FALLBACK_70PCT", "low": None}


def test_prediction_then_truth_then_report(tmp_path):
    pp, tp = str(tmp_path / "p.jsonl"), str(tmp_path / "t.jsonl")
    r = D.record_prediction("H00009", "NIFTY", "2026-10-27", 25350.0, "PE", 160.0, 1_760_000_000.0, path=pp, ltps=[150, 140, 130])
    assert r["predicted_sl"] == 126.1                                                                                     # 0.97 * 130
    with pytest.raises(ValueError): D.record_truth("H00009", 120.0, path=tp, pred_path=pp, now=r["recorded_ts"] - 1)                      # truth cannot predate the prediction
    with pytest.raises(ValueError): D.record_truth("H00009", 200.0, path=tp, pred_path=pp)                                                # a stop above the entry is invalid
    D.record_truth("H00009", 120.0, path=tp, pred_path=pp)
    with pytest.raises(ValueError): D.record_truth("H00009", 118.0, path=tp, pred_path=pp)                                                # once only
    rep = D.report(pp, tp); assert rep["n_with_truth"] == 1 and rep["rows"][0]["error_pts"] == round(100 * (r["predicted_sl"] - 120) / 160, 2) and "INSUFFICIENT_SAMPLE" in rep["verdict"]
    lo, hi = D.wilson(5, 10); assert 0.2 < lo < 0.5 < hi < 0.8


def test_no_trading_module_imports_the_estimator():
    import os
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"); bad = []
    for dp, _, fs in os.walk(root):
        if "__pycache__" in dp or dp.endswith("sl_detector") or os.sep + "arena" in dp: continue
        for f in fs:
            if f.endswith(".py") and "sl_detector" in open(os.path.join(dp, f), encoding="utf-8").read(): bad.append(os.path.relpath(os.path.join(dp, f), root))
    assert bad == [], f"only the contest ingest may reference the estimator: {bad}"
