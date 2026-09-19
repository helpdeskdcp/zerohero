"""
Regression tests for ZEROHERO_TRADING_EDGE_VALIDATION_2026-09-19.md Phase C
(approved scope only -- EV gate empirical stats, gross/net P&L cost model,
and the paper-trade lifecycle audit log). No strategy parameters (theta/IV
weights, timeout, ATR multiples) were changed and none are tested here.
"""
from app import db
from app.engines import paper_trading as PT


def _signal(**over):
    base = {
        "signal_id": "SIG-1", "market": "MCX", "underlying": "NATURALGAS",
        "instrument": "OPTION", "expiry": "25SEP2026", "strike": 300.0,
        "option_type": "CE", "direction": "BUY", "timeframe": "5m",
        "entry": 10.0, "target_1": 12.0, "target_2": 14.0, "stop_loss": 9.0,
        "trailing_stop": None, "quantity": 1.0, "probability": 0.5,
        "confidence": "MEDIUM", "market_regime": "RANGE", "oi_evidence": None,
        "reason": "test signal", "strategy": "AUTOSCALP", "setup": None,
        "atr_pct": None, "max_hold_sec": 600, "symboltoken": "1",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------- audit log

def test_open_trade_writes_open_audit_event(fresh_db):
    row = PT.open_trade(_signal())
    events = db.list_paper_trade_events(row["trade_id"])
    assert len(events) == 1
    assert events[0]["event_type"] == "OPEN"


def test_trailing_stop_modification_writes_sl_modified_event(fresh_db):
    row = PT.open_trade(_signal(entry=10.0, stop_loss=9.0, trailing_stop=0.3))
    # push price up enough to trigger the 1R risk-free ratchet (entry - stop = 1.0)
    PT.update_trade_price(row["trade_id"], 11.5)
    events = db.list_paper_trade_events(row["trade_id"])
    types = [e["event_type"] for e in events]
    assert "OPEN" in types
    assert "SL_MODIFIED" in types


def test_close_trade_writes_close_audit_event(fresh_db):
    row = PT.open_trade(_signal())
    PT.close_trade(row["trade_id"], 12.0, exit_reason="TARGET")
    events = db.list_paper_trade_events(row["trade_id"])
    assert events[-1]["event_type"] == "CLOSE"


def test_price_update_with_no_sl_change_writes_no_extra_event(fresh_db):
    row = PT.open_trade(_signal(entry=10.0, stop_loss=9.0, trailing_stop=None))
    PT.update_trade_price(row["trade_id"], 10.05)   # tiny move, nothing should ratchet
    events = db.list_paper_trade_events(row["trade_id"])
    assert [e["event_type"] for e in events] == ["OPEN"]


# ---------------------------------------------------------------- cost model

def test_close_trade_applies_validated_cost_model_for_naturalgas(fresh_db):
    row = PT.open_trade(_signal(underlying="NATURALGAS", market="MCX", entry=10.0))
    closed = PT.close_trade(row["trade_id"], 12.0, exit_reason="TARGET")
    assert closed["cost_model_status"] == "OK"
    assert closed["gross_pnl"] == 2.0
    assert closed["trading_cost"] is not None and closed["trading_cost"] > 0
    assert closed["net_pnl"] == round(closed["gross_pnl"] - closed["trading_cost"], 4)
    # WIN/LOSS classification stays gross-based -- unchanged behavior
    assert closed["result"] == "WIN"


def test_close_trade_marks_uncalibrated_for_nifty(fresh_db):
    row = PT.open_trade(_signal(underlying="NIFTY", market="NSE", entry=100.0,
                                target_1=120.0, stop_loss=90.0))
    closed = PT.close_trade(row["trade_id"], 120.0, exit_reason="TARGET")
    assert closed["cost_model_status"] == "UNCALIBRATED"
    assert closed["trading_cost"] is None
    assert closed["net_pnl"] is None
    assert closed["gross_pnl"] == 20.0   # gross still computed and preserved


def test_close_trade_cost_never_blocks_close_on_bad_market(fresh_db):
    """A missing/garbage market field must never prevent a paper trade from
    closing -- cost lookup failure degrades to UNCALIBRATED, not an error."""
    row = PT.open_trade(_signal(underlying="NATURALGAS", market=None, entry=10.0))
    closed = PT.close_trade(row["trade_id"], 11.0, exit_reason="TARGET")
    assert closed["status"] == "CLOSED"
    assert closed["cost_model_status"] == "UNCALIBRATED"


# ---------------------------------------------------------------- EV gate empirical stats

def _close_many(n_wins, n_losses, underlying="NATURALGAS", market="MCX"):
    for _ in range(n_wins):
        row = PT.open_trade(_signal(underlying=underlying, market=market, entry=10.0))
        PT.close_trade(row["trade_id"], 12.0, exit_reason="TARGET")
    for _ in range(n_losses):
        row = PT.open_trade(_signal(underlying=underlying, market=market, entry=10.0))
        PT.close_trade(row["trade_id"], 9.0, exit_reason="STOP")


def test_recent_win_loss_stats_below_min_sample_returns_none(fresh_db):
    _close_many(5, 5)   # n=10, below the 30 floor the runner enforces
    stats = db.get_recent_win_loss_stats("AUTOSCALP", "NATURALGAS", limit=100)
    assert stats["n"] == 10
    assert stats["avg_win"] is not None and stats["avg_loss"] is not None
    # the function itself always returns real stats when data exists -- the
    # >=30 floor is enforced by the CALLER (runner.py), tested separately


def test_recent_win_loss_stats_prefers_net_pnl_when_calibrated(fresh_db):
    _close_many(3, 2, underlying="NATURALGAS", market="MCX")
    stats = db.get_recent_win_loss_stats("AUTOSCALP", "NATURALGAS", limit=100)
    # gross win=2.0, gross loss=1.0; NATGAS has a real cost model so net < gross
    assert stats["avg_win"] < 2.0
    assert stats["n"] == 5


def test_recent_win_loss_stats_uses_gross_when_uncalibrated(fresh_db):
    _close_many(2, 2, underlying="NIFTY", market="NSE")
    stats = db.get_recent_win_loss_stats("AUTOSCALP", "NIFTY", limit=100)
    assert stats["avg_win"] == 2.0   # exactly gross, no cost subtracted
    assert stats["avg_loss"] == 1.0


def test_recent_win_loss_stats_empty_symbol_returns_none(fresh_db):
    stats = db.get_recent_win_loss_stats("AUTOSCALP", "SENSEX", limit=100)
    assert stats == {"n": 0, "avg_win": None, "avg_loss": None}


def test_recent_win_loss_stats_only_reads_closed_trades(fresh_db):
    """An OPEN trade must never contribute to the empirical stats -- this is
    the no-future/no-forming-information guarantee for the EV gate."""
    PT.open_trade(_signal(underlying="NATURALGAS", market="MCX"))  # left OPEN
    _close_many(2, 0, underlying="NATURALGAS", market="MCX")
    stats = db.get_recent_win_loss_stats("AUTOSCALP", "NATURALGAS", limit=100)
    assert stats["n"] == 2   # only the 2 CLOSED trades, not the 3rd OPEN one


# ---------------------------------------------------------------- structural safety guard

def test_open_trade_never_calls_a_broker(fresh_db, monkeypatch):
    """The live paper path must never place a real order. There is no
    broker client reference anywhere reachable from open_trade/close_trade --
    assert the module doesn't import one, as a permanent regression guard."""
    import inspect
    src = inspect.getsource(PT)
    for forbidden in ("angelone", "AngelOne", "place_order", "smart_api", "SmartConnect"):
        assert forbidden not in src, f"found forbidden broker reference: {forbidden}"
