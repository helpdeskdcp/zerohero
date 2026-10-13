"""Live glue: samples the shared AngelOne WS feed (read-only) -> TickGate -> 1s candles -> buffered writer + delta hub.

Observation/charting only: no order path, no tenant trading. Kill switch PLATFORM_STREAM=0. The feed object is never
modified; we poll its in-memory ltp marks, so a failure here cannot affect the connector or autoscalp.
"""
from __future__ import annotations

import asyncio
import json
import os
import time

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

    def start(self):
        self.writer.start()
        self._task = asyncio.get_event_loop().create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except BaseException:
                pass
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
            req = [k for k, t in self._last_tick_ts.items() if now - t < 3600]
            self.svc.evaluate({"ANGELONE": facts}, {"ANGELONE": req}, now)

    async def _loop(self):
        while True:
            try:
                self._poll_once(time.time())
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            await asyncio.sleep(self.poll)

    def status(self) -> dict:
        d = self.svc.decision
        return {"enabled": True, "ticks_in": self.ticks_in, "last_error": self.last_error,
                "active_provider": getattr(d, "active", None), "trading_blocked": self.svc.trading_blocked,
                "writer": self.writer.status(), "hub": self.hub.status(),
                "gate": getattr(self.svc.gate, "counters", None) and dict(self.svc.gate.counters),
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
