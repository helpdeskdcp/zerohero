"""Live glue: samples the shared AngelOne WS feed (read-only) -> TickGate -> 1s candles -> buffered writer + delta hub.

Observation/charting only: no order path, no tenant trading. Kill switch PLATFORM_STREAM=0. The feed object is never
modified; we poll its in-memory ltp marks, so a failure here cannot affect the connector or autoscalp.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from collections import deque

from .marketdata.health import ProviderFacts
from .marketdata.service import MarketDataService
from .marketdata.types import Tick
from .persistence.buffered_writer import BufferedCandleWriter
from .ws.delta_protocol import DeltaHub

EX = {1: "NSE", 2: "NFO", 3: "BSE", 4: "BFO", 5: "MCX"}
SESSION_EX = {"NSE", "BSE", "MCX"}       # candle engine session map knows these; NFO/BFO options are skipped (no session spec)
MAX_INSTRUMENTS = 40


def enabled() -> bool:
    return os.environ.get("PLATFORM_STREAM", "1").strip().lower() not in ("0", "false", "off", "no")


class StallWatchdog:
    """Separate thread: if the event loop stops beating for > threshold_sec, record the loop thread's stack (what is blocking it).
    Diagnostic only; reads sys._current_frames(), never interrupts anything."""
    def __init__(self, loop_thread_id: int, threshold_sec: float = 3.0, keep: int = 20):
        self.tid, self.threshold = loop_thread_id, threshold_sec
        self.beat = time.time()
        self.events: deque = deque(maxlen=keep)
        self._open: dict | None = None
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="platform-stall-watchdog", daemon=True)

    def start(self):
        self._t.start()

    def stop(self):
        self._stop.set()

    def heartbeat(self):
        now = time.time()
        if self._open is not None:                           # loop is back: close the stall record
            self._open["ended"] = now
            self._open["duration_sec"] = round(now - self._open["since"], 2)
            self._open = None
        self.beat = now

    def _run(self):
        while not self._stop.wait(0.5):
            lag = time.time() - self.beat
            if lag > self.threshold and self._open is None:
                fr = sys._current_frames().get(self.tid)
                frames = traceback.format_stack(fr) if fr else ["<loop thread frame unavailable>"]
                # keep every application frame (who called) plus the deepest 3 library frames (what blocked)
                app_frames = [f for f in frames if "/app/" in f.split("\n", 1)[0]]
                stack = app_frames[-12:] + frames[-3:]
                self._open = {"since": self.beat, "detected": time.time(), "duration_sec": None, "stack": [x.strip() for x in stack]}
                self.events.append(self._open)


class PlatformLive:
    def __init__(self, get_feed, poll_sec: float = 0.25):
        self.get_feed, self.poll = get_feed, poll_sec
        self.hub = DeltaHub()
        self.writer = BufferedCandleWriter()
        self.svc = MarketDataService(self.writer)
        self.svc.subscribers.append(self._publish)
        self.svc.arbiter.mark_validated("ANGELONE", "policy: default primary provider (production feed in use); Dhan stays unvalidated")
        self._seen: dict = {}                      # token -> last ts_ms ingested
        self._last_tick_ts: dict = {}
        self._task: asyncio.Task | None = None
        self.ticks_in = 0
        self.last_error: str | None = None
        self._last_eval = 0.0
        self.watchdog: StallWatchdog | None = None

    def start(self):
        self.writer.start()
        self.watchdog = StallWatchdog(threading.get_ident())     # start() runs on the loop thread
        self.watchdog.start()
        self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
        if self.watchdog:
            self.watchdog.stop()
        self.writer.stop()

    def _publish(self, candle):                   # called by MarketDataService for every closed candle
        try:
            self.hub.publish(candle)
        except Exception as e:
            self.last_error = f"publish: {e!r}"

    def _emit(self, closed):                      # service already persisted + published them
        return closed

    def _poll_once(self, now: float):
        feed = self.get_feed()
        if feed is None:
            return
        desired = dict(getattr(feed, "_desired", {}) or {})
        marks = dict(getattr(feed, "ltp", {}) or {})
        n = 0
        for tok, rec in marks.items():
            ex = EX.get(desired.get(tok))
            if ex not in SESSION_EX or n >= MAX_INSTRUMENTS:
                continue
            n += 1
            if self._seen.get(tok) == rec["ts_ms"] and rec["recv"] <= self._seen.get(("r", tok), 0):
                continue
            self._seen[tok] = rec["ts_ms"]; self._seen[("r", tok)] = rec["recv"]
            key = f"{ex}:{tok}"
            tk = Tick("ANGELONE", key, float(rec["ltp"]), rec["ts_ms"] / 1000.0, rec["recv"])
            self._last_tick_ts[key] = rec["recv"]
            self.ticks_in += 1
            self._emit(self.svc.on_tick(tk))
        self._emit(self.svc.tick(now))
        if now - self._last_eval >= 2.0:
            self._last_eval = now
            st = feed.status() if hasattr(feed, "status") else {}
            facts = ProviderFacts("ANGELONE", auth_state="VALID" if st.get("data_flowing") else "UNKNOWN",
                                  socket_open=bool(st.get("socket_open")), subscribed=bool(st.get("subscribed")),
                                  last_message_ts=feed.last_msg_ts if getattr(feed, "last_msg_ts", None) else None,
                                  instrument_last_ts=dict(self._last_tick_ts), last_error=st.get("last_error"))
            req = [k for k in self._last_tick_ts if self.svc.engine.in_session(k, now)]     # only instruments whose session is open
            self.svc.evaluate({"ANGELONE": facts}, {"ANGELONE": req}, now)

    async def _loop(self):
        while True:
            try:
                if self.watchdog:
                    self.watchdog.heartbeat()
                self._poll_once(time.time())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            await asyncio.sleep(self.poll)

    def _diagnostics(self) -> dict:
        """Read-only health extras: DB lock contention (named offenders), Angel feed connection counters, AI provider circuit breaker."""
        out: dict = {}
        try:
            from .. import db as _db
            ls = _db.LOCK_STATS
            out["db_lock"] = {"acquires": ls["acquires"], "max_wait_sec": round(ls["max_wait_sec"], 2), "max_hold_sec": round(ls["max_hold_sec"], 2),
                              "recent_slow": list(ls["slow"][-5:])}
        except Exception as e:
            out["db_lock"] = {"error": repr(e)[:80]}
        try:
            fs = self.get_feed().status()
            out["angel_feed"] = {k: fs.get(k) for k in ("socket_open", "data_flowing", "connects", "reconnects", "cred_failures", "last_connect_age_sec",
                                                        "disconnects_last_hour", "recent_disconnects", "last_error")}
        except Exception as e:
            out["angel_feed"] = {"error": repr(e)[:80]}
        try:
            from ..ai import ai_client as _ai
            out["ai_breaker"] = _ai.breaker_status()
        except Exception as e:
            out["ai_breaker"] = {"error": repr(e)[:80]}
        return out

    def status(self) -> dict:
        d = self.svc.decision
        return {"enabled": True, "ticks_in": self.ticks_in, "last_error": self.last_error,
                "active_provider": getattr(d, "active", None), "decision_reasons": getattr(d, "reasons", None),
                "health": {n: {"state": h.state, "reasons": h.reasons, "fresh": len(h.fresh), "stale": h.stale[:8]}
                           for n, h in self.svc.healths.items()}, "trading_blocked": self.svc.trading_blocked,
                "writer": self.writer.status(), "hub": self.hub.status(),
                "gate": getattr(self.svc.gate, "counters", None) and dict(self.svc.gate.counters),
                "loop_stalls": list(self.watchdog.events) if self.watchdog else [],
                **self._diagnostics(),
                "note": "observation/charting only; Angel LTP-only (no volume/OI)"}


async def ws_session(live: PlatformLive, websocket):
    """Delta protocol over a websocket: client sends subscribe/resync; server pushes snapshot + candle deltas."""
    sub = None
    async def reader():
        nonlocal sub
        while True:
            m = json.loads(await websocket.receive_text())
            if m.get("t") == "subscribe" or m.get("t") == "resync":
                if sub:
                    live.hub.unsubscribe(sub)
                sub = live.hub.subscribe(str(m.get("i")), int(m.get("tf", 60)))
    rt = asyncio.create_task(reader())
    try:
        while not rt.done():
            if sub:
                for msg in sub.drain():
                    await websocket.send_text(json.dumps(msg))
            await asyncio.sleep(0.2)
        rt.result()
    finally:
        rt.cancel()
        if sub:
            live.hub.unsubscribe(sub)
