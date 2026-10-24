import json
import time

import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.chartink import api as CA, parse, store, tracker, validate


def test_parse_json_form_and_garbage():
    b = json.dumps({"stocks": "SBIN,tcs, SBIN", "trigger_prices": "812.5,3920.1,1", "scan_name": "Breakout 15", "triggered_at": "2:34 pm", "alert_name": "A", "scan_url": "breakout-15"}).encode()
    p = parse.parse_payload(b, "application/json"); assert p["scan_name"] == "Breakout 15" and p["symbols"] == [{"symbol": "SBIN", "trigger_price": 812.5}, {"symbol": "TCS", "trigger_price": 3920.1}]
    f = parse.parse_payload(b"stocks=INFY%2CHDFCBANK&trigger_prices=1500%2C1600&scan_name=X", "application/x-www-form-urlencoded"); assert [s["symbol"] for s in f["symbols"]] == ["INFY", "HDFCBANK"]
    assert "error" in parse.parse_payload(b"", "") and "error" in parse.parse_payload(b"[1,2]", "application/json")
    assert parse.parse_payload(json.dumps({"stocks": ["A", "B"], "scan_name": "L"}).encode())["symbols"][1] == {"symbol": "B", "trigger_price": None}


def _alert(path, now, scan="S1", syms=("SBIN", "TCS")):
    p = {"scan_name": scan, "scan_url": "", "alert_name": "", "triggered_at_text": "", "symbols": [{"symbol": s, "trigger_price": 100.0} for s in syms]}
    return store.add_alert(p, b"{}", now=now, path=path)


def test_tracker_fills_marks_late_and_missing_and_skips_after_close(tmp_path):
    db = str(tmp_path / "c.db"); t0 = time.mktime(time.strptime("2026-10-26 10:00:00", "%Y-%m-%d %H:%M:%S")); _alert(db, t0)
    tm = {"SBIN": "111", "TCS": "222"}; prices = {"111": 100.0, "222": 50.0, tracker.NIFTY[1]: 20000.0}
    out = tracker.tick(now=t0 + 5, quote_fn=lambda toks: prices, path=db, token_fn=lambda: tm); assert out["filled"] == 2          # reference marks
    out = tracker.tick(now=t0 + 5 * 60 + 400, quote_fn=lambda toks: {**prices, "111": 101.0}, path=db, token_fn=lambda: tm); assert out["late"] == 2  # 400 s after due: LATE, still stored
    with store.conn(db) as c:
        st = {r["horizon"]: r["status"] for r in c.execute("SELECT horizon, status FROM marks WHERE pair_id=1").fetchall()}
    assert st["0"] == "OK" and st["5"] == "LATE" and st["60"] == "PENDING"
    t1 = time.mktime(time.strptime("2026-10-26 15:00:00", "%Y-%m-%d %H:%M:%S")); _alert(db, t1, scan="S2", syms=("SBIN",))                  # 15:00 + 60 min is after 15:25
    with store.conn(db) as c: s2 = {r["horizon"]: r["status"] for r in c.execute("SELECT m.horizon, m.status FROM marks m JOIN pairs p ON p.id=m.pair_id JOIN alerts a ON a.id=p.alert_id WHERE a.scan_name='S2'").fetchall()}
    assert s2["30"] == "SKIPPED_CLOSE" and s2["60"] == "SKIPPED_CLOSE" and s2["EOD"] == "PENDING" and s2["15"] == "PENDING"
    out = tracker.tick(now=t0 + 5, quote_fn=lambda toks: {}, path=db, token_fn=lambda: {"SBIN": "111"}); assert out["no_token"] == 0      # TCS already tokened; empty quote -> nothing filled, no crash
    n = tracker.tick(now=t0 + 6, quote_fn=lambda toks: prices, path=db, token_fn=lambda: {}); assert n["filled"] == 0


def test_unknown_symbol_gets_no_token_status(tmp_path):
    db = str(tmp_path / "c.db"); t0 = 1_800_000_000.0; _alert(db, t0, syms=("ZZZZ",))
    assert tracker.tick(now=t0 + 1, quote_fn=lambda t: {}, path=db, token_fn=lambda: {})["no_token"] == 1


def _marks_frame(days, mean_e, n_per_day=20, sd=40.0, seed=1):
    rng = np.random.default_rng(seed); rows = []
    for d in range(days):
        date = f"2026-11-{d + 1:02d}" if d < 28 else f"2026-12-{d - 27:02d}"
        for k in range(n_per_day):
            e = rng.normal(mean_e, sd)
            for h in validate.HZ: rows.append({"alert_id": d, "scan_name": "S", "session_date": date, "pair_id": d * 1000 + k, "symbol": "X", "horizon": h, "p0": 100.0, "p1": 100.0 * (1 + e / 1e4), "n0": 20000.0, "n1": 20000.0})
    return pd.DataFrame(rows)


def test_validation_verdicts():
    few = validate.evaluate(_marks_frame(5, 50), {}, B=200); assert few["S"]["verdict"] == "INSUFFICIENT_SAMPLE"
    good = validate.evaluate(_marks_frame(40, 60, n_per_day=20), {}, B=300); assert good["S"]["verdict"] == "GO-SHADOW" and good["S"]["npass"] == 3
    zero = validate.evaluate(_marks_frame(40, 0, n_per_day=20), {}, B=300); assert zero["S"]["verdict"] in ("NO-GO", "single-horizon pass: not enough")
    short = validate.evaluate(_marks_frame(40, -60, n_per_day=20), {"S": {"side": "SHORT"}}, B=300); assert short["S"]["verdict"] == "GO-SHADOW"      # SHORT flips the sign


def test_webhook_auth_and_storage(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB", str(tmp_path / "w.db")); app = FastAPI(); app.include_router(CA.router); c = TestClient(app); body = json.dumps({"stocks": "SBIN", "trigger_prices": "800", "scan_name": "T"})
    monkeypatch.delenv("CHARTINK_WEBHOOK_TOKEN", raising=False); assert c.post("/api/chartink/webhook/x", content=body).status_code == 503              # unconfigured -> closed
    tok = "t" * 40; monkeypatch.setenv("CHARTINK_WEBHOOK_TOKEN", tok)
    assert c.post("/api/chartink/webhook/wrong", content=body).status_code == 404
    r = c.post(f"/api/chartink/webhook/{tok}", content=body, headers={"content-type": "application/json"}); assert r.status_code == 200 and r.json()["stored"] is True and r.json()["n_symbols"] == 1
    assert c.post(f"/api/chartink/webhook/{tok}", content=b"").status_code == 400
    assert c.post(f"/api/chartink/webhook/{tok}", content=json.dumps({"stocks": "", "scan_name": "T"})).json()["stored"] is False
    assert c.get("/api/chartink/alerts").json()["alerts"][0]["symbols"][0]["symbol"] == "SBIN" and CA.is_public_path(f"/api/chartink/webhook/{tok}") and not CA.is_public_path("/api/chartink/alerts")


def test_side_locks_after_results(tmp_path, monkeypatch):
    db = str(tmp_path / "s.db"); monkeypatch.setattr(store, "DB", db); monkeypatch.setattr(store, "SCANS_FILE", str(tmp_path / "scans.json"))
    t0 = 1_800_000_000.0; _alert(db, t0, scan="L"); assert store.set_scan_side("L", "short")["side"] == "SHORT"
    tracker.tick(now=t0 + 400, quote_fn=lambda t: {"1": 1.0}, path=db, token_fn=lambda: {"SBIN": "1", "TCS": "2"})
    with store.conn(db) as c: c.execute("UPDATE marks SET status='OK' WHERE horizon='5'")
    with pytest.raises(ValueError): store.set_scan_side("L", "LONG")
