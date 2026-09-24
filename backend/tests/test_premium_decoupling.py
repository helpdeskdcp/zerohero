"""app.premium_decoupling -- classify() sign-rule table + engine.compute_window()
against a synthetic, isolated market_history.db (never the live db)."""
import sqlite3

from app.premium_decoupling import classify as clf
from app.premium_decoupling import engine, store


def test_bullish_premium_drag_is_the_pattern_the_user_asked_for():
    r = clf.classify(spot_delta_pct=0.5, ce_delta_pct=-3.0, pe_delta_pct=-4.0)
    assert r["classification"] == "BULLISH_PREMIUM_DRAG"


def test_textbook_up_move_is_normal_bullish():
    r = clf.classify(spot_delta_pct=0.5, ce_delta_pct=5.0, pe_delta_pct=-5.0)
    assert r["classification"] == "NORMAL_BULLISH"


def test_theta_bleed_on_flat_spot():
    r = clf.classify(spot_delta_pct=0.0, ce_delta_pct=-2.0, pe_delta_pct=-2.0)
    assert r["classification"] == "THETA_BLEED"


def test_bearish_divergence_up_spot_options_say_down():
    r = clf.classify(spot_delta_pct=0.3, ce_delta_pct=-5.0, pe_delta_pct=5.0)
    assert r["classification"] == "BEARISH_DIVERGENCE"


def test_missing_leg_data_is_insufficient_not_fabricated():
    r = clf.classify(spot_delta_pct=0.3, ce_delta_pct=None, pe_delta_pct=5.0)
    assert r["classification"] == "INSUFFICIENT_DATA"


def _seed_db(path):
    conn = sqlite3.connect(str(path))
    conn.execute("""CREATE TABLE quote_snapshots (
        received_ts TEXT, symbol TEXT, kind TEXT, expiry TEXT,
        strike REAL, option_type TEXT, ltp REAL)""")
    rows = [
        ("2026-09-24T05:00:00.000000Z", "NIFTY", "INDEX", None, None, None, 23200.0),
        ("2026-09-24T05:04:00.000000Z", "NIFTY", "INDEX", None, None, None, 23230.0),
        ("2026-09-24T05:00:00.000000Z", "NIFTY", "OPTION", "29SEP2026", 23200.0, "CE", 120.0),
        ("2026-09-24T05:04:00.000000Z", "NIFTY", "OPTION", "29SEP2026", 23200.0, "CE", 110.0),
        ("2026-09-24T05:00:00.000000Z", "NIFTY", "OPTION", "29SEP2026", 23200.0, "PE", 90.0),
        ("2026-09-24T05:04:00.000000Z", "NIFTY", "OPTION", "29SEP2026", 23200.0, "PE", 70.0),
    ]
    conn.executemany(
        "INSERT INTO quote_snapshots VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_engine_reads_captured_ticks_and_classifies_bullish_premium_drag(tmp_path):
    db = tmp_path / "market_history.db"
    _seed_db(db)
    result = engine.compute_window("NIFTY", window_sec=300,
                                    end_ts="2026-09-24T05:04:00.000000Z", db_path=db)
    assert result["spot_open"] == 23200.0 and result["spot_close"] == 23230.0
    assert result["ce_open"] == 120.0 and result["ce_close"] == 110.0
    assert result["pe_open"] == 90.0 and result["pe_close"] == 70.0
    assert result["classification"] == "BULLISH_PREMIUM_DRAG"


def test_engine_returns_insufficient_data_outside_captured_range(tmp_path):
    db = tmp_path / "market_history.db"
    _seed_db(db)
    result = engine.compute_window("NIFTY", window_sec=300,
                                    end_ts="2026-01-01T00:00:00.000000Z", db_path=db)
    assert result["classification"] == "INSUFFICIENT_DATA"


def test_store_logs_and_is_idempotent(tmp_path):
    db = tmp_path / "market_history.db"
    _seed_db(db)
    shadow_db = tmp_path / "premium_decoupling.db"
    result = engine.compute_window("NIFTY", window_sec=300,
                                    end_ts="2026-09-24T05:04:00.000000Z", db_path=db)
    first_id = store.log_result(result, db_path=shadow_db)
    second_id = store.log_result(result, db_path=shadow_db)
    assert first_id is not None
    assert second_id is None  # duplicate window -> INSERT OR IGNORE, no 2nd row
    rows = store.recent("NIFTY", db_path=shadow_db)
    assert len(rows) == 1
    assert rows[0]["classification"] == "BULLISH_PREMIUM_DRAG"


def test_store_never_logs_insufficient_data(tmp_path):
    shadow_db = tmp_path / "premium_decoupling.db"
    stub = {"underlying": "NIFTY", "classification": "INSUFFICIENT_DATA"}
    assert store.log_result(stub, db_path=shadow_db) is None
