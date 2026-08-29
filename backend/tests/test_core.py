"""Core plumbing — paper-trade lifecycle, gate, combos, WS parser, DB lease."""
import struct
import time

from conftest import candles


# ---------------------------------------------------------------- paper_trading
def test_scalp_trade_hits_target(fresh_db):
    from app.engines import paper_trading as pt
    t = pt.open_trade({"underlying": "X", "direction": "BUY", "entry": 100.0,
                       "target_1": 102.0, "stop_loss": 98.0, "quantity": 10,
                       "strategy": "SCALP", "max_hold_sec": 9999})
    r = pt.update_trade_price(t["trade_id"], 101.0)
    assert r["status"] == "OPEN"
    r = pt.update_trade_price(t["trade_id"], 102.5)
    assert r["status"] == "CLOSED" and r["exit_reason"] == "TARGET" and r["result"] == "WIN"


def test_scalp_trade_hits_stop(fresh_db):
    from app.engines import paper_trading as pt
    t = pt.open_trade({"underlying": "X", "direction": "BUY", "entry": 100.0,
                       "target_1": 102.0, "stop_loss": 98.0, "quantity": 5,
                       "strategy": "SCALP", "max_hold_sec": 9999})
    r = pt.update_trade_price(t["trade_id"], 97.5)
    assert r["status"] == "CLOSED" and r["exit_reason"] == "STOP" and r["result"] == "LOSS"


def test_manual_position_is_monitor_only(fresh_db):
    from app.engines import paper_trading as pt
    t = pt.open_trade({"underlying": "X", "option_type": "CE", "strike": 100,
                       "direction": "BUY", "entry": 10.0, "target_1": 12.0,
                       "stop_loss": 9.0, "quantity": 50, "strategy": "MANUAL",
                       "trailing_stop": 0, "max_hold_sec": None})
    r = pt.update_trade_price(t["trade_id"], 13.0)      # past target
    assert r["status"] == "OPEN"                         # never auto-closes
    assert r["_hit"] == "TARGET"
    r = pt.update_trade_price(t["trade_id"], 8.0)        # past stop
    assert r["status"] == "OPEN" and r["_hit"] == "STOP"


# ---------------------------------------------------------------- NO-TRADE gate
def test_gate_blocks_when_scalp_says_no_trade(fresh_db):
    from app.scalp_pipeline import run_scalp_pipeline
    res = run_scalp_pipeline({"underlying": "X", "candles": candles([100.0] * 40),
                              "scalp_config": {"ignore_session": True},
                              "account": {"capital": 200000, "risk_pct": 0.5}})
    assert res["contract"]["final_decision"] == "NO_TRADE"
    assert res["trade"] is None


def test_gate_opens_a_trade_when_all_stages_pass(fresh_db):
    from app.scalp_pipeline import run_scalp_pipeline
    px = [100 + i * 0.03 for i in range(39)]
    cds = candles(px)
    prior_hi = max(r[2] for r in cds[-6:-1])
    cds.append([cds[-1][0] + 60, px[-1], prior_hi + 0.35, px[-1] - 0.02, prior_hi + 0.30, 3200])
    res = run_scalp_pipeline({
        "underlying": "X", "candles": cds,
        "scalp_config": {"ignore_session": True},
        "account": {"capital": 200000, "risk_pct": 0.5, "available_margin": 200000},
        "risk_instrument": {"lot_size": 1}})
    assert res["contract"]["final_decision"] == "APPROVED"
    assert res["trade"] and res["trade"]["strategy"] == "SCALP"


# ---------------------------------------------------------------- orchestrator (core pipeline)
def test_orchestrator_no_trade_still_logs_a_signal(fresh_db):
    from app.orchestrator import run_pipeline
    res = run_pipeline({"symbol": "X", "instrument": "FUTURES", "timeframe": "5m",
                        "candles": candles([100.0] * 60),
                        "account": {"capital": 200000, "risk_pct": 1}})
    c = res["contract"]
    assert c["final_decision"] == "NO_TRADE" and res["trade"] is None
    # contract keeps its full shape and a row lands in the ledger
    for k in ("signal_id", "decision", "risk_status", "data_status", "model_version"):
        assert k in c
    assert len(fresh_db.list_signals(limit=10)) == 1
    assert fresh_db.list_signals(limit=1)[0]["signal_id"].startswith("SIG-")


def test_orchestrator_rejects_missing_snapshot_and_contract_fields(fresh_db):
    from app.orchestrator import run_pipeline
    res = run_pipeline({"market": "NSE", "symbol": "NIFTY", "instrument": "OPTION",
                        "candles": candles([100.0] * 60), "account": {"capital": 200000}})
    c = res["contract"]
    assert c["final_decision"] == "NO_TRADE" and c["approved"] is False
    assert c["data_age_seconds"] is None
    assert "data timestamp missing" in c["reason"]
    assert "expiry missing" in c["reason"] and "strike missing" in c["reason"]


def test_scalp_pipeline_signal_id_prefix(fresh_db):
    from app.scalp_pipeline import run_scalp_pipeline
    res = run_scalp_pipeline({"underlying": "X", "candles": candles([100.0] * 40),
                              "scalp_config": {"ignore_session": True},
                              "account": {"capital": 200000, "risk_pct": 0.5}})
    assert res["contract"]["signal_id"].startswith("SCL-")


# ---------------------------------------------------------------- combos
def test_combo_stop_alerts_once(fresh_db):
    from app.engines.paper_trading import open_trade
    from app import combos
    ce = open_trade({"underlying": "NG", "option_type": "CE", "strike": 275,
                     "direction": "BUY", "entry": 16.0, "quantity": 100, "strategy": "MANUAL"})
    pe = open_trade({"underlying": "NG", "option_type": "PE", "strike": 280,
                     "direction": "BUY", "entry": 14.0, "quantity": 100, "strategy": "MANUAL"})
    c = combos.create([ce["trade_id"], pe["trade_id"]], stop_combined=25.0, target_combined=40.0)
    # drive both legs' marked pnl down so combined mark ≈ 24
    from app import db
    db.update_trade(ce["trade_id"], {"pnl": (12.5 - 16.0) * 100})
    db.update_trade(pe["trade_id"], {"pnl": (11.5 - 14.0) * 100})
    fired = combos.evaluate()
    assert len(fired) == 1 and fired[0]["reason"] == "COMBO_STOP"
    assert combos.evaluate() == []          # latched — no repeat


def test_combo_breaks_when_a_leg_closes(fresh_db):
    from app.engines.paper_trading import open_trade, close_trade
    from app import combos
    ce = open_trade({"underlying": "NG", "option_type": "CE", "strike": 275,
                     "direction": "BUY", "entry": 16.0, "quantity": 100, "strategy": "MANUAL"})
    pe = open_trade({"underlying": "NG", "option_type": "PE", "strike": 280,
                     "direction": "BUY", "entry": 14.0, "quantity": 100, "strategy": "MANUAL"})
    combos.create([ce["trade_id"], pe["trade_id"]])
    close_trade(pe["trade_id"], 14.0, exit_reason="MANUAL")
    combos.evaluate()
    assert all(x["status"] != "OPEN" for x in combos.load().values())


# ---------------------------------------------------------------- angel_ws parser
def test_parse_binary_ltp_packets():
    from app.connectors.angel_ws import parse_binary

    def pkt(token, ex, price):
        b = bytearray(51)
        b[0] = 1
        b[1] = ex
        tb = token.encode()
        b[2:2 + len(tb)] = tb
        struct.pack_into("<q", b, 35, int(time.time() * 1000))
        struct.pack_into("<q", b, 43, int(round(price * 100)))
        return bytes(b)

    frame = pkt("99926000", 1, 22150.25) + pkt("578508", 5, 14.35) + b"\x00\x00"
    ticks = parse_binary(frame)
    assert [t["token"] for t in ticks] == ["99926000", "578508"]
    assert abs(ticks[0]["ltp"] - 22150.25) < 1e-6
    assert abs(ticks[1]["ltp"] - 14.35) < 1e-6


# ---------------------------------------------------------------- DB lease + dedupe
def test_lease_is_single_holder(fresh_db):
    db = fresh_db
    assert db.lease_acquire("k", "A", ttl_sec=5) is True
    assert db.lease_acquire("k", "B", ttl_sec=5) is False
    assert db.lease_acquire("k", "A", ttl_sec=5) is True     # renew
    assert db.lease_owner("k") == "A"


def test_lease_steal_after_ttl(fresh_db):
    db = fresh_db
    assert db.lease_acquire("k", "A", ttl_sec=1) is True
    time.sleep(1.2)
    assert db.lease_acquire("k", "B", ttl_sec=1) is True
    assert db.lease_owner("k") == "B"
    db.lease_release("k", "B")
    assert db.lease_owner("k") is None


def test_find_open_by_token(fresh_db):
    from app.engines.paper_trading import open_trade
    open_trade({"underlying": "X", "direction": "BUY", "entry": 10, "quantity": 1,
                "strategy": "MANUAL", "symboltoken": "578429"})
    assert fresh_db.find_open_by_token("578429", "MANUAL") is not None
    assert fresh_db.find_open_by_token("999999", "MANUAL") is None
