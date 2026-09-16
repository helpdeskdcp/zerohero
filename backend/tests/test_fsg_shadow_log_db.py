"""app.db.insert_fsg_shadow_log / list_fsg_shadow_analysis: the persistent,
per-signal shadow-mode audit log (as opposed to the single latest-per-symbol
snapshot), joined against the trade's real resolved outcome."""
from app import db


def _shadow_row(**kw):
    base = dict(trade_id="TRD-1", signal_id="SIG-1", created_ts="2026-09-17T00:00:00Z",
               symbol="NATURALGAS", state="APPROVED", reason="ok", confidence_score=85.0,
               historical_confidence="UNCALIBRATED", sr_verdict="CONFIRM",
               components="{}", shadow_mode=1)
    base.update(kw)
    return base


def _outcome_row(**kw):
    base = dict(trade_id="TRD-1", signal_id="SIG-1", closed_ts="2026-09-17T01:00:00Z",
               opened_ts="2026-09-17T00:00:00Z", underlying="NATURALGAS", option_type="PE",
               strategy="AUTOSCALP", entry_price=100.0, exit_price=107.0, exit_reason="TARGET",
               mfe=8.0, mae=1.0, mae_before_mfe=0, time_to_mfe_peak_sec=300, time_to_exit_sec=600,
               time_to_t1_sec=600, time_to_t2_sec=None, time_to_sl_sec=None,
               realized_points=7.0, realized_pnl=7.0, r_multiple=1.4, outcome="WIN",
               t1_before_sl=1, t2_before_sl=0, sl_before_target=0,
               reversal_after_entry=0, time_expiry=0)
    base.update(kw)
    return base


def test_insert_and_join_with_real_outcome(fresh_db):
    assert db.insert_fsg_shadow_log(_shadow_row()) is True
    assert db.insert_trade_exit_outcome(_outcome_row()) is True
    rows = db.list_fsg_shadow_analysis()
    assert len(rows) == 1
    r = rows[0]
    assert r["state"] == "APPROVED"
    assert r["y_outcome"] == "WIN"
    assert r["y_points"] == 7.0


def test_no_outcome_yet_is_excluded_not_fabricated(fresh_db):
    db.insert_fsg_shadow_log(_shadow_row())
    # no matching trade_exit_outcomes row -- still-open trade
    assert db.list_fsg_shadow_analysis() == []


def test_write_once_never_overwrites(fresh_db):
    db.insert_fsg_shadow_log(_shadow_row(state="APPROVED"))
    assert db.insert_fsg_shadow_log(_shadow_row(state="REJECT")) is False
    db.insert_trade_exit_outcome(_outcome_row())
    rows = db.list_fsg_shadow_analysis()
    assert rows[0]["state"] == "APPROVED"


def test_missing_trade_id_is_rejected_not_a_crash(fresh_db):
    assert db.insert_fsg_shadow_log({"symbol": "NIFTY"}) is False


def test_filter_by_symbol(fresh_db):
    db.insert_fsg_shadow_log(_shadow_row(trade_id="TRD-1", symbol="NATURALGAS"))
    db.insert_trade_exit_outcome(_outcome_row(trade_id="TRD-1"))
    db.insert_fsg_shadow_log(_shadow_row(trade_id="TRD-2", symbol="CRUDEOIL"))
    db.insert_trade_exit_outcome(_outcome_row(trade_id="TRD-2"))
    rows = db.list_fsg_shadow_analysis(symbol="CRUDEOIL")
    assert len(rows) == 1 and rows[0]["symbol"] == "CRUDEOIL"
