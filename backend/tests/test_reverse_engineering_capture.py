"""app.reverse_engineering.capture -- synthetic isolated market_history.db,
never the real one (monkeypatch MARKET_DB_PATH)."""
import sqlite3

import pytest

from app.reverse_engineering import capture as c


@pytest.fixture()
def synth_market(tmp_path, monkeypatch):
    p = tmp_path / "market_history.db"
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE market_candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, kind TEXT, tf TEXT,
            bar_start TEXT, o REAL, h REAL, l REAL, c REAL, v REAL
        );
        CREATE TABLE quote_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, kind TEXT, exch_ts TEXT,
            expiry TEXT, strike REAL, option_type TEXT, ltp REAL, bid REAL, ask REAL,
            volume REAL, oi REAL, oi_change REAL
        );
    """)
    # T0 = 2026-09-03T09:45:00+00:00. Real bars only at T0 and T+5m (T-30m
    # etc are intentionally absent -- must come back None, never fabricated).
    conn.execute("INSERT INTO market_candles (symbol,kind,tf,bar_start,o,h,l,c,v) "
                "VALUES ('NIFTY','INDEX','1m','2026-09-03T09:45:00+00:00',100,101,99,100.5,1000)")
    conn.execute("INSERT INTO market_candles (symbol,kind,tf,bar_start,o,h,l,c,v) "
                "VALUES ('NIFTY','INDEX','1m','2026-09-03T09:50:00+00:00',101,102,100,101.5,1200)")
    # Option snapshot only at T0 (real), nothing else.
    conn.execute("INSERT INTO quote_snapshots (symbol,kind,exch_ts,expiry,strike,option_type,"
                "ltp,bid,ask,volume,oi,oi_change) VALUES "
                "('SENSEX','OPTION','2026-09-03T09:45:20+00:00','03SEP26',76500.0,'PE',"
                "347.15,345.0,349.0,1580860.0,545094600.0,0)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(c, "MARKET_DB_PATH", str(p))
    return p


def test_index_window_populates_only_real_slots(synth_market):
    win = c.index_window("NIFTY", "2026-09-03T09:45:00+00:00")
    assert win["T0"] is not None and win["T0"]["c"] == 100.5
    assert win["T+5m"] is not None and win["T+5m"]["c"] == 101.5
    assert win["T-30m"] is None
    assert win["T+30m"] is None


def test_option_window_populates_only_real_slot(synth_market):
    # The one real row (09:45:20) sits within +/-90s tolerance of T0
    # (09:45:00), T-1m (09:44:00, 80s away) and T+1m (09:46:00, 40s away),
    # but not T-3m/T+3m or further out.
    win = c.option_window("SENSEX", "03SEP26", 76500.0, "PE", "2026-09-03T09:45:00+00:00")
    assert win["T0"] is not None and win["T0"]["ltp"] == 347.15
    assert win["T-3m"] is None
    assert win["T+3m"] is None
    assert win["T-30m"] is None and win["T+30m"] is None


def test_no_db_returns_all_none_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(c, "MARKET_DB_PATH", str(tmp_path / "missing.db"))
    win = c.index_window("NIFTY", "2026-09-03T09:45:00+00:00")
    assert all(v is None for v in win.values())


def test_build_event_window_coverage_reflects_real_population(synth_market):
    out = c.build_event_window("NIFTY", "2026-09-03T09:45:00+00:00")
    populated = sum(1 for v in out["index_window"].values() if v is not None)
    assert out["index_coverage"] == round(populated / 13, 3)
    assert out["option_window"] is None
    assert out["option_coverage"] is None


def test_build_event_window_with_option_leg(synth_market):
    out = c.build_event_window("SENSEX", "2026-09-03T09:45:00+00:00",
                               expiry="03SEP26", strike=76500.0, ce_pe="PE")
    assert out["option_window"] is not None
    populated = sum(1 for v in out["option_window"].values() if v is not None)
    assert out["option_coverage"] == round(populated / 13, 3)
    assert populated == 3   # T0, T-1m, T+1m all fall within tolerance of the one real row


def test_tslots_never_pull_a_tplus_row_or_vice_versa(synth_market):
    """T-1m and T+1m windows around a row that only exists at T0 must both
    stay None -- the tolerance window (+/-90s) must not accidentally reach
    a full minute away."""
    win = c.option_window("SENSEX", "03SEP26", 76500.0, "PE", "2026-09-03T09:44:00+00:00")
    assert win["T+1m"] is not None   # 09:45:00 target = the real 09:45:20 row, within tolerance
    win2 = c.option_window("SENSEX", "03SEP26", 76500.0, "PE", "2026-09-03T09:46:00+00:00")
    assert win2["T-1m"] is not None  # 09:45:00 target, same real row within tolerance
    assert win2["T+30m"] is None
