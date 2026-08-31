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


def test_profit_lock_banks_a_fading_scalp(fresh_db):
    """risk_ref = 2.0 (entry 100, stop 98). Trade runs to 101.4 (+0.7R) then
    fades: the locked stop (entry + 0.2R = 100.4) catches it -> small WIN via
    TRAIL, not a scratch held to the TIME clock."""
    from app.engines import paper_trading as pt
    t = pt.open_trade({"underlying": "X", "direction": "BUY", "entry": 100.0,
                       "target_1": 105.0, "stop_loss": 98.0, "quantity": 10,
                       "strategy": "SCALP", "max_hold_sec": 9999})
    assert t["risk_ref"] == 2.0
    assert pt.update_trade_price(t["trade_id"], 101.4)["status"] == "OPEN"   # +0.7R, arms the 0.2R lock
    r = pt.update_trade_price(t["trade_id"], 100.3)                          # fades below the lock
    assert r["status"] == "CLOSED" and r["exit_reason"] == "TRAIL" and r["result"] == "WIN"
    assert r["pnl"] > 0


def test_profit_lock_never_forces_a_loss(fresh_db):
    from app.engines import paper_trading as pt
    t = pt.open_trade({"underlying": "X", "direction": "BUY", "entry": 100.0,
                       "target_1": 105.0, "stop_loss": 98.0, "quantity": 10,
                       "strategy": "SCALP", "max_hold_sec": 9999})
    # never reached +0.6R -> lock never arms; original stop still governs
    assert pt.update_trade_price(t["trade_id"], 100.9)["status"] == "OPEN"
    assert pt.update_trade_price(t["trade_id"], 99.0)["status"] == "OPEN"
    r = pt.update_trade_price(t["trade_id"], 97.9)
    assert r["exit_reason"] == "STOP" and r["result"] == "LOSS"


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
    res = run_pipeline({"market": "NSE", "symbol": "BANKNIFTY", "underlying": "NIFTY", "instrument": "OPTION",
                        "candles": candles([100.0] * 60), "account": {"capital": 200000}})
    c = res["contract"]
    assert c["final_decision"] == "NO_TRADE" and c["approved"] is False
    assert c["symbol"] == "BANKNIFTY" and c["underlying"] == "BANKNIFTY"
    assert "expiry missing" in c["reason"] and "strike missing" in c["reason"]


def test_scalp_pipeline_signal_id_prefix(fresh_db):
    from app.scalp_pipeline import run_scalp_pipeline
    res = run_scalp_pipeline({"underlying": "X", "candles": candles([100.0] * 40),
                              "scalp_config": {"ignore_session": True},
                              "account": {"capital": 200000, "risk_pct": 0.5}})
    assert res["contract"]["signal_id"].startswith("SCL-")


# --------------------------------------------------- legacy SIG-*/SCL-* Telegram gate (Fix 2)
def test_log_and_notify_suppresses_no_trade_telegram(fresh_db, monkeypatch):
    from app import pipeline_core
    sent = []
    monkeypatch.setattr(pipeline_core.telegram, "notify_signal", lambda c: sent.append(c))
    base = {"signal_id": "SIG-test-1", "created_ts": "2026-08-31T00:00:00+00:00",
            "symbol": "NIFTY", "decision": "NO_TRADE", "probability": 0, "confidence": 100.0,
            "risk_reward": 0.83, "risk_status": "REJECTED", "direction": "NONE"}

    pipeline_core.log_and_notify({**base, "final_decision": "NO_TRADE"})
    assert sent == []                                   # NO_TRADE -> no Telegram card
    assert len(fresh_db.list_signals(limit=5)) == 1     # ... still written to the ledger

    pipeline_core.log_and_notify({**base, "signal_id": "SIG-test-2", "decision": "TRADE",
                                  "direction": "BUY", "final_decision": "APPROVED"})
    assert [c["signal_id"] for c in sent] == ["SIG-test-2"]   # APPROVED -> Telegram fires


def test_orchestrator_no_trade_sends_no_telegram_end_to_end(fresh_db, monkeypatch):
    from app import pipeline_core
    from app.orchestrator import run_pipeline
    sent = []
    monkeypatch.setattr(pipeline_core.telegram, "notify_signal", lambda c: sent.append(c))
    res = run_pipeline({"symbol": "X", "instrument": "FUTURES", "timeframe": "5m",
                        "candles": candles([100.0] * 60),
                        "account": {"capital": 200000, "risk_pct": 1}})
    assert res["contract"]["final_decision"] == "NO_TRADE"
    assert sent == []                                   # the misleading card never leaves


def test_notify_signal_card_names_the_instrument(monkeypatch):
    from app.connectors import telegram
    # an option contract renders "<UND> <STRIKE> <CE|PE>  (<expiry>)"
    txt = []
    monkeypatch.setattr(telegram, "_send", lambda t, c: txt.append(t) or {"ok": True})
    telegram.notify_signal({"decision": "TRADE", "direction": "BUY", "underlying": "NATURALGAS",
                            "option_type": "pe", "strike": 280, "expiry": "23SEP2026",
                            "market": "MCX", "timeframe": "5m", "risk_status": "APPROVED",
                            "entry_ref": 15.8, "stop_loss": 14.6, "target_1": 17.8})
    assert txt and "NATURALGAS 280 PE" in txt[0] and "23SEP2026" in txt[0] and "MCX" in txt[0]


def test_autoscalp_notifier_is_isolated_from_legacy_telegram_path():
    # The ASC-* autonomous scalper uses its own notifier; Fix 1/2 must not have
    # touched it and it must not route through pipeline_core / notify_signal.
    import pathlib
    src_dir = pathlib.Path(__file__).resolve().parents[1] / "app" / "autoscalp"
    blob = "\n".join(p.read_text() for p in sorted(src_dir.glob("*.py")))
    assert "pipeline_core" not in blob
    assert "notify_signal" not in blob


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
