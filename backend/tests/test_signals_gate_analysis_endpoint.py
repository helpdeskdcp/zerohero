"""/api/signals/gate-analysis: shadow-mode analysis endpoint (real gate
verdicts joined against real trade outcomes)."""
from app import db
from app.api import autoscalp_routes as routes


def test_endpoint_reports_no_data_yet_honestly(fresh_db):
    r = routes.api_signals_gate_analysis()
    assert r["n_resolved"] == 0


def test_endpoint_reflects_real_resolved_signals(fresh_db):
    db.insert_fsg_shadow_log({
        "trade_id": "T1", "signal_id": "T1", "created_ts": "t", "symbol": "NATURALGAS",
        "state": "APPROVED", "reason": "x", "confidence_score": 85.0,
        "historical_confidence": "UNCALIBRATED", "sr_verdict": "CONFIRM",
        "components": "{}", "shadow_mode": 1,
    })
    db.insert_trade_exit_outcome({
        "trade_id": "T1", "signal_id": "T1", "closed_ts": "t2", "opened_ts": "t1",
        "underlying": "NATURALGAS", "option_type": "PE", "strategy": "AUTOSCALP",
        "entry_price": 100.0, "exit_price": 110.0, "exit_reason": "TARGET",
        "mfe": 10.0, "mae": 0.0, "mae_before_mfe": 0, "time_to_mfe_peak_sec": 100,
        "time_to_exit_sec": 200, "time_to_t1_sec": 200, "time_to_t2_sec": None, "time_to_sl_sec": None,
        "realized_points": 10.0, "realized_pnl": 10.0, "r_multiple": 2.0, "outcome": "WIN",
        "t1_before_sl": 1, "t2_before_sl": 0, "sl_before_target": 0,
        "reversal_after_entry": 0, "time_expiry": 0,
    })
    r = routes.api_signals_gate_analysis()
    assert r["n_resolved"] == 1
    assert r["by_state"]["APPROVED"]["win_rate_pct"] == 100.0
