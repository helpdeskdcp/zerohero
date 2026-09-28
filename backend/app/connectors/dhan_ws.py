"""
Dhan Live Market Feed -- WebSocket connector -> raw-tick queue ->
app.engines.orderflow_engine.

    Dhan WS (wss://api-feed.dhan.co)  ->  parse_frame() (dhan_parser.py)
    -> asyncio.Queue  ->  OrderflowEngine (classification + footprint bars +
       CVD + stacked imbalance, app/engines/orderflow_engine.py)

Classification (which needs per-security_id previous-tick state) happens in
the consumer engine, not here -- this connector's own job is just fast,
stateless I/O: connect, subscribe, decode packets, enqueue. Mirrors
app.l2capture.worker.L2CaptureWorker's shape (.start()/.stop()/.status(),
reconnect with backoff) but is a SEPARATE worker/socket/data path -- it
never touches the L2 capture DB, the scalp runner's feed, or app/execution/
(LIVE order path). RESEARCH DATA PATH ONLY, gated OFF by default.

Auth: DHAN_ACCESS_TOKEN (stored by app.dhan_client's App-OAuth consent flow
-- see app.dhan_client.get_access_token()) and DHAN_CLIENT_ID (the same
account id used for that consent flow). Both required; the worker is inert
without them, matching this codebase's fail-closed convention.

Protocol (https://dhanhq.co/docs/v2/live-market-feed/ + .../annexure/,
verified live 2026-09-28):
  * wss://api-feed.dhan.co?version=2&token={accessToken}&clientId={clientId}&authType=2
  * subscribe: {"RequestCode": 21, "InstrumentCount": N,
    "InstrumentList": [{"ExchangeSegment": "...", "SecurityId": "..."}]}
    -- RequestCode 21 = Subscribe FULL packet (the only mode carrying
    5-level market depth / best bid-ask, needed for the quote-rule
    classification downstream). Max 100 instruments per message, 5000 per
    connection, 5 connections per user.
  * server pings every 10s -- answered automatically by the `websockets`
    library's built-in protocol-level pong; no app-level heartbeat text
    frame needed here (unlike Angel's text "ping"/"pong" convention).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

from . import dhan_parser

_log = logging.getLogger("dhan_ws")

WS_URL_FMT = "wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"
_SUBSCRIBE_FULL = 21
_MAX_INSTRUMENTS_PER_MSG = 100


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    return v if v is not None else default


def _enabled() -> bool:
    return _env("DHAN_ORDERFLOW_ENABLED", "0").lower() not in ("0", "false", "no", "off", "")


def instruments_from_env() -> list[dict]:
    """DHAN_ORDERFLOW_INSTRUMENTS="NSE_FNO:49081,NSE_FNO:49082" -- explicit
    exchange_segment:security_id pairs. No implicit symbol->security_id
    resolution here (that needs Dhan's own instrument master, a separate
    concern) -- the caller supplies real security ids."""
    raw = _env("DHAN_ORDERFLOW_INSTRUMENTS")
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            continue
        seg, sid = part.split(":", 1)
        seg, sid = seg.strip(), sid.strip()
        if seg and sid:
            out.append({"exchange_segment": seg, "security_id": sid})
    return out


class DhanOrderflowWorker:
    """Owns the WS connection lifecycle + feeds a queue for
    app.engines.orderflow_engine to consume."""

    def __init__(self, instruments: list[dict] | None = None, queue_maxsize: int = 10_000):
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.enabled = _enabled()
        self.connected = False
        self.last_error: str | None = None
        self.last_msg_ts: float | None = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._instruments = instruments if instruments is not None else instruments_from_env()
        self._n_frames = 0
        self._n_ticks = 0
        self._n_dropped = 0

    def start(self):
        if not self.enabled:
            self.last_error = "disabled (DHAN_ORDERFLOW_ENABLED=0)"
            return
        if not self._instruments:
            self.last_error = "no instruments configured (DHAN_ORDERFLOW_INSTRUMENTS)"
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=6)
            except Exception:
                pass
        self.connected = False

    def status(self) -> dict:
        now = time.time()
        return {
            "enabled": self.enabled, "connected": self.connected, "last_error": self.last_error,
            "last_msg_age_sec": round(now - self.last_msg_ts, 1) if self.last_msg_ts else None,
            "instruments": len(self._instruments), "frames": self._n_frames,
            "ticks_enqueued": self._n_ticks, "ticks_dropped_queue_full": self._n_dropped,
            "queue_depth": self.queue.qsize(),
        }

    async def _loop(self):
        try:
            import websockets
        except Exception as e:  # pragma: no cover
            self.last_error = f"websockets import failed: {e}"
            _log.error("dhan_ws: %s", self.last_error)
            return
        backoff = 2
        while not self._stop.is_set():
            token = _env("DHAN_ACCESS_TOKEN")
            client_id = _env("DHAN_CLIENT_ID")
            if not token or not client_id:
                self.last_error = "DHAN_ACCESS_TOKEN / DHAN_CLIENT_ID not set"
                await self._sleep(15)
                continue
            try:
                await self._session(websockets, token, client_id)
                backoff = 2
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                _log.warning("dhan_ws: session ended with %s -- reconnect in %ds",
                             self.last_error, backoff)
            finally:
                self.connected = False
            await self._sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _session(self, websockets, token: str, client_id: str):
        url = WS_URL_FMT.format(token=token, client_id=client_id)
        async with websockets.connect(url, ping_interval=None, max_size=None) as ws:
            self.connected = True
            self.last_error = None
            _log.info("dhan_ws: connected, subscribing %d instrument(s)", len(self._instruments))
            for start in range(0, len(self._instruments), _MAX_INSTRUMENTS_PER_MSG):
                batch = self._instruments[start:start + _MAX_INSTRUMENTS_PER_MSG]
                await ws.send(json.dumps({
                    "RequestCode": _SUBSCRIBE_FULL, "InstrumentCount": len(batch),
                    "InstrumentList": [{"ExchangeSegment": b["exchange_segment"],
                                        "SecurityId": str(b["security_id"])} for b in batch],
                }))
            while not self._stop.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15)
                except asyncio.TimeoutError:
                    continue
                if not isinstance(raw, (bytes, bytearray)):
                    _log.debug("dhan_ws: non-binary frame: %s", str(raw)[:200])
                    continue
                self.last_msg_ts = time.time()
                self._n_frames += 1
                for packet in dhan_parser.parse_frame(bytes(raw)):
                    if packet["type"] == "DISCONNECT":
                        _log.warning("dhan_ws: server sent disconnect, reason_code=%s",
                                     packet.get("reason_code"))
                        return
                    if packet["type"] != "FULL":
                        continue
                    try:
                        self.queue.put_nowait(packet)
                        self._n_ticks += 1
                    except asyncio.QueueFull:
                        self._n_dropped += 1
                        _log.warning("dhan_ws: queue full, dropping tick (security_id=%s)",
                                     packet.get("security_id"))

    async def _sleep(self, secs: float):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, secs))
        except asyncio.TimeoutError:
            pass
