"""app.signal_gate.shadow_analysis.build_shadow_report: aggregates real
gate-verdict-vs-outcome rows into win rate / expectancy / PF, overall, by
state, by symbol, and the headline APPROVED-vs-rest comparison."""
from app import db
from app.signal_gate.shadow_analysis import build_shadow_report


def _pair(db_mod, trade_id, symbol, state, points, outcome):
    db_mod.insert_fsg_shadow_log({
        "trade_id": trade_id, "signal_id": trade_id, "created_ts": "t",
        "symbol": symbol, "state": state, "reason": "x", "confidence_score": 80.0,
        "historical_confidence": "UNCALIBRATED", "sr_verdict": "CONFIRM",
        "components": "{}", "shadow_mode": 1,
    })
    db_mod.insert_trade_exit_outcome({
        "trade_id": trade_id, "signal_id": trade_id, "closed_ts": "t2", "opened_ts": "t1",
        "underlying": symbol, "option_type": "CE", "strategy": "AUTOSCALP",
        "entry_price": 100.0, "exit_price": 100.0 + points, "exit_reason": "TARGET" if points > 0 else "STOP",
        "mfe": max(points, 0), "mae": max(-points, 0), "mae_before_mfe": 0,
        "time_to_mfe_peak_sec": 100, "time_to_exit_sec": 200,
        "time_to_t1_sec": None, "time_to_t2_sec": None, "time_to_sl_sec": None,
        "realized_points": points, "realized_pnl": points, "r_multiple": points / 5.0,
        "outcome": outcome, "t1_before_sl": 0, "t2_before_sl": 0, "sl_before_target": 0,
        "reversal_after_entry": 0, "time_expiry": 0,
    })


def test_empty_report_is_honest_not_fabricated(fresh_db):
    r = build_shadow_report()
    assert r["n_resolved"] == 0


def test_report_splits_by_state_symbol_and_approved_vs_rest(fresh_db):
    _pair(db, "T1", "NATURALGAS", "APPROVED", 10.0, "WIN")
    _pair(db, "T2", "NATURALGAS", "REJECT", -10.0, "LOSS")
    _pair(db, "T3", "CRUDEOIL", "APPROVED", 5.0, "WIN")
    _pair(db, "T4", "CRUDEOIL", "WAIT", -3.0, "LOSS")

    r = build_shadow_report()
    assert r["n_resolved"] == 4
    assert r["overall"]["n"] == 4
    assert r["by_state"]["APPROVED"]["n"] == 2
    assert r["by_state"]["APPROVED"]["win_rate_pct"] == 100.0
    assert r["by_symbol"]["NATURALGAS"]["n"] == 2
    assert r["by_symbol"]["CRUDEOIL"]["n"] == 2
    assert r["approved_vs_rest"]["approved"]["n"] == 2
    assert r["approved_vs_rest"]["not_approved"]["n"] == 2
    assert r["approved_vs_rest"]["approved"]["win_rate_pct"] == 100.0
    assert r["approved_vs_rest"]["not_approved"]["win_rate_pct"] == 0.0
