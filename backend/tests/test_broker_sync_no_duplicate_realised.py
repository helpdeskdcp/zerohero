"""Regression (found 2026-10-15): the broker-position sync recorded the broker's day-realised P&L as a NEW MANUAL row on every sync after a mirror
row had been closed through another path (its reason was not 'broker realised …', so the (token, reason) dedupe missed it). One real result
was counted up to 5 times (9-Oct: five rows of -265; 13-Oct: -8,567 twice plus two cumulative copies)."""
import asyncio

import pytest


def positions(tok="44623", realised=-8567.0):
    return {"status": "OK", "positions": [], "closed": [{"symboltoken": tok, "symbol": "NIFTY", "option_type": "PE", "strike": 22750,
                                                          "expiry": "20OCT2026", "exchange": "NFO", "buy_avg": 77.8, "sell_avg": 44.85,
                                                          "traded_qty": 260, "realised_pnl": realised}]}


def runner(fresh_db, monkeypatch, res):
    from app import scalper as S
    monkeypatch.setattr(S.angelone, "fetch_positions", lambda: res)
    r = S.ScalpRunner()
    r._mkt_open = True
    return r


def manual_closed(fresh_db):
    return [t for t in fresh_db.list_trades(status="CLOSED", limit=200, strategy="MANUAL")]


def test_same_broker_realised_is_recorded_once_even_if_a_mirror_row_already_closed_the_position(fresh_db, monkeypatch):
    from app.engines.paper_trading import close_trade, open_trade
    row = open_trade({"signal_id": None, "market": "NFO", "underlying": "NIFTY", "instrument": "OPTION", "expiry": "20OCT2026", "strike": 22750,
                      "option_type": "PE", "direction": "BUY", "timeframe": "", "entry": 77.8, "target_1": None, "stop_loss": None,
                      "trailing_stop": 0, "quantity": 260, "market_regime": "", "oi_evidence": "", "reason": "auto-synced from Angel One positions",
                      "strategy": "MANUAL", "setup": None, "atr_pct": None, "max_hold_sec": None, "symboltoken": "44623"})
    close_trade(row["trade_id"], 44.85, None, "BROKER_FLAT")                     # mirror closed via the broker-flat path (reason stays 'auto-synced…')
    assert len(manual_closed(fresh_db)) == 1
    r = runner(fresh_db, monkeypatch, positions())
    for _ in range(3):                                                           # three consecutive syncs, same realised figure
        asyncio.run(r._sync_broker_positions({"broker_sync_sec": 0}))
        r._last_broker_sync = 0
    assert len(manual_closed(fresh_db)) == 1, "broker-realised copy was added although the token already has a closed row today"


def test_a_position_never_mirrored_is_still_recorded_exactly_once(fresh_db, monkeypatch):
    r = runner(fresh_db, monkeypatch, positions(tok="55555", realised=-100.0))
    for _ in range(3):
        asyncio.run(r._sync_broker_positions({"broker_sync_sec": 0}))
        r._last_broker_sync = 0
    rows = manual_closed(fresh_db)
    assert len(rows) == 1 and rows[0]["symboltoken"] == "55555" and rows[0]["pnl"] == pytest.approx(-100.0)
