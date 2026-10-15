"""While one feed's slow SYNC credential call (login HTTP) runs, another feed's WebSocket receive loop and the
event loop itself must keep processing. Regression for the 2026-10-13 loop stalls (feed gaps)."""
import asyncio
import struct
import time

import pytest

websockets = pytest.importorskip("websockets")
from app.connectors import angel_ws as AW  # noqa: E402

TOKEN = "99926000"


def frame(ts_ms, price):
    pkt = bytearray(AW._LTP_PACKET)
    pkt[0], pkt[1] = 1, 1
    pkt[2:2 + len(TOKEN)] = TOKEN.encode()
    struct.pack_into("<q", pkt, 35, ts_ms)
    struct.pack_into("<q", pkt, 43, int(price * 100))
    return bytes(pkt)


CREDS = ("OK", {"jwt": "j", "api_key": "k", "client_code": "c", "feed_token": "f"})


def test_receive_loop_and_event_loop_stay_responsive_during_slow_credential_call(monkeypatch):
    async def main():
        async def handler(ws):
            i = 0
            try:
                while True:
                    await ws.send(frame(int(time.time() * 1000), 24000 + i * 0.05))
                    i += 1
                    await asyncio.sleep(0.02)
            except Exception:
                return

        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(AW, "WS_URL", f"ws://127.0.0.1:{port}")
            slow_called = {"n": 0, "dur": 0.0}

            def slow_creds():                                   # sync, blocking, like angelone._login over a bad network
                slow_called["n"] += 1
                t = time.time()
                time.sleep(1.5)
                slow_called["dur"] = time.time() - t
                return CREDS

            a = AW.AngelMarketFeed(cred_provider=lambda: CREDS)
            b = AW.AngelMarketFeed(cred_provider=slow_creds)
            a.subscribe([{"token": TOKEN, "exchange_type": 1}], owner="t")
            b.subscribe([{"token": TOKEN, "exchange_type": 1}], owner="t")
            a.start()
            for _ in range(100):                                  # wait until A is receiving
                await asyncio.sleep(0.05)
                if TOKEN in a.ltp:
                    break
            assert TOKEN in a.ltp
            lag, gaps, last_recv, last_change = [], [], a.ltp[TOKEN]["recv"], time.time()
            b.start()                                             # B's _run now calls the slow credential provider
            t0 = time.time()
            prev = t0
            while time.time() - t0 < 2.2:
                await asyncio.sleep(0.05)
                now = time.time()
                lag.append(now - prev - 0.05); prev = now
                r = a.ltp[TOKEN]["recv"]
                if r != last_recv:
                    gaps.append(now - last_change); last_change = now; last_recv = r
            await a.stop(); await b.stop()
            return slow_called, max(lag), max(gaps) if gaps else 99, len(gaps)

    slow, max_lag, max_gap, n_updates = asyncio.run(main())
    assert slow["n"] >= 1 and slow["dur"] >= 1.4              # the slow call really ran during the window
    assert max_lag < 0.4, f"event loop blocked {max_lag:.2f}s"
    assert max_gap < 0.4, f"feed A stopped receiving for {max_gap:.2f}s"
    assert n_updates > 40                                      # ~50 Hz source: updates kept flowing


def test_autoscalp_daily_report_does_not_block_event_loop(monkeypatch):
    """session_report is a heavy full-table SQL (~10s on the live DB): it must run in a worker thread."""
    from app.autoscalp import report as report_mod
    from app.autoscalp import runner as ascr

    r = ascr.AutoScalpRunner(feed=None, chain_provider=lambda *a, **k: {}, telegram_fn=lambda *a: None,
                             now_fn=lambda: 1_800_000_000.0)
    monkeypatch.setattr(ascr, "_ist_hhmm", lambda: "23:59")
    monkeypatch.setattr(ascr.db, "get_setting", lambda k, *a, **kw: None)
    monkeypatch.setattr(ascr.db, "set_setting", lambda *a, **kw: None)
    called = {"n": 0}

    def slow_report(day):
        called["n"] += 1
        time.sleep(1.2)
        return {"day": day}

    monkeypatch.setattr(report_mod, "session_report", slow_report)
    monkeypatch.setattr(ascr.notify, "session_report_card", lambda rep, segment=None: "card")
    r._tg_send = lambda *a, **k: None

    async def main():
        lag, stop = [], asyncio.Event()

        async def ticker():
            prev = time.time()
            while not stop.is_set():
                await asyncio.sleep(0.05)
                now = time.time(); lag.append(now - prev - 0.05); prev = now

        t = asyncio.create_task(ticker())
        await r._maybe_daily_report()
        stop.set(); await t
        return max(lag)

    max_lag = asyncio.run(main())
    assert called["n"] == 2                      # NSE + MCX both due at 23:59
    assert max_lag < 0.4, f"event loop blocked {max_lag:.2f}s"


def test_autoscalp_underlying_ref_warmup_does_not_block_event_loop(monkeypatch):
    """_underlying_ref does broker REST on a cache miss; tick_once must warm it in a worker thread (stall seen 2026-10-14)."""
    from app.autoscalp import runner as ascr

    r = ascr.AutoScalpRunner(feed=object(), chain_provider=lambda *a, **k: {}, telegram_fn=lambda *a: None, now_fn=lambda: 1_800_000_000.0)
    calls = {"n": 0}

    def slow_ref(sym):
        calls["n"] += 1
        time.sleep(1.0)
        return {"token": "1", "exchange": "NSE"}

    monkeypatch.setattr(ascr, "_underlying_ref", slow_ref)

    async def main():
        lag, stop = [], asyncio.Event()

        async def ticker():
            prev = time.time()
            while not stop.is_set():
                await asyncio.sleep(0.05)
                now = time.time(); lag.append(now - prev - 0.05); prev = now

        t = asyncio.create_task(ticker())
        await r._warm_underlying_refs({"symbols": ["CRUDEOIL", "NATURALGAS"]})
        stop.set(); await t
        return max(lag)

    assert asyncio.run(main()) < 0.4 and calls["n"] == 2

    import ast
    import inspect
    src = inspect.getsource(ascr.AutoScalpRunner.tick_once)
    assert src.index("_warm_underlying_refs") < src.index("_refresh_subscription")   # warmed BEFORE the sync call


def test_reconnects_and_disconnects_are_counted_and_exposed_in_status(monkeypatch):
    """The Angel feed used to reconnect silently (no counter, no log). Now status shows connects/reconnects/disconnect history."""
    async def main():
        async def handler(ws):
            for i in range(3):
                await ws.send(frame(int(time.time() * 1000), 24000 + i))
                await asyncio.sleep(0.05)
            await ws.close()                                   # server drops the connection -> the feed must reconnect

        async with websockets.serve(handler, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(AW, "WS_URL", f"ws://127.0.0.1:{port}")
            f = AW.AngelMarketFeed(cred_provider=lambda: CREDS)
            f.subscribe([{"token": TOKEN, "exchange_type": 1}], owner="t")
            f.start()
            for _ in range(120):
                await asyncio.sleep(0.1)
                if f.connects >= 2:
                    break
            st = f.status()
            await f.stop()
            return st

    st = asyncio.run(main())
    assert st["connects"] >= 2 and st["reconnects"] >= 1 and st["disconnects_last_hour"] >= 1
    assert st["recent_disconnects"] and st["cred_failures"] == 0 and st["last_connect_age_sec"] is not None
