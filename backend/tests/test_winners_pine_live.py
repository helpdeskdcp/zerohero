"""Audit tests for the ZEROHERO_PINE_AI live NG path (2026-10-26). External services are MOCKED here (calendar, option capture, risk module, arena ledger); real-data checks are reported separately."""
import json, os, re, shutil, subprocess
import numpy as np, pandas as pd
import pytest
from app.winners_scalper import live, strategy as WS
from app.arena import pine_submitter as P

IST = live.IST
NOW = pd.Timestamp("2026-10-26 10:00:20", tz=IST).timestamp()
TODAY = "2026-10-26"


class Env:
    def __init__(self, mp, *, listed=("2026-11-20", "2026-11-23", TODAY), trading=True, day=TODAY, expiries=("2026-11-23",), quote=None, atm=250.0, problems=None, plan=None):
        self.submitted = []
        q = quote if quote is not None else dict(ltp=2.3, bid=2.3, ask=2.4, ts=pd.Timestamp(NOW - 10, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000000Z"))
        from app import market_calendar as mc, sl_detector
        from app.arena import contracts as CT, submit as SU
        mp.setattr(mc, "is_trading", lambda ex, *a, **k: trading)
        mp.setattr(P, "listed_option_expiries", lambda sym, path=None: set(listed) if listed is not None else None)
        mp.setattr(CT, "captured", lambda s, *a, **k: dict(day=day, expiries={e: dict(raw=e, strikes=[245.0, 250.0, 255.0]) for e in expiries}))
        mp.setattr(CT, "chain", lambda s, e, ot, n=7, path=None: dict(atm=atm, rows=[dict(strike=atm, quote=q)], problems=[], expiry=e, option_type=ot))
        mp.setattr(CT, "check", lambda *a, **k: dict(problems=problems or []))
        mp.setattr(sl_detector, "plan_for", lambda s, e, k, ot, entry, t, **kw: plan or dict(sl=round(entry * 0.55, 2), targets=[round(entry * 1.45, 2), round(entry * 1.9, 2), round(entry * 2.35, 2)]))
        mp.setattr(SU, "submit", lambda body, practice=False, **k: (self.submitted.append((body, practice)) or {"id": "H99999"}))


def _go(side="BUY", now=NOW):
    return P.submit_to_arena("NATURALGAS", side, True, pd.Timestamp("2026-10-26 09:45", tz=IST), 252.0, 249.0, now=now)


def test_buy_maps_to_call_and_sell_maps_to_put_long_only(monkeypatch):
    e = Env(monkeypatch)
    assert _go("BUY")["status"] == "SUBMITTED" and _go("SELL")["status"] == "SUBMITTED"
    (b1, _), (b2, _) = e.submitted
    assert (b1["option_type"], b2["option_type"]) == ("CE", "PE") and b1["strike"] == b2["strike"] == 250.0
    assert all(b["entry_mode"] == "NEAR" and b["lots"] == 1 and b["valid_sec"] == 900 and b["participant"] == "ZEROHERO_PINE_AI" for b, _ in e.submitted)   # a SELL signal buys a PUT, never shorts


def test_entry_sl_targets_are_consistent_option_premiums(monkeypatch):
    e = Env(monkeypatch, quote=dict(ltp=2.4, bid=2.4, ask=2.43, ts=pd.Timestamp(NOW - 5, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000000Z")))
    r = _go(); b = e.submitted[0][0]
    assert b["entry"] == 2.45 and 0 < b["sl"] < b["entry"] < min(b["targets"]) and r["risk_per_unit"] == round(b["entry"] - b["sl"], 2) and r["valid_until"] == NOW + 900


@pytest.mark.parametrize("kw,reason", [(dict(day="2026-10-23"), "STALE_CAPTURE_DAY"), (dict(expiries=("2026-10-23", "2026-10-22")), "EXPIRED_CONTRACT"), (dict(trading=False), "not trading"),
                                       (dict(problems=["no quotes are captured"]), "unscorable")])
def test_invalid_expiry_stale_capture_closed_market_or_contract_is_skipped(monkeypatch, kw, reason):
    e = Env(monkeypatch, **kw); r = _go()
    assert r["status"] == "SKIPPED" and reason in r["detail"] and e.submitted == []


def test_expiry_must_be_exchange_listed_and_verifiable(monkeypatch):
    e = Env(monkeypatch, expiries=("2026-12-01",)); r = _go(); assert r["status"] == "SKIPPED" and "EXPIRY_NOT_LISTED" in r["detail"] and e.submitted == []
    e = Env(monkeypatch, listed=None); r = _go(); assert r["status"] == "SKIPPED" and "EXPIRY_UNVERIFIED" in r["detail"] and e.submitted == []


def test_instrument_master_parser(tmp_path):
    p = tmp_path / "m.json"; p.write_text(json.dumps([dict(name="NATURALGAS", exch_seg="MCX", instrumenttype="OPTFUT", expiry="20NOV2026"), dict(name="NATURALGAS", exch_seg="MCX", instrumenttype="FUTCOM", expiry="27OCT2026"),
                                                       dict(name="CRUDEOIL", exch_seg="MCX", instrumenttype="OPTFUT", expiry="19NOV2026")]))
    assert P.listed_option_expiries("NATURALGAS", str(p)) == {"2026-11-20"} and P.listed_option_expiries("NATURALGAS", str(tmp_path / "missing.json")) is None


def test_expiry_today_is_allowed_but_next_expiry_is_preferred(monkeypatch):
    e = Env(monkeypatch, expiries=(TODAY, "2026-11-23")); assert _go()["status"] == "SUBMITTED" and e.submitted[0][0]["expiry"] == "2026-11-23"
    e2 = Env(monkeypatch, expiries=(TODAY,)); assert _go()["status"] == "SUBMITTED" and e2.submitted[0][0]["expiry"] == TODAY


def test_missing_stale_or_crossed_quotes_are_skipped_with_reason(monkeypatch):
    old = pd.Timestamp(NOW - 400, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000000Z"); fresh = pd.Timestamp(NOW - 2, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    for quote, word in ((dict(ltp=2.3, bid=2.3, ask=2.4, ts=old), "STALE_QUOTE"), (dict(ltp=2.3, bid=2.3, ask=None, ts=fresh), "no ATM quote"), (dict(ltp=2.3, bid=2.9, ask=2.4, ts=fresh), "INVALID_QUOTE"), ({}, "no ATM quote")):
        e = Env(monkeypatch, quote=quote); r = _go()
        assert r["status"] == "SKIPPED" and word in r["detail"] and e.submitted == []


def test_premium_too_low_or_undefined_stop_is_skipped(monkeypatch):
    low = dict(ltp=0.05, bid=0.0, ask=0.05, ts=pd.Timestamp(NOW - 5, unit="s", tz="UTC").strftime("%Y-%m-%dT%H:%M:%S.000000Z"))
    e = Env(monkeypatch, quote=low); r = _go("SELL"); assert r["status"] == "SKIPPED" and "degenerate" in r["detail"] and e.submitted == []
    e = Env(monkeypatch, plan=dict(sl=2.5, targets=[3.0, 3.5, 4.0])); r = _go(); assert r["status"] == "SKIPPED" and e.submitted == []            # stop above the entry


def _bars(n=1300, seed=3):
    rng = np.random.default_rng(seed); ts = []; day = pd.Timestamp("2024-01-01 09:00", tz=IST)
    while len(ts) < n:
        if day.weekday() < 5: ts += [day + pd.Timedelta(minutes=15 * k) for k in range(56)]
        day += pd.Timedelta(days=1)
    ts = ts[:n]; c = 250 + np.cumsum(rng.normal(0, 1.2, n) + 0.15 * np.sin(np.arange(n) / 30)); o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) + abs(rng.normal(0, .5, n)); l = np.minimum(o, c) - abs(rng.normal(0, .5, n))
    return pd.DataFrame(dict(timestamp=ts, open=o, high=h, low=l, close=c, volume=0.0))


def _first_signal_index(b, start=1000):
    s = WS.signals(b); idx = [i for i in range(start, len(b)) if s["buy"].iat[i] or s["sell"].iat[i]]; assert idx, "fixture must contain a signal after the warm-up"; return idx[0]


def _ready(tmp_path, name, src="TICK", age=5):
    """a db whose state sits one bar before the first fixture signal; returns (conn, bars, signal bar end epoch)"""
    b = _bars(); i = _first_signal_index(b); end = (b["timestamp"].iat[i] + pd.Timedelta(minutes=15)).timestamp(); c = live._conn(str(tmp_path / name)); bb = b.iloc[:i + 1].copy().reset_index(drop=True); bb["src"] = src
    c.execute("INSERT INTO state VALUES (?,?)", ("NATURALGAS", str(bb["timestamp"].iat[i - 1]))); c.commit(); return c, bb, end


def test_generator_records_a_fresh_tick_signal_as_new_and_nothing_else(tmp_path):
    c, bb, end = _ready(tmp_path, "a.db"); out = live.process("NATURALGAS", bb, c, now=end + 5)
    assert len(out) == 1 and out[0]["status"] == "NEW" and c.execute("SELECT status, bar_source FROM signals").fetchall() == [("NEW", "TICK")]
    assert live.process("NATURALGAS", bb, c, now=end + 6) == []                                                                  # same bars again: nothing new


def test_signal_expiry_and_delayed_capture_bars_are_recorded_as_skipped(tmp_path):
    c, bb, end = _ready(tmp_path, "b.db"); live.process("NATURALGAS", bb, c, now=end + 301)
    st = c.execute("SELECT status, detail FROM signals").fetchone(); assert st[0] == "SKIPPED" and "STALE_SIGNAL" in st[1]
    c, bb, end = _ready(tmp_path, "c.db", src="CAPTURE"); live.process("NATURALGAS", bb, c, now=end + 120)
    st = c.execute("SELECT status, detail FROM signals").fetchone(); assert st[0] == "SKIPPED" and "CAPTURE_BAR" in st[1]


def test_consumer_submits_once_even_if_it_crashes_after_claiming(tmp_path):
    c, bb, end = _ready(tmp_path, "d.db"); live.process("NATURALGAS", bb, c, now=end + 5); n = []; db = str(tmp_path / "d.db")
    def boom(*a, **k): n.append(1); raise RuntimeError("process died after the ledger write")
    with pytest.raises(RuntimeError): P.drain(db, submit=boom, send=lambda t: None, now=end + 6)
    assert c.execute("SELECT status FROM signals").fetchall() == [("PENDING",)]                                                       # claimed before the submit
    P.drain(db, submit=lambda *a, **k: (n.append(1) or dict(status="SUBMITTED", detail="x")), send=lambda t: None, now=end + 7)
    assert len(n) == 1                                                                                                                # the restart did NOT submit a second time


def test_consumer_ledger_row_is_complete_and_consistent(tmp_path):
    c, bb, end = _ready(tmp_path, "e.db"); live.process("NATURALGAS", bb, c, now=end + 5); sent = []
    res = dict(status="SUBMITTED", detail="d", arena_id="H7", opt_expiry="2026-11-23", strike=250.0, opt_type="CE", entry=2.4, opt_sl=1.3, opt_targets="[3.5, 4.6, 5.7]", quote_ts="2026-10-26T04:30:00Z", quote_age_s=7.0, risk_per_unit=1.1, valid_until=end + 900)
    out = P.drain(str(tmp_path / "e.db"), submit=lambda *a, **k: res, send=sent.append, now=end + 6)
    r = c.execute("SELECT status, ledger_id, opt_expiry, strike, opt_type, entry, opt_sl, opt_targets, quote_ts, quote_age_s, risk_per_unit, valid_until, stop, close, atr FROM signals").fetchone()
    assert r[:12] == ("SUBMITTED", "H7", "2026-11-23", 250.0, "CE", 2.4, 1.3, "[3.5, 4.6, 5.7]", "2026-10-26T04:30:00Z", 7.0, 1.1, end + 900) and r[12] > 0 and r[13] > 0 and r[14] > 0
    assert c.execute("SELECT COUNT(*) FROM signals WHERE status IN ('NEW','PENDING')").fetchone()[0] == 0 and len(sent) == 1 and "PAPER" in sent[0] and out[0]["status"] == "SUBMITTED"


def test_consumer_refuses_a_signal_it_picks_up_too_late(tmp_path):
    c, bb, end = _ready(tmp_path, "f.db"); live.process("NATURALGAS", bb, c, now=end + 5); calls = []
    P.drain(str(tmp_path / "f.db"), submit=lambda *a, **k: calls.append(1), send=lambda t: None, now=end + 400)
    assert calls == [] and c.execute("SELECT status, detail FROM signals").fetchone()[0] == "SKIPPED"


def test_tick_builder_partial_bars_never_feed_signals_and_roll_is_spliced(tmp_path):
    c = live._conn(str(tmp_path / "t.db")); tb = live.TickBuilder(c); t0 = int(pd.Timestamp("2026-10-26 09:00", tz=IST).timestamp())
    for k in range(0, 900, 2): tb.add("NG", t0 + k, 250.0 + (k % 7) * 0.1, "tok", "2026-10-27")                                       # a full bucket
    for k in range(400, 900, 2): tb.add("NG", t0 + 900 + k, 251.0, "tok", "2026-10-27")                                               # restart: the next bucket starts 400 s late
    for k in range(0, 900, 2): tb.add("NG", t0 + 1800 + k, 262.0, "tok2", "2026-11-25")                                               # new contract, +11 points
    tb.due(t0 + 2800); rows = c.execute("SELECT bucket, partial, expiry FROM tick_bars ORDER BY bucket").fetchall()
    assert [r[1] for r in rows] == [0, 1, 0]
    base = _bars(300); base["timestamp"] = [pd.Timestamp(t0, unit="s", tz="UTC").tz_convert(IST) - pd.Timedelta(minutes=15 * (300 - k)) for k in range(300)]; base["src"] = "CAPTURE"
    for k in ("open", "high", "low", "close"): base[k] = base[k] + (250.0 - base["close"].iat[-1])                        # same contract level as the tick bars
    orig = live.load_bars; live.load_bars = lambda *a, **k: (lambda d: (d.attrs.update(last_expiry="2026-10-27") or d))(base.copy())
    try: m = live.merged_bars("NG", t0 + 2800, c)
    finally: live.load_bars = orig
    assert set(m["src"]) == {"CAPTURE", "TICK"} and not any(pd.Timestamp(t0 + 900, unit="s", tz="UTC").tz_convert(IST) == t for t in m[m.src == "TICK"]["timestamp"])           # partial bucket excluded
    assert m["close"].diff().abs().iloc[-3:].max() < 3                                                                                  # the 11-point contract roll did not reach the series


def test_no_live_order_pathway_and_information_barrier():
    for mod in (live, P):
        src = open(mod.__file__, encoding="utf-8").read()
        assert not re.search(r"import[^\n]*(execution|angelone_orders|order_manager|place_order)|place_order|\.execution\.", src)             # paper only: no broker / order-manager import
    src = open(live.__file__, encoding="utf-8").read().lower()
    assert "arena" not in src and "sl_detector" not in src                                                                           # the generator has no contest / estimator reference (barrier tests)
    assert "PAPER" in open(P.__file__, encoding="utf-8").read()


def test_quote_timestamp_parsing():
    assert abs(P._epoch("2026-10-23T18:04:59.939004Z") - pd.Timestamp("2026-10-23T18:04:59.939004Z").timestamp()) < 1e-3 and P._epoch(5) == 5.0


@pytest.mark.skipif(shutil.which("node") is None or not os.path.isdir(os.path.join(os.path.dirname(live.__file__), "..", "smart_chart", "node_modules", "pinets")), reason="Pine interpreter (node + pinets) not installed")
def test_pine_script_vs_python_port_regression():
    here = os.path.dirname(live.__file__); root = os.path.abspath(os.path.join(here, "..", ".."))
    pine = os.path.join(root, "data", "research", "winners_scalper", "pine", "WSP_v45_IN_reviewed.pine")
    if not os.path.exists(pine): pytest.skip("reviewed pine file missing")
    src = open(pine, encoding="utf-8").read() + "\nplot(initialBuy or lateStrongBuy ? 1 : 0, 'DBG_BUY')\nplot(initialSell or lateStrongSell ? 1 : 0, 'DBG_SELL')\n"
    b = _bars(1500); candles = [dict(time=int(t.timestamp() * 1000), open=float(o), high=float(h), low=float(l), close=float(c), volume=0.0) for t, o, h, l, c in zip(b.timestamp, b.open, b.high, b.low, b.close)]
    pkg = os.path.abspath(os.path.join(here, "..", "smart_chart")); worker = os.path.join(pkg, "pine_runner", "worker.mjs")
    p = subprocess.run(["node", "--permission", f"--allow-fs-read={pkg}", worker], input=json.dumps(dict(op="run", source=src, candles=candles, tf="15", symbol="NG", inputs={}, props={})).encode(), capture_output=True, cwd=pkg, env={}, timeout=300)
    out = json.loads(p.stdout.decode().strip().splitlines()[-1]); assert out["ok"], out.get("diagnostics")
    pl = {x["title"]: np.array([np.nan if v is None else v for v in x["values"]], float) for x in out["plots"]}; py = WS.signals(b)
    assert ((pl["DBG_BUY"] > 0) == py["buy"].values).all() and ((pl["DBG_SELL"] > 0) == py["sell"].values).all() and py["buy"].sum() + py["sell"].sum() > 10
