"""
SLICE 4 — DB tables + paper-trade state machine.

Offline: fresh temp DB, market_context + the scanner are monkeypatched. The
paper engine reuses engines.paper_trading (ai_paper_trades) and
autoscalp.safeguards — never a real order.
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))


def _fresh(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("CHANAKYA_DB_PATH", os.path.join(d, "t.db"))
    import importlib
    import app.db as db
    importlib.reload(db)
    db.init_db()
    return db


# --------------------------------------------------------------- state machine
from app.smart_index_scalper import state_machine as sm


def _scan_row(**over):
    base = {
        "index": "NIFTY", "status": "OK", "eligible": True,
        "eligibility": {"failed": []},
        "direction": "CE", "signal_type": "BUY_CE",
        "confidence": 78, "index_selection_score": 74, "confluence_score": 70,
        "risk_reward": [1.9, 3.1, 4.0],
        "reason_codes": ["nearest zone strength 72 (4 families)", "PUT support wall @ 23900",
                         "volume 1.4x recent average", "candle structure: hammer, lower_wick"],
        "selected_option": {"status": "OK", "selected_strike": 24000, "option_type": "CE",
                            "option_ltp": 95, "selection_score": 71,
                            "candidates": [{"strike": 24000, "expected_premium_move": 26}],
                            "expected_index_move_pts": 60},
        "nearest_support": 23950, "nearest_resistance": 24080,
    }
    base.update(over)
    return base


BAL = {"min_confidence": 72, "min_selection_score": 68, "min_rr1": 1.4,
       "required_confirmations": ["level", "oi", "price_action"], "allowed_option_distance": 2}


def test_pre_entry_confirms_only_when_every_gate_passes():
    d = sm.pre_entry_state(_scan_row(), BAL)
    assert d["state"] == "ENTRY_CONFIRMED" and d["action"] == "OPEN_PAPER"


def test_pre_entry_gates_block_and_name_the_reason():
    assert sm.pre_entry_state(_scan_row(confidence=60), BAL)["state"] in ("WATCHING", "SETUP_FORMING")
    assert sm.pre_entry_state(_scan_row(risk_reward=[0.9, 1, 1]), BAL)["state"] in ("WATCHING", "SETUP_FORMING", "NO_TRADE")
    d = sm.pre_entry_state(_scan_row(reason_codes=["nearest zone strength 72"]), BAL)  # missing oi + price_action
    assert d["state"] in ("ENTRY_READY", "SETUP_FORMING", "WATCHING")
    assert "missing confirmations" in d["reason"]
    ne = sm.pre_entry_state(_scan_row(eligible=False, eligibility={"failed": ["liquidity"]}), BAL)
    assert ne["state"] == "NO_TRADE" and "liquidity" in ne["reason"]
    nt = sm.pre_entry_state(_scan_row(signal_type="NO_TRADE", direction="NONE",
                                      no_trade_reason="conflicting OI"), BAL)
    assert nt["state"] == "NO_TRADE" and "conflicting OI" in nt["reason"]


def test_in_trade_profit_protection_and_invalidation():
    pos = {"entry": 100.0, "stop_loss": 80.0, "target_1": 140.0, "option_type": "CE",
           "risk_ref": 20.0, "mfe": 26.0}
    # up 0.6R+ (MFE 26 > 12) then given back to +4 -> EXIT_WARNING PROTECT
    d = sm.in_trade_state(position=pos, mark=104.0, engine_out=None, profile=BAL)
    assert d["state"] == "EXIT_WARNING" and d["action"] in ("PROTECT", "CLOSE")
    # engine flips to PE with confidence -> INVALIDATED CLOSE
    d2 = sm.in_trade_state(position={**pos, "mfe": 2.0}, mark=99.0,
                           engine_out={"status": "OK", "direction": "PE", "confidence": 60}, profile=BAL)
    assert d2["state"] == "INVALIDATED" and d2["action"] == "CLOSE"
    # normal: just mark
    d3 = sm.in_trade_state(position={**pos, "mfe": 3.0}, mark=103.0, engine_out=None, profile=BAL)
    assert d3["action"] == "UPDATE_MARK"
    # hard stop
    d4 = sm.in_trade_state(position=pos, mark=79.0, engine_out=None, profile=BAL)
    assert d4["state"] == "STOPPED" and d4["action"] == "CLOSE"


# --------------------------------------------------------------- DB tables + journal
def test_smart_scalper_db_helpers_roundtrip(monkeypatch):
    db = _fresh(monkeypatch)
    assert db.insert_smart_scalper_signal({"signal_id": "SS-1", "created_ts": "2026-09-04T09:30:00Z",
                                           "instrument": "NIFTY", "profile": "BALANCED",
                                           "direction": "CE", "signal_type": "BUY_CE",
                                           "confidence": 78, "state": "PAPER_OPEN"}) is True
    assert db.insert_smart_scalper_signal({"signal_id": "SS-1"}) is False       # write-once
    db.update_smart_scalper_signal("SS-1", {"state": "EXITED", "trade_id": "TRD-x"})
    got = db.list_smart_scalper_signals()[0]
    assert got["state"] == "EXITED" and got["trade_id"] == "TRD-x"
    db.log_smart_scalper_state({"ts": "t", "signal_id": "SS-1", "trade_id": "TRD-x",
                                "instrument": "NIFTY", "profile": "BALANCED",
                                "from_state": "PAPER_OPEN", "to_state": "EXITED",
                                "action": "CLOSED", "reason": "target", "spot": 24000,
                                "option_mark": 140, "pnl": 45, "mfe": 50, "mae": 5})
    assert db.list_smart_scalper_states(signal_id="SS-1")[0]["to_state"] == "EXITED"


def test_journal_metrics_from_closed_paper_trades(monkeypatch):
    db = _fresh(monkeypatch)
    from app.engines.paper_trading import open_trade, close_trade
    for i, (entry, exitp) in enumerate([(100, 140), (100, 80), (100, 130), (100, 82)]):
        sid = f"SS-{i}"
        db.insert_smart_scalper_signal({"signal_id": sid, "created_ts": "t", "instrument": "NIFTY",
                                        "profile": "BALANCED", "market_regime": "BULLISH_EXPANSION",
                                        "state": "PAPER_OPEN"})
        tr = open_trade({"signal_id": sid, "underlying": "NIFTY", "instrument": "OPTION",
                         "option_type": "CE", "direction": "BUY", "entry": entry,
                         "stop_loss": 80, "target_1": 140, "quantity": 1, "strategy": "SMART_SCALPER"})
        close_trade(tr["trade_id"], float(exitp), forced_result="WIN" if exitp > entry else "LOSS",
                    exit_reason="TARGET" if exitp > entry else "STOP")
    from app.smart_index_scalper.journal import journal
    j = journal()
    o = j["overall"]
    assert o["n"] == 4 and o["wins"] == 2 and o["losses"] == 2
    assert o["win_rate"] == 0.5 and o["profit_factor"] is not None
    assert "BALANCED" in j["by_profile"] and "NIFTY" in j["by_instrument"]
    assert "BULLISH_EXPANSION" in j["by_market_regime"]
    assert "no profitability claim" in j["note"]


def test_paper_engine_never_places_a_real_order():
    src = Path(__file__).parents[1] / "app" / "smart_index_scalper"
    joined = "\n".join(p.read_text() for p in src.glob("*.py"))
    for banned in ("place_order", "placeOrder", "OrderManager", "live_trading = true",
                   "live_trading=true", "LIVE_TRADING = True"):
        assert banned not in joined
    # it DOES use the paper engine + safeguards (allowed)
    assert "from ..engines.paper_trading import" in joined
    assert "Safeguards" in joined
