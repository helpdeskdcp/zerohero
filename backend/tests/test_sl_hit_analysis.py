"""app.sl_hit_analysis -- descriptive breakdowns over already-closed trades.
Uses fresh_db (own tmp file, see conftest.py) + app.db.insert_trade, never
the live chanakya.db."""
from app import db
from app.sl_hit_analysis import engine


def _trade(trade_id, **over):
    row = {
        "trade_id": trade_id, "signal_id": f"SIG-{trade_id}",
        "opened_ts": "2026-09-20T04:00:00+00:00", "closed_ts": "2026-09-20T04:10:00+00:00",
        "status": "CLOSED", "result": "LOSS", "market": "NSE", "underlying": "NIFTY",
        "instrument": "INDEX", "direction": "BUY", "timeframe": "1m",
        "entry": 100.0, "exit_price": 98.0, "target_1": 105.0, "stop_loss": 98.0,
        "quantity": 1, "probability": 0.6, "confidence": 0.6,
        "market_regime": "TRENDING_UP", "pnl": -2.0, "strategy": "SCALP",
        "mfe": 0.5, "mae": 2.0, "exit_reason": "STOP",
    }
    row.update(over)
    db.insert_trade(row)


def test_summary_counts_stop_exits_and_results(fresh_db):
    _trade("t1", exit_reason="STOP", result="LOSS")
    _trade("t2", exit_reason="TARGET", result="WIN")
    _trade("t3", exit_reason="TIME", result="FLAT")
    s = engine.summary()
    assert s["total_closed"] == 3
    assert s["stop_exits"] == 1
    assert s["stop_hit_rate"] == round(1 / 3, 4)
    assert s["win"] == 1 and s["loss"] == 1 and s["flat"] == 1


def test_reconstructed_and_estimated_exit_reasons_count_as_stop(fresh_db):
    _trade("t1", exit_reason="SL_HIT_RECONSTRUCTED")
    _trade("t2", exit_reason="SL_HIT_ESTIMATED_NO_NATGASMINI_DATA")
    _trade("t3", exit_reason="TARGET", result="WIN")
    s = engine.summary()
    assert s["stop_exits"] == 2


def test_by_underlying_breaks_down_correctly(fresh_db):
    _trade("t1", underlying="NIFTY", exit_reason="STOP")
    _trade("t2", underlying="NIFTY", exit_reason="TARGET", result="WIN")
    _trade("t3", underlying="BANKNIFTY", exit_reason="STOP")
    rows = {r["key"]: r for r in engine.by_underlying()}
    assert rows["NIFTY"]["n"] == 2 and rows["NIFTY"]["stop_exits"] == 1
    assert rows["BANKNIFTY"]["n"] == 1 and rows["BANKNIFTY"]["sample_flag"] == "LOW_SAMPLE"


def test_by_hour_ist_converts_utc_opened_ts_correctly(fresh_db):
    # 2026-09-20T04:00:00+00:00 -> 09:30 IST
    _trade("t1", opened_ts="2026-09-20T04:00:00+00:00", exit_reason="STOP")
    rows = engine.by_hour_ist()
    assert rows[0]["key"] == 9


def test_mae_overshoot_ratio_and_note(fresh_db):
    # planned risk = |100-98| = 2.0, mae = 2.0 -> ratio 1.0 (stop respected exactly)
    _trade("t1", entry=100.0, stop_loss=98.0, mae=2.0, exit_reason="STOP")
    # planned risk = 2.0, mae = 3.0 -> ratio 1.5 (overshot)
    _trade("t2", entry=100.0, stop_loss=98.0, mae=3.0, exit_reason="STOP")
    out = engine.mae_overshoot()
    row = out["rows"][0]
    assert row["underlying"] == "NIFTY" and row["n"] == 2
    assert row["mean_mae_over_planned_risk"] == 1.25
    assert row["max_mae_over_planned_risk"] == 1.5


def test_mae_overshoot_skips_non_stop_exits(fresh_db):
    _trade("t1", exit_reason="TARGET", result="WIN", mae=5.0)
    out = engine.mae_overshoot()
    assert out["rows"] == []


def test_probability_vs_stop_rate_buckets(fresh_db):
    _trade("t1", probability=0.55, exit_reason="STOP", result="LOSS")
    _trade("t2", probability=0.58, exit_reason="TARGET", result="WIN")
    _trade("t3", probability=0.85, exit_reason="STOP", result="LOSS")
    rows = {r["probability_bucket"]: r for r in engine.probability_vs_stop_rate()}
    assert rows["0.5-0.6"]["n"] == 2 and rows["0.5-0.6"]["stop_hit_rate"] == 0.5
    assert rows["0.8-0.9"]["n"] == 1 and rows["0.8-0.9"]["stop_hit_rate"] == 1.0


def test_full_report_shape(fresh_db):
    _trade("t1", exit_reason="STOP")
    out = engine.full_report()
    for key in ("summary", "by_underlying", "by_regime", "by_strategy",
                "by_hour_ist", "mae_overshoot", "probability_vs_stop_rate"):
        assert key in out
