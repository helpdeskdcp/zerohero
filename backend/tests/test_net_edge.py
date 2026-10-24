import numpy as np
import pandas as pd
import pytest

from app.autoscalp import net_edge as NE, safeguards as SG
from app.execution.angelone_broker import AngelOneBroker, LiveDisabled
from app.execution.broker_base import OrderReq


def _rows(sym, n, mean_pts, sd=3.0, seed=1, entry=100.0):
    rng = np.random.default_rng(seed); days = pd.date_range("2026-09-01", periods=n // 2 + 1).strftime("%Y-%m-%d")
    return pd.DataFrame({"id": range(n), "symbol": sym, "created_ts": [days[i // 2] + "T05:00:00" for i in range(n)], "entry": entry, "exit_price": entry + rng.normal(mean_pts, sd, n), "exit_reason": "TIME"})


LOT = lambda und: 100
FR = lambda und, e, x, lot: 50.0                                   # flat Rs 50 friction


def test_analyze_statuses_and_numbers():
    rows = pd.concat([_rows("LOSER", 60, -1.0, seed=1), _rows("WINNER", 120, 2.5, seed=2), _rows("FEW", 10, 5.0, seed=3), _rows("FLAT", 80, 0.5, seed=4)], ignore_index=True)
    out = NE.analyze(rows, lot_fn=LOT, friction_fn=FR)
    assert out["LOSER"]["status"] == "SUSPEND_RECOMMENDED" and out["LOSER"]["net_rs_per_trade"] < -100
    assert out["WINNER"]["status"] == "EVIDENCE_OK" and out["WINNER"]["net_ci90"][0] > 0
    assert out["FEW"]["status"] == "WATCH" and out["FLAT"]["status"] == "WATCH"                      # too few trades / CI includes 0
    assert abs(out["WINNER"]["friction_rs_per_trade"] - 50.0) < 1e-9 and out["ALL"]["n"] == 270
    assert abs(out["LOSER"]["gross_rs_per_trade"] - out["LOSER"]["net_rs_per_trade"] - 50.0) < 1e-6


def test_underlying_of():
    assert NE.underlying_of("SENSEX2610872500PE") == "SENSEX" and NE.underlying_of("BANKNIFTY26OCT55000CE") == "BANKNIFTY" and NE.underlying_of("NIFTY26OCT22600PE") == "NIFTY"
    assert NE.underlying_of("CRUDEOILM26OCT9800CE") == "CRUDEOILM" and NE.underlying_of("XYZ") is None


def test_safeguard_net_edge_enforce_switch(monkeypatch):
    monkeypatch.setattr(NE, "symbol_status", lambda s, db_path=None: "SUSPEND_RECOMMENDED")
    monkeypatch.setattr(SG.killswitch, "is_active", lambda: False); monkeypatch.setattr(SG.Safeguards, "_trades_today", lambda self: 0); monkeypatch.setattr(SG.Safeguards, "_daily_realised", lambda self: 0.0)
    kw = dict(underlying="CRUDEOIL", side="BUY_CE", open_keys=set(), feed_connected=True, feed_age_sec=1.0, open_count=0, exchange="NSE")
    base = {"allow_weekend": True, "session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59", "max_feed_age_sec": 99}
    import inspect
    sig = inspect.signature(SG.Safeguards.check_entry)
    ok_off, _ = SG.Safeguards(base).check_entry(**{k: v for k, v in kw.items() if k in sig.parameters})
    ok_on, why = SG.Safeguards({**base, "net_edge_enforce": True}).check_entry(**{k: v for k, v in kw.items() if k in sig.parameters})
    assert ok_off is True and ok_on is False and "net-edge suspended" in why


def _req(ts="SENSEX2610872500PE"):
    return OrderReq(client_tag="T", trade_id="T1", leg="ENTRY", side="BUY", order_type="MARKET", symbol=ts, symboltoken="1", exchange="BFO", quantity=20, tradingsymbol=ts)


def test_live_entry_blocked_without_evidence_exits_never(monkeypatch):
    monkeypatch.setenv("CHANAKYA_ALLOW_LIVE", "1"); monkeypatch.setenv("CHANAKYA_LIVE_CONFIRM_TOKEN", "tok"); monkeypatch.delenv("CHANAKYA_NET_EDGE_OVERRIDE", raising=False)
    monkeypatch.setattr(NE, "report", lambda db_path=None, max_age=300.0: {"per_symbol": {"SENSEX": {"n": 178, "net_rs_per_trade": -265.0, "net_ci90": [-356, -169], "status": "SUSPEND_RECOMMENDED"}}})
    import app.connectors.angelone_orders as ao
    calls = []
    monkeypatch.setattr(ao, "place_order", lambda body: calls.append(body) or {"status": "OK", "data": {"orderid": "1", "uniqueorderid": "u"}, "raw": {}})
    b = AngelOneBroker(config={"execution_mode": "LIVE"})
    with pytest.raises(LiveDisabled) as e: b.market_entry(_req())
    assert "net-edge evidence gate" in str(e.value) and calls == []                                         # blocked BEFORE any broker call
    assert b.stoploss_market(_req()).ok and b.target_exit(_req()).ok and len(calls) == 2                       # exits are never blocked
    monkeypatch.setenv("CHANAKYA_NET_EDGE_OVERRIDE", "1"); assert b.market_entry(_req()).ok                   # explicit owner bypass
    monkeypatch.delenv("CHANAKYA_NET_EDGE_OVERRIDE"); monkeypatch.setattr(NE, "report", lambda db_path=None, max_age=300.0: {"per_symbol": {"SENSEX": {"n": 150, "net_rs_per_trade": 120.0, "net_ci90": [10, 230], "status": "EVIDENCE_OK"}}})
    assert b.market_entry(_req()).ok
    assert NE.live_evidence_ok("UNKNOWNXYZ")[0] is False


def test_safeguard_enforce_symbols_only_listed(monkeypatch):
    monkeypatch.setattr(NE, "symbol_status", lambda s, db_path=None: "SUSPEND_RECOMMENDED")
    monkeypatch.setattr(SG.killswitch, "is_active", lambda: False); monkeypatch.setattr(SG.Safeguards, "_trades_today", lambda self: 0); monkeypatch.setattr(SG.Safeguards, "_daily_realised", lambda self: 0.0)
    base = {"allow_weekend": True, "session_start_hhmm": "00:00", "daily_cutoff_hhmm": "23:59", "mcx_daily_cutoff_hhmm": "23:59", "max_feed_age_sec": 99, "net_edge_enforce_symbols": ["CRUDEOIL", "naturalgas"]}
    kw = dict(side="BUY_CE", open_keys=set(), feed_connected=True, feed_age_sec=1.0, open_count=0, exchange="MCX")
    sg = SG.Safeguards(base)
    assert sg.check_entry(underlying="CRUDEOIL", **kw)[0] is False and "net-edge suspended" in sg.check_entry(underlying="NATURALGAS", **kw)[1]
    assert "net-edge" not in sg.check_entry(underlying="SENSEX", **kw)[1]                      # unlisted symbol is not touched by the list


def test_underlying_edge_uses_signal_direction_and_futures_cost(tmp_path):
    import sqlite3
    db = str(tmp_path / "c.db"); c = sqlite3.connect(db)
    c.execute("CREATE TABLE scalp_signals (symbol TEXT, decision TEXT, created_ts TEXT, entry_ts TEXT, exit_ts TEXT, holding_sec REAL, r_multiple REAL, exit_reason TEXT)")
    for i, (dec, p0, p1) in enumerate([("BUY_CE", 100.0, 100.10), ("BUY_PE", 100.0, 99.90), ("BUY_CE", 100.0, 99.95)]):
        c.execute("INSERT INTO scalp_signals VALUES (?,?,?,?,?,?,?,?)", ("NIFTY", dec, f"2026-10-0{i + 1}T05:00:00Z", f"2026-10-0{i + 1}T05:00:00Z", f"2026-10-0{i + 1}T05:15:00Z", 900, 0.1, "TIME"))
    c.commit(); c.close(); prices = {("2026-10-01T05:00:00Z"): 100.0}
    def price_fn(sym, ts):
        d = str(ts)[:10]; end = str(ts).endswith("05:15:00Z"); return {"2026-10-01": (100.0, 100.10), "2026-10-02": (100.0, 99.90), "2026-10-03": (100.0, 99.95)}[d][1 if end else 0]
    out = NE.underlying_edge(db, price_fn)
    assert out["NIFTY"]["n"] == 3 and abs(out["NIFTY"]["gross_bp"] - ((10 + 10 - 5) / 3)) < 0.01 and abs(out["NIFTY"]["net_bp_after_futures_cost"] - (out["NIFTY"]["gross_bp"] - 1.0)) < 0.01
