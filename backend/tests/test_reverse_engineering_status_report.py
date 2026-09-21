"""app.reverse_engineering.status_report -- isolated synthetic DBs only."""
import sqlite3

import pytest

from app.reverse_engineering import status_report as sr


@pytest.fixture()
def synth_market(tmp_path):
    p = tmp_path / "market_history.db"
    conn = sqlite3.connect(p)
    conn.executescript("""
        CREATE TABLE market_candles (symbol TEXT, kind TEXT, session_date_ist TEXT);
        CREATE TABLE quote_snapshots (symbol TEXT, kind TEXT, session_date_ist TEXT, strike REAL);
    """)
    conn.execute("INSERT INTO market_candles VALUES ('NIFTY','INDEX','2026-09-01')")
    conn.execute("INSERT INTO market_candles VALUES ('NIFTY','INDEX','2026-09-02')")
    conn.execute("INSERT INTO quote_snapshots VALUES ('SENSEX','OPTION','2026-09-03', 76500.0)")
    conn.execute("INSERT INTO quote_snapshots VALUES ('SENSEX','OPTION','2026-09-03', 76500.0)")
    conn.execute("INSERT INTO quote_snapshots VALUES ('SENSEX','OPTION','2026-09-03', 77000.0)")
    conn.commit()
    conn.close()
    return str(p)


def test_status_report_uses_real_queries_not_fabricated_numbers(tmp_path, synth_market, monkeypatch):
    from app.orderflow import depth as d
    monkeypatch.setattr(d, "snapshot_for_symbol",
                        lambda sym, at_or_before=None: {"available": False, "note": "test"})
    research_db = str(tmp_path / "research_events.db")
    out = sr.build_status_report(research_db_path=research_db, market_db_path=synth_market)
    assert out["events_captured"] == 0
    assert out["model_readiness"]["model_status"] == "NOT_READY"
    cov = {row["symbol"]: row for row in out["data_coverage"]["by_symbol_kind"]}
    assert cov["NIFTY"]["n"] == 2
    density = out["option_chain_density"]["per_session"][0]
    assert density["n_snapshots"] == 3 and density["n_strikes"] == 2


def test_status_report_missing_market_db_does_not_raise(tmp_path):
    out = sr.build_status_report(research_db_path=str(tmp_path / "research_events.db"),
                                 market_db_path=str(tmp_path / "does_not_exist.db"))
    assert out["data_coverage"]["available"] is False
    assert out["option_chain_density"]["available"] is False
