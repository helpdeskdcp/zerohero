"""G7: indicator parity vs independent batch references, end-to-end tick->candle->writer vs brute force,
look-ahead (prefix) invariance, and no-live-order guards."""
import ast
import glob
import os
import random
import sqlite3

import pytest

from app.platform.indicators import ATR, EMA, RSI, SessionVWAP
from app.platform.marketdata.service import MarketDataService
from app.platform.marketdata.types import Tick
from app.platform.persistence.buffered_writer import BufferedCandleWriter

PKG = os.path.join(os.path.dirname(__file__), "..", "app", "platform")


# ---------------------------------------------------------------- indicators vs independent batch references
def ref_ema(xs, n):
    out, e = [], None
    for i, x in enumerate(xs):
        if i < n - 1: out.append(None); continue
        e = sum(xs[:n]) / n if i == n - 1 else e + 2 / (n + 1) * (x - e)
        out.append(e)
    return out


def ref_rsi(xs, n):
    out = [None] * len(xs)
    d = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
    if len(d) < n: return out
    ag = sum(max(x, 0) for x in d[:n]) / n; al = sum(max(-x, 0) for x in d[:n]) / n
    out[n] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + max(d[i], 0)) / n; al = (al * (n - 1) + max(-d[i], 0)) / n
        out[i + 1] = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def walk(n, seed=3):
    r, p, rows = random.Random(seed), 100.0, []
    for _ in range(n):
        o = p; p = max(1.0, p + r.gauss(0, 1)); h = max(o, p) + abs(r.gauss(0, .3)); l = min(o, p) - abs(r.gauss(0, .3))
        rows.append((o, h, l, p))
    return rows


def test_ema_rsi_atr_match_batch_reference():
    rows = walk(400); cl = [r[3] for r in rows]
    e, rs, a = EMA(21), RSI(14), ATR(14)
    got_e = [e.update(x) for x in cl]; got_r = [rs.update(x) for x in cl]
    for x, y in zip(got_e, ref_ema(cl, 21)): assert (x is None) == (y is None) and (x is None or x == pytest.approx(y))
    for x, y in zip(got_r, ref_rsi(cl, 14)): assert (x is None) == (y is None) and (x is None or x == pytest.approx(y))
    trs, pc = [], None
    for o, h, l, c in rows:
        trs.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc))); pc = c
    ref = sum(trs[:14]) / 14
    for t in trs[14:]: ref = (ref * 13 + t) / 14
    for o, h, l, c in rows: last = a.update(h, l, c)
    assert last == pytest.approx(ref)


def test_indicators_are_causal_prefix_invariant():
    cl = [r[3] for r in walk(200)]
    full, pre = RSI(14), RSI(14)
    fv = [full.update(x) for x in cl]
    pv = [pre.update(x) for x in cl[:120]]
    assert fv[:120] == pv                                   # appending future data never changes past values


def test_vwap_session_reset_and_missing_volume_never_guessed():
    v = SessionVWAP()
    v.update("d1", 100, 10); v.update("d1", 110, 30)
    assert v.value == pytest.approx((1000 + 3300) / 40)
    v.update("d1", 500, None); v.update("d1", 500, 0)
    assert v.skipped == 2 and v.value == pytest.approx(4300 / 40)
    v.update("d2", 50, 5)
    assert v.value == 50


# ---------------------------------------------------------------- end-to-end vs brute force
from datetime import datetime, timezone
BASE = int(datetime(2026, 10, 14, 4, 30, tzinfo=timezone.utc).timestamp())      # Wed 10:00 IST (inside NSE hours)


def mk_ticks(n=3000, seed=9):
    r, p, out, t = random.Random(seed), 24000.0, [], float(BASE)
    for _ in range(n):
        t += r.choice([0.1, 0.2, 0.4, 0.9])
        p = round(max(1.0, p + r.choice([-.5, -.25, .25, .5])), 2)
        out.append(Tick("ANGELONE", "NSE:99926000", p, t, t + 0.05))
    return out


def run_pipeline(ticks, path):
    w = BufferedCandleWriter(path, flush_interval=0.05)
    svc = MarketDataService(w)
    svc.arbiter.active = "ANGELONE"
    w.start()
    closed = []
    for tk in ticks:
        closed += svc.on_tick(tk)
    closed += svc.tick(ticks[-1].recv_ts + 10)
    w.stop(); w.flush_sync()
    return closed, w


def test_e2e_candles_match_brute_force_and_persist(tmp_path):
    ticks = mk_ticks()
    closed, w = run_pipeline(ticks, str(tmp_path / "t.db"))
    one = {c.start_ts: c for c in closed if c.tf_sec == 1}
    ref = {}
    for t in ticks:
        ref.setdefault(int(t.source_ts), []).append(t.price)
    assert one, "no 1s candles produced"
    for ts, c in one.items():
        ps = ref[ts]
        assert (c.o, c.h, c.l, c.c, c.tick_count) == (ps[0], max(ps), min(ps), ps[-1], len(ps))
        assert not c.synthetic
    assert set(one) <= set(ref)                      # nothing fabricated for seconds without ticks
    db = sqlite3.connect(str(tmp_path / "t.db"))
    tabs = [r[0] for r in db.execute("select name from sqlite_master where type='table'")]
    total = sum(db.execute(f"select count(*) from {t}").fetchone()[0] for t in tabs if "candle" in t)
    assert total >= len(one) and w.counters["write_errors"] == 0 and w.counters["lost_over_cap"] == 0


def test_prefix_invariance_closed_candles_never_change(tmp_path):
    ticks = mk_ticks(1500)
    full, _ = run_pipeline(ticks, str(tmp_path / "a.db"))
    cut = ticks[:900]
    part, _ = run_pipeline(cut, str(tmp_path / "b.db"))
    key = lambda c: (c.instrument, c.tf_sec, c.start_ts)
    fm = {key(c): c for c in full}
    horizon = int(cut[-1].source_ts) - 3                # seconds safely closed before the cut
    checked = 0
    for c in part:
        if c.tf_sec == 1 and c.start_ts < horizon:
            f = fm[key(c)]
            assert (f.o, f.h, f.l, f.c) == (c.o, c.h, c.l, c.c); checked += 1
    assert checked > 50


# ---------------------------------------------------------------- no accidental live-order path
FORBIDDEN_CALLS = {"place_order", "placeOrder", "modify_order", "cancel_order", "placeorder", "order_place", "submit_order"}


def test_platform_has_no_order_calls_or_broker_imports():
    bad = []
    for f in glob.glob(os.path.join(PKG, "**", "*.py"), recursive=True):
        tree = ast.parse(open(f).read())
        for n in ast.walk(tree):
            name = getattr(n.func, "attr", getattr(n.func, "id", None)) if isinstance(n, ast.Call) else None
            if name in FORBIDDEN_CALLS:
                bad.append((f, n.lineno, name))
            if isinstance(n, (ast.Import, ast.ImportFrom)):
                mod = getattr(n, "module", "") or " ".join(a.name for a in n.names)
                if any(x in mod for x in ("app.execution", "smartapi", "SmartApi", "dhanhq", "kiteconnect")):
                    bad.append((f, n.lineno, mod))
    assert not bad, bad


def test_live_mode_refused_for_tenants(tmp_path):
    from app.platform.tenancy import LiveModeNotEnabled, TenantStore
    st = TenantStore(str(tmp_path / "p.db")); st.create_tenant("ta", "A")
    with pytest.raises(LiveModeNotEnabled):
        st.set_execution_mode("ta", "LIVE_AUTO", actor="test")


# ---------------------------------------------------------------- stall watchdog (diagnoses event-loop blocking)
def test_stall_watchdog_captures_the_blocking_stack():
    import asyncio
    import threading
    import time as _t
    from app.platform.live import StallWatchdog

    def blocker_in_loop():
        _t.sleep(1.6)

    async def main():
        wd = StallWatchdog(threading.get_ident(), threshold_sec=0.6)
        wd.start(); wd.heartbeat()
        blocker_in_loop()                              # blocks the loop like a sync requests call would
        wd.heartbeat()
        await asyncio.sleep(0.05)
        wd.stop()
        return wd
    wd = asyncio.run(main())
    assert len(wd.events) == 1
    ev = wd.events[0]
    assert ev["duration_sec"] is not None and ev["duration_sec"] >= 1.4
    assert any("blocker_in_loop" in line for line in ev["stack"])


def test_platform_status_exposes_db_lock_angel_feed_and_ai_breaker_diagnostics():
    from app.platform.live import PlatformLive

    class F:
        def status(self):
            return {"socket_open": True, "data_flowing": True, "connects": 2, "reconnects": 1, "cred_failures": 0, "last_connect_age_sec": 5.0,
                    "disconnects_last_hour": 1, "recent_disconnects": [], "last_error": None}
    live = PlatformLive(lambda: F())
    st = live.status()
    assert {"acquires", "max_wait_sec", "max_hold_sec", "recent_slow"} <= set(st["db_lock"])
    assert st["angel_feed"]["reconnects"] == 1 and set(st["ai_breaker"]) == {"groq", "openai"}


def test_tick_listener_path_keeps_every_tick_in_order_where_sampling_would_lose_most():
    import time as _t
    from app.connectors.angel_ws import AngelMarketFeed
    from app.platform.live import PlatformLive

    feed = AngelMarketFeed()
    feed.subscribe([{"token": "99926000", "exchange_type": 1}], owner="t")
    live = PlatformLive(lambda: feed)
    live._attach()
    assert live.listener_attached
    live.svc.arbiter.active = "ANGELONE"
    live.svc.engine.in_session = lambda inst, ts: True
    base = _t.time() - 5
    for i in range(300):                                           # 300 ticks, 10 ms apart: a 0.25 s sampler would have seen about 12 of them
        ts_ms = int((base + i * 0.01) * 1000)
        feed._ingest({"token": "99926000", "exchange_type": 1, "ltp": 24000 + i * 0.05, "ts_ms": ts_ms})
    assert len(live._q) == 300
    live._poll_once(_t.time())
    assert live.ticks_in == 300 and live.svc.gate.counters["ACCEPTED"] == 300 and len(live._q) == 0
    st = live.status()
    assert st["tick_source"].startswith("listener") and st["queue_dropped"] == 0


def test_sampling_fallback_when_feed_has_no_listener_hook():
    from app.platform.live import PlatformLive

    class Old:
        _desired = {"99926000": 1}
        ltp = {}
        last_msg_ts = None
        def status(self):
            return {}
    live = PlatformLive(lambda: Old())
    live._attach()
    assert not live.listener_attached and live.status()["tick_source"].startswith("sampling")


def test_ticks_received_before_a_provider_is_active_are_held_and_flushed_in_order_not_discarded():
    import time as _t
    from app.connectors.angel_ws import AngelMarketFeed
    from app.platform.live import PlatformLive

    feed = AngelMarketFeed()
    feed.subscribe([{"token": "99926000", "exchange_type": 1}], owner="t")
    live = PlatformLive(lambda: feed)
    live._attach()
    live.svc.engine.in_session = lambda inst, ts: True
    base = _t.time() - 3
    for i in range(50):
        feed._ingest({"token": "99926000", "exchange_type": 1, "ltp": 24000 + i * 0.05, "ts_ms": int((base + i * 0.02) * 1000)})
    live._poll_once(_t.time())                                       # arbiter has no active provider yet -> hold, do not reject
    assert len(live._hold) == 50 and live.svc.gate.counters.get("NO_ACTIVE_PROVIDER", 0) == 0
    live.svc.arbiter.active = "ANGELONE"
    feed._ingest({"token": "99926000", "exchange_type": 1, "ltp": 24100.0, "ts_ms": int((base + 2) * 1000)})
    live._poll_once(_t.time())
    assert live.hold_flushed == 50 and len(live._hold) == 0 and live.svc.gate.counters["ACCEPTED"] == 51
