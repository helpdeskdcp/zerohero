"""HTTP-level tests for /api/smart-chart (offline candles, real Node/PineTS runtime)."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.smart_chart import analysis, candles, routes, runner, store, templates
from tests import smart_chart_fakes as fk

pytestmark = pytest.mark.skipif(not runner.available()["ok"], reason="node/pinets not installed")
CSRF = {"X-SmartChart-CSRF": "1"}
EMA_X = next(t for t in templates.TEMPLATES if t["id"] == "ema-cross-strategy")["source"]
RIBBON = templates.TEMPLATES[0]["source"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMART_CHART_DB", str(tmp_path / "sc.db"))
    store.close()
    fk.install(monkeypatch)
    runner._rate.clear()
    routes._hits.clear()
    monkeypatch.setattr(routes, "owner_of", lambda req: req.headers.get("x-test-owner", "alice"))
    app = FastAPI()
    app.include_router(routes.router)
    yield TestClient(app)
    store.close()


def H(owner="alice", csrf=True):
    return {"X-Test-Owner": owner, **(CSRF if csrf else {})}


def mk(client, owner="alice", kind="strategy", source=EMA_X, name="s1"):
    r = client.post("/api/smart-chart/scripts", json={"name": name, "kind": kind, "source": source, "description": "d"}, headers=H(owner))
    assert r.status_code == 201, r.text
    return r.json()


def test_csrf_header_required_on_mutations_only(client):
    assert client.get("/api/smart-chart/scripts", headers=H(csrf=False)).status_code == 200
    r = client.post("/api/smart-chart/scripts", json={"name": "x", "kind": "indicator", "source": RIBBON}, headers=H(csrf=False))
    assert r.status_code == 403
    assert client.put("/api/smart-chart/scripts/abc", json={}, headers=H(csrf=False)).status_code == 403
    assert client.delete("/api/smart-chart/scripts/abc", headers=H(csrf=False)).status_code == 403
    assert client.post("/api/smart-chart/paper/x/start", headers=H(csrf=False)).status_code == 403


def test_script_lifecycle_versions_and_persistence(client):
    s = mk(client)
    assert s["version"] == 1 and s["status"] == "UNCOMPILED" and s["owner"] == "alice"
    sid = s["id"]
    c = client.post(f"/api/smart-chart/scripts/{sid}/compile", headers=H()).json()
    assert c["ok"] and c["kind"] == "strategy" and {i["key"] for i in c["inputs"]} == {"fastLen", "slowLen"}
    g = client.get(f"/api/smart-chart/scripts/{sid}", headers=H()).json()           # "after a page reload"
    assert g["status"] == "OK" and g["last_compile"]["ok"] is True and g["source"] == EMA_X
    u = client.put(f"/api/smart-chart/scripts/{sid}", json={"source": EMA_X + "\n// edit"}, headers=H()).json()
    assert u["version"] == 2 and u["status"] == "UNCOMPILED"
    v = client.get(f"/api/smart-chart/scripts/{sid}/versions", headers=H()).json()["versions"]
    assert [x["version"] for x in v] == [2, 1]
    assert client.get(f"/api/smart-chart/scripts/{sid}/versions/1", headers=H()).json()["source"] == EMA_X
    renamed = client.put(f"/api/smart-chart/scripts/{sid}", json={"name": "renamed", "favorite": True}, headers=H()).json()
    assert renamed["name"] == "renamed" and renamed["favorite"] is True and renamed["version"] == 2
    lst = client.get("/api/smart-chart/scripts?kind=strategy&q=renam", headers=H()).json()
    assert lst["total"] == 1 and "source" not in lst["items"][0]
    assert client.delete(f"/api/smart-chart/scripts/{sid}", headers=H()).status_code == 200
    assert client.get(f"/api/smart-chart/scripts/{sid}", headers=H()).status_code == 404


def test_owner_isolation_on_every_script_and_result_route(client):
    s = mk(client)
    sid = s["id"]
    bad = H("bob")
    assert client.get(f"/api/smart-chart/scripts/{sid}", headers=bad).status_code == 404
    assert client.put(f"/api/smart-chart/scripts/{sid}", json={"name": "pwn"}, headers=bad).status_code == 404
    assert client.delete(f"/api/smart-chart/scripts/{sid}", headers=bad).status_code == 404
    assert client.post(f"/api/smart-chart/scripts/{sid}/compile", headers=bad).status_code == 404
    assert client.get(f"/api/smart-chart/scripts/{sid}/versions", headers=bad).status_code == 404
    assert client.post("/api/smart-chart/run", json={"script_id": sid}, headers=bad).status_code == 404
    assert client.post("/api/smart-chart/backtests", json={"script_id": sid}, headers=bad).status_code == 404
    assert client.post("/api/smart-chart/paper", json={"script_id": sid}, headers=bad).status_code == 404
    assert client.post("/api/smart-chart/analysis", json={"script_id": sid}, headers=bad).status_code == 404
    assert client.get("/api/smart-chart/scripts", headers=bad).json()["total"] == 0
    assert client.get(f"/api/smart-chart/scripts/{sid}", headers=H()).json()["name"] == "s1"       # untouched
    bt = client.post("/api/smart-chart/backtests", json={"script_id": sid, "bars": 800, "causality_check": False}, headers=H()).json()
    assert client.get(f"/api/smart-chart/backtests/{bt['backtest_id']}", headers=bad).status_code == 404
    assert client.get("/api/smart-chart/backtests", headers=bad).json()["backtests"] == []
    pf = client.post("/api/smart-chart/paper", json={"script_id": sid}, headers=H()).json()
    for path in ("", "/trades"):
        assert client.get(f"/api/smart-chart/paper/{pf['id']}{path}", headers=bad).status_code == 404
    for act in ("start", "stop", "step"):
        assert client.post(f"/api/smart-chart/paper/{pf['id']}/{act}", headers=bad).status_code == 404
    assert client.delete(f"/api/smart-chart/paper/{pf['id']}", headers=bad).status_code == 404
    assert client.get("/api/smart-chart/audit", headers=bad).json()["events"] == []


@pytest.mark.parametrize("body,status,code", [
    ({"name": "x", "kind": "weird", "source": RIBBON}, 422, None),
    ({"name": "", "kind": "indicator", "source": RIBBON}, 422, None),
    ({"name": "x", "kind": "indicator", "source": "   "}, 422, "EMPTY_SCRIPT"),
    ({"name": "x", "kind": "indicator", "source": "//@version=5\n" + "a" * 70_000}, 413, "SCRIPT_TOO_LARGE"),
    ({"name": "x" * 500, "kind": "indicator", "source": RIBBON}, 422, None),
])
def test_script_validation(client, body, status, code):
    r = client.post("/api/smart-chart/scripts", json=body, headers=H())
    assert r.status_code == status
    if code:
        assert r.json()["error"]["code"] == code


def test_adhoc_compile_returns_diagnostics_with_positions(client):
    r = client.post("/api/smart-chart/compile", json={"source": '//@version=5\nindicator("x")\nplot(ta.sma(close,)'}, headers=H()).json()
    d = [x for x in r["diagnostics"] if x["severity"] == "error"][0]
    assert r["ok"] is False and d["code"] == "SYNTAX_ERROR" and d["line"] == 3
    r2 = client.post("/api/smart-chart/compile", json={"source": RIBBON}, headers=H()).json()
    assert r2["ok"] and r2["kind"] == "indicator" and r2["capabilities"]


def test_candles_endpoint_shapes_errors_and_limits(client):
    r = client.get("/api/smart-chart/candles?symbol=NIFTY&tf=15m&limit=120", headers=H())
    j = r.json()
    assert r.status_code == 200 and len(j["candles"]) == 120 and j["meta"]["source"] == "upstox" and j["meta"]["transport"] == "rest-poll"
    assert set(j["candles"][0]) == {"time", "open", "high", "low", "close", "volume"}
    e = client.get("/api/smart-chart/candles?symbol=MCX:CRUDE&tf=5m", headers=H())
    assert e.status_code == 422 and e.json()["error"]["code"] == "BAD_SYMBOL"
    assert client.get("/api/smart-chart/candles?symbol=NIFTY&tf=1s", headers=H()).json()["error"]["code"] == "BAD_TIMEFRAME"
    assert client.get("/api/smart-chart/candles?symbol=NIFTY&tf=5m&limit=999999", headers=H()).status_code == 422
    j5 = client.get("/api/smart-chart/candles?symbol=NIFTY&tf=5m&limit=50", headers=H()).json()
    live = client.get(f"/api/smart-chart/live?symbol=NIFTY&tf=5m&since={j5['candles'][-1]['time']}", headers=H()).json()
    assert [c["time"] for c in live["candles"]] == [j5["candles"][-1]["time"]]          # market closed: only the last bar, no phantom updates
    old = client.get(f"/api/smart-chart/live?symbol=NIFTY&tf=5m&since={j5['candles'][-3]['time']}", headers=H()).json()
    assert len(old["candles"]) == 3


def test_run_indicator_by_id_and_inline_with_input_override(client):
    s = mk(client, kind="indicator", source=RIBBON, name="ribbon")
    a = client.post("/api/smart-chart/run", json={"script_id": s["id"], "symbol": "NIFTY", "tf": "5m", "bars": 300}, headers=H()).json()
    assert a["kind"] == "indicator" and {p["title"] for p in a["plots"]} >= {"Fast", "Mid", "Slow"} and len(a["times"]) == 300
    assert a["data_meta"]["source"] == "upstox"
    b = client.post("/api/smart-chart/run", json={"source": RIBBON, "bars": 300, "inputs": {"fastLen": 3}}, headers=H()).json()
    fa = next(p for p in a["plots"] if p["title"] == "Fast")["values"]
    fb = next(p for p in b["plots"] if p["title"] == "Fast")["values"]
    assert fa != fb
    err = client.post("/api/smart-chart/run", json={"source": '//@version=5\nindicator("x")\nplot(zzz)', "bars": 100}, headers=H())
    assert err.status_code == 422 and err.json()["error"]["details"][0]["code"] == "UNDECLARED_IDENTIFIER"
    jobs = client.get("/api/smart-chart/jobs", headers=H()).json()["jobs"]
    assert [j["status"] for j in jobs][:3] == ["ERROR", "OK", "OK"]


def test_backtest_api_flow_and_indicator_rejected(client):
    s = mk(client)
    r = client.post("/api/smart-chart/backtests", json={"script_id": s["id"], "bars": 1200, "initial_capital": 50000, "commission_pct": 0.05, "slippage_ticks": 1}, headers=H())
    j = r.json()
    assert r.status_code == 200 and j["stats"]["reconciliation"]["ok"] and j["strategy_config"]["initial_capital"] == 50000 and j["strategy_config"]["commission_value"] == 0.05
    assert j["causality"]["ok"] is True and j["assumptions"]
    got = client.get(f"/api/smart-chart/backtests/{j['backtest_id']}", headers=H()).json()
    assert got["stats"] == j["stats"] and len(got["equity"]) == j["range"]["bars"]
    assert client.get(f"/api/smart-chart/backtests?script_id={s['id']}", headers=H()).json()["backtests"][0]["id"] == j["backtest_id"]
    ind = mk(client, kind="indicator", source=RIBBON, name="i")
    r2 = client.post("/api/smart-chart/backtests", json={"script_id": ind["id"]}, headers=H())
    assert r2.status_code == 422 and r2.json()["error"]["code"] == "NOT_A_STRATEGY"


def test_paper_api_flow_never_places_orders(client):
    s = mk(client)
    pf = client.post("/api/smart-chart/paper", json={"script_id": s["id"], "capital": 100000, "daily_loss_limit": 1e8}, headers=H()).json()
    assert pf["status"] == "STOPPED"
    assert client.post(f"/api/smart-chart/paper/{pf['id']}/step", headers=H()).json()["advanced"] is False       # stopped: nothing happens
    assert client.post(f"/api/smart-chart/paper/{pf['id']}/start", headers=H()).json()["status"] == "RUNNING"
    a = client.post(f"/api/smart-chart/paper/{pf['id']}/step", headers=H()).json()
    b = client.post(f"/api/smart-chart/paper/{pf['id']}/step", headers=H()).json()
    assert a["advanced"] and b["new_trades"] == 0                                                              # forward-only from creation time: history never traded
    assert client.get("/api/smart-chart/paper", headers=H()).json()["mode"] == "PAPER_ONLY"
    assert client.post(f"/api/smart-chart/paper/{pf['id']}/stop", headers=H()).json()["status"] == "STOPPED"
    assert client.delete(f"/api/smart-chart/paper/{pf['id']}", headers=H()).status_code == 200
    assert client.get(f"/api/smart-chart/paper/{pf['id']}", headers=H()).status_code == 404
    acts = [e["action"] for e in client.get("/api/smart-chart/audit", headers=H()).json()["events"]]
    assert {"paper.create", "paper.start", "paper.stop", "paper.delete"} <= set(acts)
    bad = client.post("/api/smart-chart/paper", json={"script_id": s["id"], "capital": 5}, headers=H())
    assert bad.status_code == 422


def test_analysis_deterministic_and_never_fabricates_unavailable_data(client):
    r = client.post("/api/smart-chart/analysis", json={"symbol": "NIFTY", "tf": "15m"}, headers=H()).json()
    assert r["trend"]["direction"] in ("UP", "DOWN", "SIDEWAYS") and r["structure"]["state"] and r["volatility"]["atr14"] > 0
    names = " ".join(u["item"] for u in r["unavailable"])
    for must in ("order book", "open interest", "implied volatility", "bid / ask"):
        assert must in names
    assert "oi" not in r and "iv" not in r and "bid" not in r and "order_book" not in r
    assert any("volume" in w for w in r["warnings"])                                  # index volume caveat shown
    assert r["ai"]["status"] == "NOT_REQUESTED"
    ref = r.get("reference")
    if ref:
        assert (ref["t1"] - ref["entry_ref"]) * (1 if ref["bias"] == "LONG" else -1) == pytest.approx(ref["risk_per_unit"], abs=0.02)
        assert ref["rr"] == {"t1": 1.0, "t2": 2.0, "t3": 3.0}


def test_analysis_ai_is_optional_and_labelled_as_estimate(client, monkeypatch):
    from app.ai import ai_client
    from app.ai.groq_client import AIResult
    monkeypatch.setattr(ai_client, "is_available", lambda: False)
    analysis._ai_cache.clear()
    off = client.post("/api/smart-chart/analysis", json={"symbol": "NIFTY", "tf": "15m", "ai": True}, headers=H())
    assert off.status_code == 200 and off.json()["ai"]["status"] == "UNAVAILABLE" and off.json()["trend"]               # chart analysis survives AI outage
    monkeypatch.setattr(ai_client, "is_available", lambda: True)
    calls = []

    def fake(**kw):
        calls.append(kw)
        return AIResult(status="OK", data={"summary": "s", "signal_explanation": "none", "risks": ["r1"], "model_confidence": 150}, model_used="m", latency_ms=1)
    monkeypatch.setattr(ai_client, "chat_completion_json", fake)
    on = client.post("/api/smart-chart/analysis", json={"symbol": "NIFTY", "tf": "15m", "ai": True}, headers=H()).json()["ai"]
    assert on["status"] == "OK" and on["model_confidence"] == 100 and "NOT a probability" in on["confidence_kind"]
    client.post("/api/smart-chart/analysis", json={"symbol": "NIFTY", "tf": "15m", "ai": True}, headers=H())
    assert len(calls) == 1                                                                                                    # cached: no repeated paid call
    payload = calls[0]["messages"][1]["content"]
    assert len(payload) < 6000 and '"candles"' not in payload                                                                 # compact summary, not raw history


def test_analysis_includes_script_signals_and_validation(client):
    s = mk(client, kind="indicator", source=templates.TEMPLATES[1]["source"], name="rsi")
    r = client.post("/api/smart-chart/analysis", json={"symbol": "NIFTY", "tf": "5m", "script_id": s["id"]}, headers=H()).json()
    assert isinstance(r["signals"], list) and r["validation"] is None


def test_health_meta_capabilities_license_and_no_secret_leak(client):
    h = client.get("/api/smart-chart/health", headers=H()).json()
    assert h["execution"] == {"mode": "PAPER_ONLY", "live_orders": False, "note": h["execution"]["note"]}
    assert h["runtime"]["ok"] and h["live_transport"] == "rest-poll" and h["db_schema_version"] == len(store.MIGRATIONS)
    blob = str(h).lower()
    assert "bearer" not in blob and "access_token" not in blob and "secret" not in blob.replace("credentials_configured", "")
    m = client.get("/api/smart-chart/meta", headers=H()).json()
    assert "1s" not in m["timeframes"] and m["timeframes"] == ["1m", "3m", "5m", "15m", "30m", "1h", "1d"]
    assert any("1-second" in x for x in m["not_supported"]) and any("Apache" in c["license"] for c in m["license"]["components"])
    reg = client.get("/api/smart-chart/capabilities", headers=H()).json()
    assert set(reg["tiers"]) <= {"supported", "partial", "unsupported", "not_tested"} and reg["tiers"]["supported"] >= 15
    assert "not TradingView" in reg["notice"] or "Not TradingView" in reg["notice"]
    lic = client.get("/api/smart-chart/license", headers=H()).json()
    assert "AGPL" in str(lic) and "TradingView" in lic["disclaimer"]


def test_templates_symbols_prefs(client, monkeypatch):
    assert len(client.get("/api/smart-chart/templates", headers=H()).json()["templates"]) == len(templates.TEMPLATES)
    s = client.get("/api/smart-chart/symbols?q=nifty", headers=H()).json()["symbols"]
    assert {"NIFTY", "BANKNIFTY"} <= {x["id"] for x in s}
    assert client.put("/api/smart-chart/prefs/theme", json={"value": {"tf": "5m"}}, headers=H()).status_code == 200
    assert client.get("/api/smart-chart/prefs", headers=H()).json() == {"theme": {"tf": "5m"}}
    assert client.get("/api/smart-chart/prefs", headers=H("bob")).json() == {}
    assert client.put("/api/smart-chart/prefs/bad key!", json={"value": 1}, headers=H()).status_code == 422


def test_rate_limit_on_script_execution(client, monkeypatch):
    monkeypatch.setattr(runner.config, "RUNS_PER_MINUTE", 3)
    codes = [client.post("/api/smart-chart/compile", json={"source": RIBBON}, headers=H()).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]
    assert client.post("/api/smart-chart/compile", json={"source": RIBBON}, headers=H("bob")).status_code == 200   # per-owner


def test_unexpected_errors_do_not_leak_internals(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internal path /root/zerohero/.env")
    monkeypatch.setattr(routes.candles, "get_candles", boom)
    r = client.get("/api/smart-chart/candles?symbol=NIFTY&tf=5m", headers=H())
    assert r.status_code == 500 and "/root" not in r.text and ".env" not in r.text and r.json()["error"]["code"] == "INTERNAL"


def test_real_app_auth_gate_protects_the_router():
    from app import main
    c = TestClient(main.app)
    assert c.get("/api/smart-chart/health").status_code == 401
    assert c.post("/api/smart-chart/scripts", json={"name": "x", "kind": "indicator", "source": RIBBON}, headers=CSRF).status_code == 401
    assert c.get("/api/smart-chart/candles?symbol=NIFTY").status_code == 401


def test_symbols_all_for_the_chart_picker(client, monkeypatch):
    from app.smart_chart import symbols as sy
    monkeypatch.setattr(sy, "_equities", lambda: {"RELIANCE": ("NSE_EQ|INE002A01018", "RELIANCE INDUSTRIES"), "TCS": ("NSE_EQ|INE467B01029", "TCS")})
    j = client.get("/api/smart-chart/symbols/all", headers=H()).json()["symbols"]
    assert j[0]["ticker"] == "NIFTY" and j[0]["type"] == "index" and {"RELIANCE", "TCS", "SENSEX"} <= {x["ticker"] for x in j}
    assert next(x for x in j if x["ticker"] == "SENSEX")["prefix"] == "BSE" and next(x for x in j if x["ticker"] == "TCS")["prefix"] == "NSE"
