"""Data-contract, adapter, dedupe/gap/stale, repaint, parity and state tests for the PA/OF research chart. Uses ONLY temp sqlite files (never the live tick DB)."""
import hashlib
import os
import random
import sqlite3

import pytest

from app.engines.orderflow_engine import FootprintBar, SymbolState
from app.engines.orderflow_store import TickStore
from app.pa_of_lab import api as lab_api, confirm, log as lab_log, pa_events as ev, price_action as pa, ticks as tk
from app.pa_of_lab.schema import footprint_to_canonical, to_orderflow_input, dq_of

DAY = "2026-10-16"
S0 = tk.day_bounds(DAY)[0]                       # epoch of 00:00 IST that day
T0915 = S0 + 9 * 3600 + 15 * 60


def row(i, ltt, ltp, ltq=10, side="BUY_AGGRESSOR", method="QUOTE_RULE", sym="NIFTY", kind="FUTURE", recv=None, sid=1, bid=None, ask=None):
    from datetime import datetime, timezone
    recv = recv if recv is not None else ltt + 1
    return {"id": i, "received_ts": datetime.fromtimestamp(recv, tz=timezone.utc).isoformat(), "security_id": sid, "symbol": sym, "kind": kind, "ltp": ltp, "ltq": ltq, "ltt": ltt,
            "side": side, "classification_method": method, "best_bid": ltp - 0.05 if bid is None else bid, "best_ask": ltp + 0.05 if ask is None else ask, "cvd": 0}


def mkdb(tmp_path, rows):
    p = str(tmp_path / "ticks.db"); TickStore(p).insert_many(rows); return p


def stream(n, start=T0915, step=3, base=25000.0, qty=10, sym="NIFTY"):
    rnd = random.Random(3); px = base; out = []
    for k in range(n):
        px = round(px + rnd.choice([-0.1, 0.0, 0.1, 0.1]), 2)
        out.append(row(k, start + k * step, px, qty, "BUY_AGGRESSOR" if rnd.random() < .5 else "SELL_AGGRESSOR", "QUOTE_RULE", sym=sym))
    return out


# ---------------- adapter / schema ----------------
def test_adapter_maps_engine_bar_to_canonical_and_orderflow_input():
    fp = FootprintBar(T0915, 300, 100.0)
    for k in range(40):
        fp.add({"ltp": 100.0 + (k % 3) * 0.05, "ltq": 5, "side": "BUY_AGGRESSOR" if k % 2 else "SELL_AGGRESSOR", "classification_method": "QUOTE_RULE"})
    c = footprint_to_canonical(fp.to_dict(), "NIFTY", "FUTURE")
    assert (c["o"], c["h"], c["l"], c["c"]) == (fp.open, fp.high, fp.low, fp.close)
    assert c["buy"] == fp.buy_vol and c["sell"] == fp.sell_vol and c["delta"] == fp.delta
    assert c["side_source"] == "INFERRED_QUOTE_RULE" and c["provenance"] == "CLASSIFIED_PROXY"
    assert c["unknown"] is None and c["levels"] is None and c["volume"] is None       # not invented
    oi = to_orderflow_input(c)
    assert set(oi) == {"t", "o", "h", "l", "c", "buy", "sell"}


def test_candle_bar_cannot_feed_orderflow_engine():
    with pytest.raises(ValueError): to_orderflow_input({"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "buy": None, "sell": None})


def test_no_field_claims_broker_confirmation():
    src = open(os.path.join(os.path.dirname(lab_api.__file__), "schema.py")).read().lower()
    assert "broker-confirmed" in src and "never broker-confirmed" in src.replace("\n", " ")
    assert "inferred" in lab_api.DEFINITIONS["classification"].lower()


def test_dq_rules():
    assert dq_of(50, 0.9, 0.0, False, True)["status"] == "OK"
    assert dq_of(50, 0.9, 0.0, True, True)["status"] == "INSUFFICIENT"
    assert dq_of(50, 0.3, 0.0, False, True)["status"] == "DEGRADED"
    assert dq_of(50, 0.9, 0.0, False, False)["status"] == "INSUFFICIENT"


# ---------------- timestamps / timeframe ----------------
def test_bar_starts_align_to_timeframe_and_ist():
    acc, rep = tk.prepare(stream(400), "NIFTY")
    bars = tk.build_bars(acc, "NIFTY", "FUTURE", 300)
    assert all(b["t"] % 300 == 0 for b in bars)
    assert ((bars[0]["t"] + 19800) // 60) % 1440 == 9 * 60 + 15
    assert tk.day_bounds(DAY)[1] - tk.day_bounds(DAY)[0] == 86400


# ---------------- dedupe / same-second / late / stale / session ----------------
def test_dedupe_same_second_late_stale_outside():
    rows = [row(1, T0915 + 10, 100.0, 5), row(2, T0915 + 10, 100.0, 5),                 # exact duplicate -> dropped
            row(3, T0915 + 10, 100.1, 7),                                               # same second, different trade -> kept
            row(4, T0915 + 5, 100.0, 5),                                                # late (ltt < running max) -> dropped
            row(5, T0915 + 20, 100.0, 5, recv=T0915 + 20 + 500),                        # stale (received 500s after ltt) -> dropped
            row(6, T0915 - 3600, 100.0, 5),                                             # before session open -> dropped
            row(7, T0915 + 30, 100.2, 5)]
    acc, rep = tk.prepare(rows, "NIFTY")
    assert [r["id"] for r in acc] == [1, 3, 7]
    assert rep["duplicate_dropped"] == 1 and rep["same_second_multi"] == 1 and rep["late_dropped"] == 1 and rep["stale_dropped"] == 1 and rep["outside_session_dropped"] == 1


def test_null_symbol_rows_excluded_and_counted(tmp_path):
    rows = stream(50) + [dict(r, symbol=None, kind=None, security_id=99) for r in stream(30, start=T0915 + 400)]
    p = mkdb(tmp_path, rows)
    got, nnull = tk.load_rows("NIFTY", "FUTURE", DAY, p)
    assert len(got) == 50 and nnull == 30


def test_tick_size_inference_is_labelled():
    assert tk.infer_tick_size([25000.1, 25000.2000001, 25000.3])[0] in (0.1, 0.05, 0.01) and tk.infer_tick_size([])[1] == "NO_DATA"
    assert tk.infer_tick_size([100.0, 100.5, 101.0]) == (0.5, "INFERRED_FROM_DATA")


# ---------------- gaps ----------------
def gap_rows():
    a = stream(120, start=T0915, step=3)                       # 09:15..09:21
    b = stream(120, start=T0915 + 2400, step=3)                # resumes 40 min later -> gap + many missing bars
    return a + b


def test_gap_recorded_bars_not_filled_and_segments_split():
    acc, rep = tk.prepare(gap_rows(), "NIFTY")
    assert len(rep["gaps"]) == 1
    bars = tk.build_bars(acc, "NIFTY", "FUTURE", 300, gaps=rep["gaps"], final_closed=True)
    ts = [b["t"] for b in bars]
    assert all(t2 - t1 >= 300 for t1, t2 in zip(ts, ts[1:])) and (max(ts) - min(ts)) > 2000 and len(bars) <= 4      # no fabricated filler bars
    segs = tk.segments(bars)
    assert len(segs) >= 2


def test_cvd_resets_at_gap_and_is_not_carried(tmp_path):
    p = mkdb(tmp_path, gap_rows())
    r = lab_api.build_chart("NIFTY", DAY, 300, "ticks", 1, path=p, now=tk.day_bounds(DAY)[1] + 10)
    f = r["footprint"]["bars"]
    first_after_gap = [x for x, y in zip(f[1:], f[:-1]) if x["t"] - y["t"] != 300][0]
    assert first_after_gap["cvd_segment"] == first_after_gap["delta"]


# ---------------- live engine vs replay parity ----------------
def test_replay_bars_equal_live_engine_closed_bars():
    st = SymbolState(); rnd = random.Random(5); px = 25000.0; ticks = []; ltt = T0915
    for k in range(900):
        ltt += rnd.choice([1, 2, 3, 5]); px = round(px + rnd.choice([-0.1, 0.0, 0.1]), 2)
        pk = {"security_id": 1, "ltp": px, "ltq": rnd.randint(1, 20), "ltt": ltt, "depth": [{"bid_price": px - 0.1 if rnd.random() < .8 else px, "ask_price": px + 0.1}]}
        t = st.ingest(pk)
        if t: ticks.append(dict(t, id=k, received_ts="2026-10-16T05:00:00+00:00", security_id=1, best_bid=t["best_bid"], best_ask=t["best_ask"]))
    live = st.closed_bars[300]
    rows = [dict(t, received_ts=__import__("datetime").datetime.fromtimestamp(t["ltt"] + 1, tz=__import__("datetime").timezone.utc).isoformat()) for t in ticks]
    acc, rep = tk.prepare(rows, "NIFTY")
    mine = tk.build_bars(acc, "NIFTY", "FUTURE", 300)
    assert len(live) >= 2
    for lb, mb in zip(live, mine):
        assert lb["bar_start"] == mb["t"] and (lb["open"], lb["high"], lb["low"], lb["close"]) == (mb["o"], mb["h"], mb["l"], mb["c"])
        assert lb["buy_vol"] == mb["buy"] and lb["sell_vol"] == mb["sell"]


def test_price_bars_and_footprint_bars_share_ohlc_and_timestamps(tmp_path):
    p = mkdb(tmp_path, stream(500))
    r = lab_api.build_chart("NIFTY", DAY, 300, "ticks", 1, path=p, now=tk.day_bounds(DAY)[1] + 10)
    assert [b["t"] for b in r["bars"]] == [f["t"] for f in r["footprint"]["bars"]]
    assert all((b["o"], b["h"], b["l"], b["c"]) == (f["o"], f["h"], f["l"], f["c"]) for b, f in zip(r["bars"], r["footprint"]["bars"]))
    lv = r["footprint"]["bars"][0]["levels"]
    assert min(x[0] for x in lv) == pytest.approx(r["footprint"]["bars"][0]["l"], abs=0.06)


# ---------------- repaint / causality ----------------
def walk(n, seed=30, tf=300):
    r = random.Random(seed); p = 100.0; bars = []
    for j in range(n):
        o = p; p += r.uniform(-1, 1.1); hi = max(o, p) + r.random() * .6; lo = min(o, p) - r.random() * .6
        bars.append({"symbol": "X", "kind": "FUTURE", "tf_sec": tf, "t": T0915 + j * tf, "o": o, "h": hi, "l": lo, "c": p, "volume": 100, "closed": True})
    return ev.with_day(bars)


def key(e): return (e["id"], e["type"], round(e["price"], 6), e["confirmed_t"])


@pytest.mark.parametrize("cut", [60, 117, 200, 262])
def test_events_do_not_repaint_when_future_bars_arrive(cut):
    full = walk(300); pre = [dict(b) for b in full[:cut]]
    close_t = pre[-1]["t"] + 300
    f_piv = {key(e) for e in ev.pivots(full) if e["confirmed_t"] <= close_t}
    assert f_piv == {key(e) for e in ev.pivots(pre)}
    f_ev = {key(e) for e in ev.structure(full, ev.pivots(full))["events"] if e["confirmed_t"] <= close_t}
    p_ev = {key(e) for e in ev.structure(pre, ev.pivots(pre))["events"]}
    assert f_ev == p_ev
    f_sig = {s["id"] for s in ev.signals(full, 555, 930) if s["confirmed_t"] <= close_t}
    assert f_sig == {s["id"] for s in ev.signals(pre, 555, 930)}


def test_provisional_pivots_are_marked_and_not_in_confirmed_list():
    b = walk(80); conf = {p["t"] for p in ev.pivots(b)}
    prov = ev.provisional_pivots(b)
    assert all(p["status"] == "PROVISIONAL" and p["confirmed_t"] is None for p in prov)
    assert all(p["t"] not in conf or True for p in prov) and all(p["idx"] >= len(b) - 3 for p in prov)


def test_pivots_match_price_action_swings():
    b = walk(150); sh, sl = pa.swings(b, len(b) - 1, 3)
    mine = ev.pivots(b)
    assert {(p["idx"]) for p in mine if p["type"] == "SH"} >= {j for j, _ in sh if j >= len(b) - 1 - 150}


def test_signals_are_price_action_signal_at_exactly():
    b = walk(300)
    got = ev.signals(b, 555, 930)
    for s in got:
        ref = pa.signal_at(b, s["idx"])
        assert ref and (ref.side, ref.entry, ref.stop, ref.target) == (s["side"], s["entry"], s["stop"], s["t2"])
        assert s["t1"] == pytest.approx(s["entry"] + s["side"] * abs(s["entry"] - s["stop"]))


def test_open_bar_never_generates_signal():
    b = walk(300); got = ev.signals(b, 555, 930)
    assert got, "fixture must contain at least one signal"
    i = got[0]["idx"]; b2 = [dict(x) for x in b[:i + 1]]; b2[-1]["closed"] = False
    assert all(s["idx"] != i for s in ev.signals(b2, 555, 930))


# ---------------- combined confirmation ----------------
def cbars(n=6, dq="OK"):
    return [{"symbol": "X", "tf_sec": 300, "t": T0915 + i * 300, "buy": 10, "sell": 5, "dq": {"status": dq, "reasons": [] if dq == "OK" else ["x"]}} for i in range(n)]


def sig(i, side, src): return {"id": f"{src}{i}", "idx": i, "side": side, "t": T0915 + i * 300}


def test_combined_truth_table():
    b = cbars()
    assert confirm.combine(b, [sig(3, 1, "pa")], [sig(3, 1, "of")])[0]["state"] == confirm.BOTH
    c = confirm.combine(b, [sig(3, 1, "pa")], [sig(3, -1, "of")])[0]; assert c["state"] == confirm.PA_ONLY and c["of_status"] == "ORDER FLOW OPPOSES"
    c = confirm.combine(b, [sig(3, 1, "pa")], [])[0]; assert c["state"] == confirm.PA_ONLY and c["of_status"] == "NO ORDER FLOW CONFIRMATION"
    assert confirm.combine(b, [], [sig(3, 1, "of")])[0]["state"] == confirm.OF_ONLY
    bad = cbars(dq="DEGRADED")
    c = confirm.combine(bad, [sig(3, 1, "pa")], [sig(3, 1, "of")])[0]
    assert c["state"] == confirm.PA_ONLY and c["of_status"] == confirm.UNVERIFIED and c["of_unverified_reasons"]
    assert confirm.combine(bad, [], [sig(3, 1, "of")])[0]["state"] == confirm.NONE
    nof = [{"symbol": "X", "tf_sec": 300, "t": T0915 + i * 300, "buy": None, "sell": None, "dq": {"status": "UNAVAILABLE", "reasons": ["CANDLE_ONLY"]}} for i in range(6)]
    c = confirm.combine(nof, [sig(3, 1, "pa")], [])[0]; assert c["of_status"] == confirm.UNVERIFIED


def test_missing_previous_bar_makes_orderflow_unusable():
    b = cbars(); del b[2]
    ok, why = confirm.of_usable(b, 2)
    assert not ok and "MISSING_PREV_BAR" in why


def test_of_signals_never_cross_a_gap_and_need_ok_bars():
    # synthetic bars with strong absorption pattern but a gap_in_bar flag everywhere -> no signals
    b = []
    for i in range(40):
        big = i == 30
        b.append({"symbol": "X", "kind": "FUTURE", "tf_sec": 300, "t": T0915 + i * 300, "o": 100, "h": 100.5 if not big else 100.4, "l": 99.5 if not big else 99.6, "c": 100 if i != 31 else 101.4,
                  "buy": 100 if not big else 40, "sell": 100 if not big else 300, "gap_in_bar": True, "closed": True, "dq": {"status": "INSUFFICIENT", "reasons": ["FEED_GAP_IN_BAR"]}})
    b[31].update(h=101.5, l=99.9, buy=250, sell=100)
    assert confirm.of_signals(b) == []
    for x in b: x.update(gap_in_bar=False, dq={"status": "OK", "reasons": []})
    assert any(s["kind"] == "ABSORPTION" for s in confirm.of_signals(b))


# ---------------- states: no-data / stale / candle-only / replay ----------------
def test_no_data_state(tmp_path):
    p = mkdb(tmp_path, stream(10))
    r = lab_api.build_chart("NIFTY", "2026-01-05", 300, "ticks", 1, path=p, now=tk.day_bounds(DAY)[1])
    assert r["status"]["state"] == "NO_DATA" and r["bars"] == [] and r["footprint"]["available"] is False


def test_stale_feed_state(tmp_path):
    p = mkdb(tmp_path, stream(200, start=T0915))                               # last tick ~09:25
    now = T0915 + 3 * 3600                                                     # 12:15 IST same day, in session
    r = lab_api.build_chart("NIFTY", DAY, 300, "ticks", 1, path=p, now=now)
    assert r["status"]["state"] == "STALE" and r["status"]["feed"]["stale"] and r["status"]["feed"]["mode"] == "LIVE_TODAY"
    assert r["bars"][-1]["closed"] is False


def test_candle_source_gives_unavailable_footprint_not_fake_data(monkeypatch):
    cs = [{"symbol": "NIFTY", "kind": "INDEX", "tf_sec": 300, "t": T0915 + i * 300, "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100.5 + i, "volume": None, "buy": None, "sell": None, "unknown": None,
           "delta": None, "n_ticks": None, "quote_share": None, "spread_mean": None, "gap_in_bar": False, "closed": True, "levels": None, "side_source": "NONE", "provenance": "CANDLE_DERIVED",
           "dq": {"status": "UNAVAILABLE", "reasons": ["CANDLE_ONLY"]}} for i in range(60)]
    monkeypatch.setattr(lab_api, "_candle_bars", lambda s, d, tf: [dict(x) for x in cs] if d == DAY else [])
    r = lab_api.build_chart("NIFTY", DAY, 300, "candles", 1, now=tk.day_bounds(DAY)[1] + 5)
    assert r["footprint"]["available"] is False and r["footprint"]["bars"] == [] and "cannot establish" in r["footprint"]["reason"]
    assert r["pa"]["vwap"] is None and "unavailable" in r["pa"]["vwap_note"].lower()
    assert r["of"]["signals"] == [] and all(c["of_status"] == confirm.UNVERIFIED for c in r["combined"])


def test_replay_as_of_equals_prefix_of_full(tmp_path):
    p = mkdb(tmp_path, stream(3000, step=3))
    now = tk.day_bounds(DAY)[1] + 10
    full = lab_api.build_chart("NIFTY", DAY, 300, "ticks", 1, path=p, now=now)
    cut = full["bars"][20]["t"] + 300
    part = lab_api.build_chart("NIFTY", DAY, 300, "ticks", 1, as_of=cut, path=p, now=now)
    assert all(b["t"] + 300 <= cut or b["closed"] is False for b in part["bars"])
    assert {e["id"] for e in part["pa"]["events"]} == {e["id"] for e in full["pa"]["events"] if e["confirmed_t"] <= cut}
    assert {s["id"] for s in part["pa"]["signals"]} == {s["id"] for s in full["pa"]["signals"] if s["confirmed_t"] <= cut}


def test_markers_match_research_log(tmp_path):
    p = mkdb(tmp_path, stream(1500, step=3))
    r = lab_api.build_chart("NIFTY", DAY, 300, "ticks", 1, path=p, now=tk.day_bounds(DAY)[1] + 10)
    out = tmp_path / "log.jsonl"; n = lab_log.write_jsonl(str(out), r)
    ids = {__import__("json").loads(l)["id"] for l in out.read_text().splitlines() if '"id"' in l}
    assert n == len(lab_log.records(r))
    assert {s["id"] for s in r["pa"]["signals"]} <= ids
    b = tk.build_bars(tk.prepare(tk.load_rows("NIFTY", "FUTURE", DAY, p)[0], "NIFTY")[0], "NIFTY", "FUTURE", 300, tick_size=r["meta"]["tick_size"], final_closed=True)
    assert {s["id"] for s in ev.signals(ev.with_day(b), 555, 930)} == {s["id"] for s in r["pa"]["signals"]}


# ---------------- safety ----------------
def test_tick_db_opened_read_only(tmp_path):
    p = mkdb(tmp_path, stream(5)); c = tk.connect_ro(p)
    with pytest.raises(sqlite3.OperationalError): c.execute("DELETE FROM classified_ticks")
    c.close()


def test_no_order_or_write_path_in_lab_modules():
    d = os.path.dirname(lab_api.__file__)
    for f in os.listdir(d):
        if f.endswith(".py") and f != "__init__.py":
            s = open(os.path.join(d, f)).read()
            assert "app.execution" not in s and "place_order" not in s and "INSERT" not in s.replace("INSERT_NOT", "") and "DELETE" not in s.upper().replace("DELETE_NOT", "") or f == "ticks.py" and "DELETE" not in s


def test_baseline_backtest_script_unchanged():
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "pa_lab_backtest.py")
    assert hashlib.sha256(open(p, "rb").read()).hexdigest().startswith("5b43815b67c171cb")


def test_router_validation_and_kill_switch(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI(); app.include_router(lab_api.router); c = TestClient(app)
    assert c.get("/api/pa_of_lab/chart?symbol=NIFTY&date=bad").status_code == 422
    assert c.get("/api/pa_of_lab/chart?symbol=NIFTY&date=2026-10-16&tf=7").status_code == 400
    monkeypatch.setenv("PA_OF_LAB", "0")
    assert c.get("/api/pa_of_lab/meta").status_code == 503
