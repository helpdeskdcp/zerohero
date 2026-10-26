import os, sqlite3, time
import pytest
from app.nifty_direction import adapters as A, indicators as I, strategy as ST, derivatives as D, scoring as S, store, paper as P, constituents as C, service, validation as V
from app.nifty_direction.historical import HistoricalAdapter

T0 = 1_700_000_000 - (1_700_000_000 % 900)


def bars(closes, step=300, t0=T0, vol=100, spread=0.2):
    out = []
    for i, c in enumerate(closes):
        o = c
        out.append({"t": t0 + i * step, "o": o, "h": max(o, c) + spread, "l": min(o, c) - spread, "c": c, "v": vol})
    return out


# ---- indicators ----
def test_ema_sma_atr_known_values():
    assert I.sma([1, 2, 3, 4], 2) == 3.5 and I.sma([1], 2) is None
    assert I.ema([2] * 10, 5) == pytest.approx(2.0)
    c = bars([10, 11, 12, 11, 12, 13] * 5); assert I.atr(c, 14) > 0 and I.atr(c[:5], 14) is None


def test_closed_only_drops_forming_bar():
    c = bars([1, 2, 3]); assert len(I.closed_only(c, c[-1]["t"] + 299, 300)) == 2 and len(I.closed_only(c, c[-1]["t"] + 300, 300)) == 3


def test_indicators_have_no_lookahead():
    c = bars([100 + (i % 7) + i * 0.1 for i in range(80)])
    for k in (40, 55, 70):                                  # value on the first k bars must not change when later bars are appended
        a = (I.ema([x["c"] for x in c[:k]], 9), I.atr(c[:k], 14), I.structure(c[:k]), I.support_resistance(c[:k]))
        b = (I.ema([x["c"] for x in c[:k]], 9), I.atr(c[:k], 14), I.structure(c[:k]), I.support_resistance(c[:k]))
        assert a == b
    s_short = I.swings(c[:50])["highs"]; s_long = I.swings(c)["highs"]
    assert all(x in s_long for x in s_short if x[0] <= c[47]["t"])           # swings confirmed on the short series stay on the longer one (no repaint)


def test_swing_confirmed_only_after_k_bars():
    c = bars([1, 2, 3, 9, 3, 2, 1, 2, 3]); assert I.swings(c[:5], 2)["highs"] == []           # peak at idx3 needs 2 bars after it
    assert I.swings(c[:6], 2)["highs"]                                                          # confirmed once 2 lower bars closed


def test_vwap_none_without_volume_and_value_with():
    assert I.vwap(bars([10, 11], vol=0)) is None
    c = bars([10, 12], vol=5); assert I.vwap(c) == pytest.approx(sum((x["h"] + x["l"] + x["c"]) / 3 for x in c) / 2)


def test_rel_volume():
    c = bars([1] * 25); c[-1]["v"] = 300; assert I.rel_volume(c, 20) == pytest.approx(3.0)
    assert I.rel_volume(bars([1] * 25, vol=0), 20) is None


def test_break_events():
    c = bars([9, 9, 9, 11]); assert I.break_events(c, 10, "UP")["event"] == "BREAKOUT"
    c = bars([9, 9, 9, 9]); c[-1]["h"] = 10.5; assert I.break_events(c, 10, "UP")["event"] == "REJECTION"
    c = bars([11, 11, 11, 9]); assert I.break_events(c, 10, "DOWN")["event"] == "BREAKDOWN"
    c = bars([9, 9, 11, 12, 10.5]); c[-1]["l"] = 10.0; assert I.break_events(c, 10, "UP", retest_tol=0.002)["event"] == "RETEST"


def test_validate_candles():
    assert I.validate_candles([], 300) == "EMPTY"
    b = bars([1, 2]); b[1]["t"] = b[0]["t"]; assert I.validate_candles(b, 300) == "DUPLICATE"
    b = bars([1, 2]); b[1]["h"] = 0.5; assert I.validate_candles(b, 300) == "BAD_OHLC"


# ---- strategy ----
def up15(n=60): return bars([100 + i * 0.5 for i in range(n)], 900, T0 - 900 * n)
def dn15(n=60): return bars([200 - i * 0.5 for i in range(n)], 900, T0 - 900 * n)


def test_trend_states():
    now = T0 + 5
    assert ST.trend15(up15(), now)["state"] == "BULLISH" and ST.trend15(dn15(), now)["state"] == "BEARISH"
    assert ST.trend15(bars([100] * 60, 900, T0 - 900 * 60), now)["state"] == "NEUTRAL"
    assert ST.trend15(up15(10), now)["reason"] == "INSUFFICIENT_BARS"
    assert ST.trend15(up15(), now + 6 * 3600)["state"] == "STALE"
    b = up15(); b[5]["c"] = -1; assert ST.trend15(b, now)["state"] == "INVALID"
    assert ST.trend15([], now)["state"] == "INVALID"


def test_entry_requires_trend_and_data():
    now = T0
    assert ST.entry5(bars([1] * 40), {"state": "NEUTRAL"}, now)["reason"] == "TREND_NEUTRAL"
    assert ST.entry5(bars([1] * 40), {"state": "STALE"}, now)["signal"] is None
    assert ST.entry5(bars([1] * 5), {"state": "BULLISH"}, now)["reason"] == "INSUFFICIENT_5M_BARS"
    assert ST.entry5(bars([1] * 40, t0=T0 - 300 * 40), {"state": "BULLISH"}, now + 99999)["reason"] == "STALE_5M"


def test_entry_long_on_swing_breakout():
    pat = [100, 101, 102, 101, 100, 101, 102, 103, 102, 101, 102, 103, 102, 101, 100, 101, 102, 103, 102, 101] * 2
    c = bars(pat + [104.5], 300, T0 - 300 * 41)
    r = ST.entry5(c, {"state": "BULLISH"}, T0 + 1, ST.StrategyConfig(rel_vol_min=0))
    assert r["signal"] == "LONG" and r["invalidation"] < r["price"] and "BREAKOUT" in r["reason"]


def test_dedup_and_invalidation():
    d = ST.Deduper(1800); assert d.allow("LONG", 1, 100) and not d.allow("LONG", 1, 101) and not d.allow("LONG", 2, 500)
    assert d.allow("SHORT", 3, 600) and not d.allow("SHORT", 4, 700) and d.allow("SHORT", 5, 600 + 1801)
    assert ST.invalidated("LONG", 99, 98.9, "BULLISH") == "STOP_LEVEL" and ST.invalidated("SHORT", 101, 99, "BULLISH") == "TREND_FLIPPED" and ST.invalidated("LONG", 99, 100, "BULLISH") is None


# ---- derivatives ----
NOW = 1_800_000_000.0
FUT = {"symbol": "NIFTY26NOVFUT", "expiry": "2026-11-24", "ltp": 101, "prev_close": 100, "volume": 10, "oi": 120, "prev_oi": 100, "ts": NOW - 1}


def test_futures_view_rules():
    v = D.futures_view(FUT, NOW, spot=100.5); assert v["available"] and v["activity"] == "LONG_BUILDUP" and v["bias"] == 1.0 and v["basis"] == pytest.approx(0.5)
    assert D.futures_view({**FUT, "oi": 80}, NOW)["activity"] == "SHORT_COVERING" and D.futures_view({**FUT, "oi": 80}, NOW)["bias"] == 0.5
    assert D.futures_view(None, NOW)["reason"] == "NO_FUTURES" and D.futures_view({**FUT, "oi": None}, NOW)["reason"] == "INCOMPLETE"
    assert D.futures_view({**FUT, "symbol": "BANKNIFTY26NOVFUT"}, NOW)["reason"] == "WRONG_INSTRUMENT" and D.futures_view({**FUT, "ts": NOW - 999}, NOW)["reason"] == "STALE"


def _chain(n=8, **kw):
    rows = [{"strike": 22000 + 50 * i, "ce_oi": 100 + i, "pe_oi": 150, "ce_iv": 12, "pe_iv": 13, "ce_delta": .5} for i in range(n)]
    return {"underlying": "NIFTY", "expiry": "2026-11-03", "ts": NOW - 1, "rows": rows, **kw}


def test_chain_view_rules():
    v = D.chain_view(_chain(), NOW, spot=22100); assert v["available"] and v["pcr"] > 1 and v["greeks"] and v["strikes"] == 8
    assert D.chain_view(_chain(4), NOW)["reason"] == "TOO_FEW_STRIKES" and D.chain_view({**_chain(), "underlying": "BANKNIFTY"}, NOW)["reason"] == "WRONG_UNDERLYING"
    assert D.chain_view({**_chain(), "expiry": None}, NOW)["reason"] == "INCOMPLETE" and D.chain_view({**_chain(), "ts": NOW - 500}, NOW)["reason"] == "STALE"
    assert D.confirm(None, None, NOW)["available"] is False and D.confirm(None, None, NOW)["bias"] is None


# ---- scoring ----
SNAP_UP = {"status": "OK", "contribution": {"net": 0.5}, "breadth": {"weighted_adv": 1.0}, "sectors": [{"weight_pct": 50, "contrib": 1}]}


def test_scoring_blocked_without_weights_never_actionable():
    sc = S.score(S.ScoreConfig(), snapshot={"status": "NO_SIGNAL", "reason": "UNIVERSE_NOT_TRADABLE"}, trend={"state": "BULLISH"}, entry={"signal": "LONG"}, deriv={"available": True, "bias": 1})
    assert sc["actionable"] is False and sc["reason"] == "BLOCKED_BY_VERIFIED_OFFICIAL_WEIGHTS" and sc["total"] == 35 and {"contribution", "breadth", "sector"} <= {m["component"] for m in sc["missing"]}


def test_scoring_full_and_thresholds():
    sc = S.score(S.ScoreConfig(), snapshot=SNAP_UP, trend={"state": "BULLISH"}, entry={"signal": "LONG"}, deriv={"available": True, "bias": 1})
    assert sc["total"] == 100 and sc["actionable"] and sc["grade"] == "STRONG"
    sc = S.score(S.ScoreConfig(), snapshot=SNAP_UP, trend={"state": "BEARISH"}, entry={"signal": "LONG"}, deriv=None)
    assert not sc["actionable"] and sc["reason"] == "NOT_ALIGNED_OR_BELOW_THRESHOLD"
    assert sum(getattr(S.ScoreConfig(), k) for k in ("w_contribution", "w_breadth", "w_sector", "w_trend15", "w_entry5", "w_derivatives")) == 100


# ---- store / migrations ----
def test_migrations_idempotent_and_history(tmp_path):
    db = str(tmp_path / "n.db")
    with store.conn(db) as c: assert c.execute("PRAGMA user_version").fetchone()[0] == len(store.MIGRATIONS)
    with store.conn(db) as c: assert c.execute("PRAGMA user_version").fetchone()[0] == len(store.MIGRATIONS)
    sid = store.add_signal({"side": "LONG", "grade": "WEAK", "total": 50, "actionable": False, "reason": "x"}, {"bar_t": 1}, {"a": 1}, "MOCK", path=db)
    store.health("DATA_HEALTH", {"x": 1}, path=db); store.save_config("strategy", {"a": 1}, path=db)
    assert store.rows("signals", path=db)[0]["id"] == sid and store.latest_config("strategy", path=db) == {"a": 1} and store.rows("health_events", path=db)
    with pytest.raises(AssertionError): store.rows("sqlite_master", path=db)


def test_migration_from_v1_upgrades(tmp_path):
    db = str(tmp_path / "old.db"); c = sqlite3.connect(db); c.executescript("BEGIN;" + store.MIGRATIONS[0] + "PRAGMA user_version=1;COMMIT;"); c.close()
    with store.conn(db) as c: assert c.execute("PRAGMA user_version").fetchone()[0] == 2 and c.execute("SELECT count(*) FROM paper_trades").fetchone()[0] == 0


# ---- paper ----
def test_paper_refuses_when_live_orders_flag_set(monkeypatch):
    monkeypatch.setenv("ENABLE_LIVE_ORDERS", "true")
    with pytest.raises(P.LiveOrdersForbidden): P.PaperBook(persist=False)
    with pytest.raises(P.LiveOrdersForbidden): service.evaluate(A.MockAdapter(), {"members": []}, NOW)


def test_paper_stop_before_target_costs_and_risk(monkeypatch):
    monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False)
    b = P.PaperBook(P.PaperConfig(slippage_bps=0, cost_bps_round_trip=0, max_trades_per_day=1), "MOCK", persist=False)
    assert b.open("LONG", 100, 99, 1, "d") and b.open("LONG", 100, 99, 2, "d") is None
    t = b.on_bar({"h": 110, "l": 98, "c": 100}, 2); assert t["exit_reason"] == "STOP" and t["net"] == pytest.approx(-1)         # both touched -> stop wins
    assert b.open("SHORT", 100, 101, 3, "d") is None                                                                           # daily trade cap
    b2 = P.PaperBook(P.PaperConfig(slippage_bps=10, cost_bps_round_trip=3), "MOCK", persist=False); b2.open("LONG", 100, 99, 1, "d"); t = b2.on_bar({"h": 105, "l": 100, "c": 104}, 2)
    assert t["exit_reason"] == "TARGET" and t["cost"] > 0 and t["net"] < t["gross"]
    assert P.PaperBook(persist=False).open("LONG", 100, 101, 1, "d") is None                                                   # stop on the wrong side rejected


def test_paper_persists(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False); db = str(tmp_path / "p.db")
    b = P.PaperBook(P.PaperConfig(), "HISTORICAL", db_path=db); b.open("LONG", 100, 99, 1, "d"); b.on_bar({"h": 100, "l": 90, "c": 95}, 2)
    assert store.rows("paper_trades", path=db)[0]["data_kind"] == "HISTORICAL"


def test_no_order_api_imported_in_package():
    import pathlib, re
    src = "".join(p.read_text() for p in pathlib.Path(__file__).resolve().parents[1].joinpath("app/nifty_direction").glob("*.py"))
    assert not re.search(r"angelone_orders|place_order|placeOrder|\.execution\b|OrderManager|dhan_client", src)


# ---- adapter contract (every registered adapter) ----
@pytest.mark.parametrize("name", A.names())
def test_adapter_contract(name):
    a = A.get(name); assert isinstance(a.name, str) and a.health()["ok"] is True and isinstance(A.capabilities(a), set)
    assert a.quotes([]) == {} and a.candles("NIFTY", "5m", 5) == []


def test_historical_adapter_causal_and_closed_only(tmp_path):
    db = str(tmp_path / "h.db"); c = sqlite3.connect(db); c.execute("CREATE TABLE bars (sym TEXT, ts INTEGER, o REAL, h REAL, l REAL, c REAL, v REAL, PRIMARY KEY (sym, ts)) WITHOUT ROWID")
    base = 1_790_000_000 // 300 * 300
    for i in range(120): c.execute("INSERT INTO bars VALUES ('NIFTY',?,?,?,?,?,0)", (base + i * 60, 100 + i, 101 + i, 99 + i, 100.5 + i))
    c.commit(); c.close()
    a1 = HistoricalAdapter(base + 60 * 60, db); x = a1.candles("NIFTY", "5m", 50)
    a2 = HistoricalAdapter(base + 60 * 60 + 1, db); y = a2.candles("NIFTY", "5m", 50)
    assert x == y and x[-1]["t"] + 300 <= base + 3600                              # same visible past; no bar that is still forming
    a3 = HistoricalAdapter(base + 3600 - 30, db); assert a3.candles("NIFTY", "5m", 50)[-1]["t"] + 300 <= base + 3600 - 30
    assert HistoricalAdapter(base + 3600, db).quotes(["NIFTY"])["NIFTY"].ltp == pytest.approx(100.5 + 59)


# ---- service end-to-end on the mock ----
CSV = ("Company Name,Industry,Symbol,Series,ISIN Code\n" + "".join(f"Co{i},{'Fin' if i%2 else 'IT'},S{i},EQ,INE{i:03d}\n" for i in range(50))).encode()


def test_service_label_and_blocked_when_no_weights(tmp_path, monkeypatch):
    monkeypatch.delenv("ENABLE_LIVE_ORDERS", raising=False)
    m = C.build_master(CSV, None); now = time.time(); a = A.MockAdapter()
    for i in range(50): a.set_quote(f"S{i}", 101, 100, ts=now - 1)
    a.set_quote("NIFTY", 22000, 21900, ts=now - 1); a.set_candles("NIFTY", "15m", bars([22000] * 5, 900, now - 5000)); a.set_candles("NIFTY", "5m", bars([22000] * 5, 300, now - 2000))
    out = service.evaluate(a, m, now, persist=True, db_path=str(tmp_path / "s.db"))
    assert out["data_kind"] == "MOCK" and out["score"]["actionable"] is False and out["health"]["weights_status"] == "MISSING" and out["snapshot"]["status"] == "NO_SIGNAL"
    assert len(out["heatmap"]) == 50 and out["signal_id"] and store.rows("health_events", path=str(tmp_path / "s.db"))


# ---- unweighted breadth / sector (weights not required) + weights provenance ----
from app.nifty_direction import engine as EN
import datetime as _dtm


def _uw_setup(n_valid=48, chg=None):
    m = C.build_master(CSV, None); now = time.time(); a = A.MockAdapter(); chg = chg or {}
    for i in range(n_valid): a.set_quote(f"S{i}", 100 + chg.get(i, 1), 100, ts=now - 1)
    return m, V.validate_universe(m, a.quotes([f"S{i}" for i in range(50)]), now)


def test_unweighted_breadth_and_sectors_without_weights():
    chg = {0: 0, 1: 0, 2: -1, 3: -1}                     # 2 unchanged, 2 decliners, rest advancers
    m, v = _uw_setup(48, chg); s = EN.snapshot(m, v); u = s["unweighted"]
    assert s["status"] == "NO_SIGNAL" and s["reason"] == "UNIVERSE_NOT_TRADABLE"          # weighted block stays closed
    assert u["status"] == "OK" and u["basis"] == "UNWEIGHTED" and u["advancers"] == 44 and u["decliners"] == 2 and u["unchanged"] == 2 and u["valid"] == 48
    assert sum(x["n"] for x in u["sectors"]) == 48 and all("weight_pct" not in x for x in u["sectors"]) and {x["sector"] for x in u["sectors"]} == {"Fin", "IT"}


def test_unweighted_unavailable_when_quotes_insufficient():
    m, v = _uw_setup(40); u = EN.snapshot(m, v)["unweighted"]
    assert u["status"] == "UNAVAILABLE" and u["reason"] == "INSUFFICIENT_QUOTES" and u["count_coverage"] == 0.8


def test_scoring_uses_unweighted_for_breadth_and_sector_but_stays_non_actionable():
    m, v = _uw_setup(48); snap = EN.snapshot(m, v)
    sc = S.score(S.ScoreConfig(), snapshot=snap, trend={"state": "BULLISH"}, entry={"signal": "LONG"}, deriv={"available": True, "bias": 1})
    assert sc["basis"] == {"breadth": "UNWEIGHTED", "sector": "UNWEIGHTED"} and sc["components"]["breadth"] == 20 and sc["components"]["sector"] == 15 and "contribution" not in sc["components"]
    assert [x["component"] for x in sc["missing"]] == ["contribution"] and sc["actionable"] is False and sc["reason"] == "BLOCKED_BY_VERIFIED_OFFICIAL_WEIGHTS" and sc["total"] == 70


def test_scoring_breadth_missing_names_the_real_reason_when_quotes_insufficient():
    m, v = _uw_setup(40); sc = S.score(S.ScoreConfig(), snapshot=EN.snapshot(m, v), trend={"state": "BULLISH"}, entry={"signal": "LONG"}, deriv=None)
    deps = {x["component"]: x["dependency"] for x in sc["missing"]}
    assert deps["breadth"] == "INSUFFICIENT_QUOTES" and deps["sector"] == "INSUFFICIENT_QUOTES" and deps["contribution"] == "VERIFIED_OFFICIAL_WEIGHTS"


def _wcsv(d, src="NSE Indices factsheet", asof=True, total=2.0):
    return ("symbol,weight_pct" + (",as_of,source" if asof else "") + "\n" + "".join(f"S{i},{total}" + (f",{d},{src}" if asof else "") + "\n" for i in range(50))).encode()


def test_weights_provenance_required_and_staleness():
    today = _dtm.date(2026, 10, 26); ok = C.build_master(CSV, _wcsv("2026-10-20"), today=today)
    assert ok["weights_status"] == "COMPLETE" and ok["weights_as_of"] == "2026-10-20" and ok["weights_source"] == "NSE Indices factsheet" and C.validate(ok) == []
    assert C.build_master(CSV, _wcsv("", asof=False), today=today)["weights_status"] == "INCOMPLETE"                     # no as_of / source columns
    assert C.build_master(CSV, _wcsv("2026-10-20", src=""), today=today)["weights_status"] == "INCOMPLETE"               # source blank
    st = C.build_master(CSV, _wcsv("2026-08-01"), today=today); assert st["weights_status"] == "STALE" and "days old" in st["weights_notes"][0] and C.validate(st)
    assert C.build_master(CSV, _wcsv("2026-12-01"), today=today)["weights_status"] == "INCOMPLETE"                       # future date
    assert C.build_master(CSV, _wcsv("20-10-2026"), today=today)["weights_status"] == "INCOMPLETE"                       # bad format
    assert all(x["weight_pct"] is None for x in st["members"])                                                          # a stale file never feeds the engine


def test_trend_reports_neutral_band():
    t = ST.trend15(bars([100] * 60, 900, T0 - 900 * 60), T0 + 5); assert t["state"] == "NEUTRAL" and t["neutral_band"] == pytest.approx(0.15 * t["atr"])
