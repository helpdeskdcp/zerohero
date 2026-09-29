"""app.engines.orderflow_store.TickStore -- classified-tick persistence."""
from __future__ import annotations

from app.engines import orderflow_store as os_


def _row(**overrides):
    row = {"received_ts": "2026-09-29T10:00:00+00:00", "security_id": 68407, "symbol": "NIFTY",
          "kind": "FUTURE", "ltp": 22800.5, "ltq": 10, "ltt": 1790637180, "side": "BUY_AGGRESSOR",
          "classification_method": "QUOTE_RULE", "best_bid": 22800.0, "best_ask": 22801.0, "cvd": 10}
    row.update(overrides)
    return row


def test_insert_many_writes_and_counts_rows(tmp_path):
    store = os_.TickStore(str(tmp_path / "ticks.db"))
    n = store.insert_many([_row(), _row(security_id=68390, symbol="BANKNIFTY")])
    assert n == 2
    assert store.count() == 2


def test_insert_many_empty_list_is_a_noop(tmp_path):
    store = os_.TickStore(str(tmp_path / "ticks.db"))
    assert store.insert_many([]) == 0
    assert store.count() == 0


def test_insert_many_skips_a_malformed_row_without_crashing(tmp_path):
    store = os_.TickStore(str(tmp_path / "ticks.db"))
    good = _row()
    bad = "not a dict"
    n = store.insert_many([good, bad])
    assert n == 1
    assert store.count() == 1


def test_schema_init_is_idempotent(tmp_path):
    path = str(tmp_path / "ticks.db")
    os_.TickStore(path)
    store2 = os_.TickStore(path)  # second init on the same file must not raise
    assert store2.count() == 0


def test_persisted_row_round_trips_real_field_values(tmp_path):
    import sqlite3
    path = str(tmp_path / "ticks.db")
    store = os_.TickStore(path)
    store.insert_many([_row()])
    conn = sqlite3.connect(path)
    row = conn.execute("SELECT security_id, symbol, side, classification_method, cvd "
                       "FROM classified_ticks").fetchone()
    conn.close()
    assert row == (68407, "NIFTY", "BUY_AGGRESSOR", "QUOTE_RULE", 10)
