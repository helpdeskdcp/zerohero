"""Regression coverage for fail-closed Live Monitor presentation and inputs."""
import asyncio
import math

import pytest
from fastapi import HTTPException


def _status(marks):
    return {
        "armed": False, "running": True, "is_leader": True, "runner_owner": "test",
        "feed": {"connected": True, "last_msg_age_sec": 0, "marks": marks},
        "execution": {},
    }


def _open_manual(direction, entry=100.0, target=110.0, stop=90.0, token="T1"):
    from app.engines.paper_trading import open_trade
    return open_trade({
        "underlying": "SAFE", "direction": direction, "entry": entry,
        "target_1": target, "stop_loss": stop, "quantity": 1,
        "strategy": "MANUAL", "symboltoken": token,
    })


def _monitor(monkeypatch, marks):
    from app import main
    monkeypatch.setattr(main.scalp_runner, "status", lambda: _status(marks))
    return main.api_monitor()


def test_monitor_buy_distances_and_hits(fresh_db, monkeypatch):
    _open_manual("BUY", token="buy")
    row = _monitor(monkeypatch, {"buy": {"ltp": 105, "age_sec": 12}})["positions"][0]
    assert row["dist_to_target"] == 5 and row["dist_to_stop"] == 15
    assert row["hit"] is None and row["monitor_status"] == "OPEN"

    row = _monitor(monkeypatch, {"buy": {"ltp": 111, "age_sec": 1}})["positions"][0]
    assert row["hit"] == "TARGET" and row["monitor_status"] == "TARGET_HIT"

    row = _monitor(monkeypatch, {"buy": {"ltp": 89, "age_sec": 1}})["positions"][0]
    assert row["hit"] == "STOP" and row["monitor_status"] == "STOP_HIT"


def test_monitor_sell_distances_and_hits(fresh_db, monkeypatch):
    _open_manual("SELL", target=90, stop=110, token="sell")
    row = _monitor(monkeypatch, {"sell": {"ltp": 95, "age_sec": 1}})["positions"][0]
    assert row["dist_to_target"] == 5 and row["dist_to_stop"] == 15
    assert row["hit"] is None and row["monitor_status"] == "OPEN"

    row = _monitor(monkeypatch, {"sell": {"ltp": 89, "age_sec": 1}})["positions"][0]
    assert row["hit"] == "TARGET" and row["monitor_status"] == "TARGET_HIT"

    row = _monitor(monkeypatch, {"sell": {"ltp": 111, "age_sec": 1}})["positions"][0]
    assert row["hit"] == "STOP" and row["monitor_status"] == "STOP_HIT"


def test_monitor_stale_or_unavailable_quote_fails_closed(fresh_db, monkeypatch):
    _open_manual("BUY", token="stale")
    row = _monitor(monkeypatch, {"stale": {"ltp": 120, "age_sec": 12.1}})["positions"][0]
    assert row["freshness"] == "STALE" and row["stale_age_sec"] == 12.1
    assert row["mark"] is None and row["live_pnl"] is None
    assert row["dist_to_target"] is None and row["dist_to_stop"] is None
    assert row["hit"] is None and row["monitor_status"] == "STALE_DATA"


def test_feed_freshness_boundary_and_stale_get_ltp():
    from app.connectors.angel_ws import AngelMarketFeed, LTP_MAX_AGE_SEC, is_ltp_fresh

    assert is_ltp_fresh(LTP_MAX_AGE_SEC)
    assert not is_ltp_fresh(LTP_MAX_AGE_SEC + 0.01)
    assert not is_ltp_fresh(None) and not is_ltp_fresh(float("nan"))
    feed = AngelMarketFeed()
    feed.ltp["x"] = {"ltp": 100, "recv": 0}
    assert feed.get_ltp("x") is None


def test_runner_does_not_turn_stale_manual_data_into_an_alert(monkeypatch):
    from app.scalper import ScalpRunner

    runner = ScalpRunner()
    runner.feed.ltp["manual"] = {"ltp": 120, "recv": 0}
    monkeypatch.setattr(runner, "_rest_mark_for", lambda *_: pytest.fail("stale REST fallback used"))
    trade = {"strategy": "MANUAL", "symboltoken": "manual", "direction": "BUY",
             "entry": 100, "option_type": "", "underlying": "SAFE"}
    assert runner._ltp_for(trade, {"watchlist": []}) is None


@pytest.mark.parametrize("direction", ["", "HOLD", "NONE", None])
def test_track_rejects_invalid_or_missing_direction(direction):
    from app import main
    if direction is None:
        with pytest.raises(Exception):
            main.TrackPositionRequest(symbol="X", symboltoken="1", entry=100, target=110, stop=90)
        return
    req = main.TrackPositionRequest(symbol="X", symboltoken="1", direction=direction,
                                    entry=100, target=110, stop=90)
    with pytest.raises(HTTPException, match="direction must be BUY or SELL"):
        asyncio.run(main.api_track_position(req))


@pytest.mark.parametrize("direction,target,stop", [
    ("BUY", 90, 80), ("BUY", 110, 101),
    ("SELL", 110, 120), ("SELL", 90, 99),
])
def test_track_rejects_reversed_levels(direction, target, stop):
    from app import main
    req = main.TrackPositionRequest(symbol="X", symboltoken="1", direction=direction,
                                    entry=100, target=target, stop=stop)
    with pytest.raises(HTTPException, match="invalid"):
        asyncio.run(main.api_track_position(req))


@pytest.mark.parametrize("field,value", [
    ("entry", 0), ("target", -1), ("stop", 0),
    ("target", math.nan), ("stop", math.inf), ("trailing_stop", -1),
    ("trailing_stop", math.nan),
])
def test_track_rejects_non_finite_or_non_positive_levels(field, value):
    from app import main
    payload = {"symbol": "X", "symboltoken": "1", "direction": "BUY",
               "entry": 100, "target": 110, "stop": 90}
    payload[field] = value
    req = main.TrackPositionRequest(**payload)
    with pytest.raises(HTTPException, match="finite"):
        asyncio.run(main.api_track_position(req))


def test_levels_reject_invalid_existing_direction_and_preserves_optional_levels(fresh_db):
    from app import main
    t = _open_manual("BUY", target=None, stop=None, token="optional")
    req = main.LevelsRequest(trade_id=t["trade_id"], target=110)
    updated = asyncio.run(main.api_position_levels(req))
    assert updated["target_1"] == 110 and updated["stop_loss"] is None

    fresh_db.update_trade(t["trade_id"], {"direction": "INVALID"})
    with pytest.raises(HTTPException, match="direction must be BUY or SELL"):
        asyncio.run(main.api_position_levels(main.LevelsRequest(trade_id=t["trade_id"], stop=90)))


def test_paper_monitor_ignores_invalid_direction_or_quote(fresh_db):
    from app.engines.paper_trading import update_trade_price
    t = _open_manual("BUY", token="quote")
    fresh_db.update_trade(t["trade_id"], {"direction": "INVALID"})
    assert update_trade_price(t["trade_id"], 200)["pnl"] == 0
    fresh_db.update_trade(t["trade_id"], {"direction": "BUY"})
    assert update_trade_price(t["trade_id"], float("nan"))["pnl"] == 0
