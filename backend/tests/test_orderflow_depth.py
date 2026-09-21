"""app.orderflow.depth -- resting order-book snapshot for the AI shadow
verification layer. Uses a synthetic, isolated l2_capture.db (never the
real one) via monkeypatched L2_DB_PATH."""
import sqlite3

import pytest

from app.orderflow import depth as d


@pytest.fixture()
def synth_l2(tmp_path, monkeypatch):
    p = tmp_path / "l2_capture.db"
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE snapquote_ticks (
            id INTEGER PRIMARY KEY AUTOINCREMENT, received_ts TEXT, exch_ts_ms INTEGER,
            exch_ts TEXT, token TEXT, tot_buy_qty REAL, tot_sell_qty REAL,
            bid REAL, ask REAL
        );
    """)
    _T0 = 1767255300000   # realistic epoch-ms (2026-01-01T09:15:00Z), not a tiny fictional int
    rows = [
        (_T0, "2026-01-01T09:15:00+00:00", "TOK1", 100.0, 100.0, 99.0, 100.0),
        (_T0 + 60000, "2026-01-01T09:16:00+00:00", "TOK1", 300.0, 100.0, 99.5, 100.0),  # bullish
        (_T0 + 120000, "2026-01-01T09:17:00+00:00", "TOK1", 50.0, 400.0, 99.0, 99.5),   # bearish, thin
    ]
    for ts_ms, ts_iso, tok, buy, sell, bid, ask in rows:
        conn.execute(
            "INSERT INTO snapquote_ticks (received_ts,exch_ts_ms,exch_ts,token,"
            "tot_buy_qty,tot_sell_qty,bid,ask) VALUES (?,?,?,?,?,?,?,?)",
            (ts_iso, ts_ms, ts_iso, tok, buy, sell, bid, ask))
    conn.commit()
    conn.close()
    monkeypatch.setattr(d, "L2_DB_PATH", str(p))
    return p


def test_no_capture_db_returns_unavailable_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(d, "L2_DB_PATH", str(tmp_path / "does_not_exist.db"))
    snap = d.orderflow_snapshot("TOK1")
    assert snap["available"] is False
    assert snap["bid_volume"] is None    # never fabricated as 0


def test_unknown_token_returns_unavailable(synth_l2):
    snap = d.orderflow_snapshot("NOPE")
    assert snap["available"] is False


def test_latest_snapshot_computes_real_imbalance_and_state(synth_l2):
    snap = d.orderflow_snapshot("TOK1")
    assert snap["available"] is True
    # latest row: buy=50, sell=400 -> imbalance = (50-400)/450
    assert snap["bid_volume"] == 50.0 and snap["ask_volume"] == 400.0
    assert round(snap["depth_imbalance"], 3) == round((50 - 400) / 450, 3)
    assert snap["orderflow_state"] == "BEARISH"
    assert snap["liquidity_state"] == "NORMAL"


_T0 = 1767255300000


def test_cutoff_never_returns_a_later_row_than_the_cutoff(synth_l2):
    # cutoff 30s after the FIRST row -- must not see rows 2/3
    snap = d.orderflow_snapshot("TOK1", at_or_before=_T0 + 30000)
    assert snap["bid_volume"] == 100.0 and snap["ask_volume"] == 100.0
    assert snap["orderflow_state"] == "BALANCED"


def test_cutoff_before_any_row_returns_unavailable(synth_l2):
    snap = d.orderflow_snapshot("TOK1", at_or_before=_T0 - 60000)
    assert snap["available"] is False


def test_never_labels_this_as_orderflow_delta_or_aggressor():
    import inspect
    src = inspect.getsource(d)
    assert "orderflow_delta" not in src.lower().replace("_", "")
    assert "resting" in src.lower() and "not trade-level aggressor" in src.lower()


def test_spread_and_spread_pct_computed_from_real_bid_ask(synth_l2):
    snap = d.orderflow_snapshot("TOK1", at_or_before=_T0)
    assert snap["spread"] == round(100.0 - 99.0, 4)
    assert snap["spread_pct"] > 0


def test_snapshot_for_symbol_uses_the_resolved_future_token(synth_l2, monkeypatch):
    from app import instruments
    monkeypatch.setattr(instruments, "resolve_index_future",
                        lambda sym, expiry="AUTO": {"status": "OK", "symboltoken": "TOK1"})
    monkeypatch.setattr(instruments, "resolve_mcx_future",
                        lambda sym, expiry="AUTO": {"status": "DATA_UNAVAILABLE"})
    snap = d.snapshot_for_symbol("NIFTY")
    assert snap["available"] is True
    assert snap["orderflow_state"] == "BEARISH"   # same latest row as TOK1's direct test


def test_snapshot_for_symbol_falls_back_to_mcx_resolver(synth_l2, monkeypatch):
    from app import instruments
    monkeypatch.setattr(instruments, "resolve_index_future",
                        lambda sym, expiry="AUTO": {"status": "DATA_UNAVAILABLE"})
    monkeypatch.setattr(instruments, "resolve_mcx_future",
                        lambda sym, expiry="AUTO": {"status": "OK", "symboltoken": "TOK1"})
    snap = d.snapshot_for_symbol("NATURALGAS")
    assert snap["available"] is True


def test_snapshot_for_symbol_unresolvable_never_raises(monkeypatch):
    from app import instruments
    monkeypatch.setattr(instruments, "resolve_index_future",
                        lambda sym, expiry="AUTO": {"status": "DATA_UNAVAILABLE"})
    monkeypatch.setattr(instruments, "resolve_mcx_future",
                        lambda sym, expiry="AUTO": {"status": "DATA_UNAVAILABLE"})
    snap = d.snapshot_for_symbol("UNKNOWNSYM")
    assert snap["available"] is False
