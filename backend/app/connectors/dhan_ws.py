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

Auth: the access token comes from app.dhan_client.get_access_token() (stored
by the App-OAuth consent flow in app/dhan_client.py -- never read from
.env), plus DHAN_CLIENT_ID (the same account id used for that consent flow,
set once in .env). Both required; the worker is inert without them,
matching this codebase's fail-closed convention. Dhan's access token is
short-lived (~24h) -- once it expires the worker starts failing closed again
until the user re-completes the OAuth login.

Instruments: DHAN_ORDERFLOW_AUTO_SYMBOLS="NIFTY,BANKNIFTY" gets each
symbol's front-month future + nearest-expiry ATM CE/PE re-resolved once per
IST calendar day (app.connectors.dhan_instruments) against Dhan's own
instrument master + this app's live spot -- a hand-picked security_id would
otherwise go dead at that contract's next expiry (NIFTY/BANKNIFTY weekly
options can expire within days of being picked). DHAN_ORDERFLOW_INSTRUMENTS
is for anything NOT auto-resolved (e.g. an MCX commodity future, which rolls
monthly and has no ATM-options need here) -- explicit "SEGMENT:security_id"
pairs, always included alongside the auto-resolved set.

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

from . import dhan_instruments, dhan_parser

_log = logging.getLogger("dhan_ws")

WS_URL_FMT = "wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"
_SUBSCRIBE_FULL = 21
_MAX_INSTRUMENTS_PER_MSG = 100


def _auto_symbols() -> list[str]:
    """DHAN_ORDERFLOW_AUTO_SYMBOLS="NIFTY,BANKNIFTY" -- symbols whose
    front-month future + nearest-expiry ATM CE/PE get re-resolved once per
    IST calendar day (app.connectors.dhan_instruments), instead of a fixed
    security_id that goes stale at the next expiry."""
    raw = _env("DHAN_ORDERFLOW_AUTO_SYMBOLS")
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _auto_futures() -> list[str]:
    """DHAN_ORDERFLOW_AUTO_FUTURES="NATURALGAS,CRUDEOIL" -- futures-only symbols whose front-month contract is re-resolved daily (so the
    subscription follows the MCX roll). Unset => these two MCX symbols (the ones the static DHAN_ORDERFLOW_INSTRUMENTS line used to pin
    by hand, which went stale at every expiry); set it to an empty string to turn auto-roll off."""
    raw = os.environ.get("DHAN_ORDERFLOW_AUTO_FUTURES")
    raw = "NATURALGAS,CRUDEOIL" if raw is None else raw
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


def _fut_min_days() -> int:
    try:
        return max(0, int(_env("DHAN_ORDERFLOW_FUT_MIN_DAYS", "1")))
    except ValueError:
        return 1


_SPOT_STALE_SEC = 1800  # 30min -- stale enough to reject for a same-day ATM pick


def _histcap_latest_index_spot(symbol: str) -> float | None:
    """Fallback for a symbol app.mathematical_confluence.context doesn't
    resolve (e.g. SENSEX -- a BSE index the shared hub's WS/REST spot path
    doesn't cover, even though histcap's own capture DB has it, verified
    live 2026-09-28). Reads the capture DB directly, read-only, own
    short-lived connection -- never writes, never touches the hub's cache."""
    import sqlite3
    from datetime import datetime, timezone
    from ..histcap.store import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        try:
            row = conn.execute(
                "SELECT ltp, received_ts FROM quote_snapshots WHERE symbol=? AND kind='INDEX' "
                "ORDER BY received_ts DESC LIMIT 1", (symbol.upper(),)).fetchone()
        finally:
            conn.close()
    except Exception as e:
        _log.warning("dhan_ws: histcap spot fallback failed for %s: %s: %s", symbol, type(e).__name__, e)
        return None
    if not row or not row[0]:
        return None
    try:
        ts = datetime.fromisoformat(row[1].replace("Z", "+00:00"))
        age_sec = (datetime.now(timezone.utc) - ts).total_seconds()
    except (ValueError, TypeError):
        return None
    if age_sec > _SPOT_STALE_SEC:
        _log.warning("dhan_ws: histcap spot fallback for %s is stale (%.0fs old), rejecting", symbol, age_sec)
        return None
    return float(row[0])


def _get_spot(symbol: str) -> float | None:
    try:
        from ..mathematical_confluence import context as _ctx
        spot = _ctx.market_context(symbol).get("spot")
        if spot:
            return spot
    except Exception as e:
        _log.warning("dhan_ws: spot lookup failed for %s: %s: %s", symbol, type(e).__name__, e)
    return _histcap_latest_index_spot(symbol)


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
    app.engines.orderflow_engine to consume.

    Instrument resolution: pass `instruments` explicitly for a fixed list
    (used by tests, or a manual override) -- no daily re-resolution happens.
    Leave it None (the default/production path) to get DHAN_ORDERFLOW_INSTRUMENTS
    (static extras, e.g. an MCX future) merged with DHAN_ORDERFLOW_AUTO_SYMBOLS
    (front-month future + ATM CE/PE, re-resolved once per IST day via
    app.connectors.dhan_instruments -- see that module's docstring for why a
    fixed security_id would otherwise go stale at the next expiry)."""

    def __init__(self, instruments: list[dict] | None = None, queue_maxsize: int = 10_000):
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.enabled = _enabled()
        # `connected` now means AUTHENTICATED + DATA FLOWING: it turns True only when the first
        # valid frame arrives in a session. `socket_open` is the bare TCP/WebSocket state.
        self.connected = False
        self.socket_open = False
        self.auth_state = "UNKNOWN"
        self.reconnects = 0
        self.empty_sessions = 0
        self.last_close_info: dict | None = None
        self.last_error: str | None = None
        self.last_msg_ts: float | None = None
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self._explicit_instruments = instruments
        self._auto_syms = _auto_symbols() if instruments is None else []
        self._auto_futs = _auto_futures() if instruments is None else []
        self._instruments = list(instruments) if instruments is not None else list(instruments_from_env())
        self._instruments_day = None
        self._instrument_meta: dict = {}
        self._n_frames = 0
        self._n_ticks = 0
        self._n_dropped = 0

    def start(self):
        if not self.enabled:
            self.last_error = "disabled (DHAN_ORDERFLOW_ENABLED=0)"
            return
        if not self._instruments and not self._auto_syms and not self._auto_futs:
            self.last_error = "no instruments configured (DHAN_ORDERFLOW_INSTRUMENTS / DHAN_ORDERFLOW_AUTO_SYMBOLS)"
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def _refresh_instruments(self):
        """No-op when constructed with an explicit instrument list (tests /
        manual override). Otherwise re-resolves self._auto_syms once per IST
        calendar day and merges with the static DHAN_ORDERFLOW_INSTRUMENTS
        extras -- mirrors app.l2capture.worker's day-cached _refresh_tokens()."""
        if self._explicit_instruments is not None or not (self._auto_syms or self._auto_futs):
            return
        today = dhan_instruments.today_ist()
        if self._instruments_day == today:
            return
        try:
            auto, meta, incomplete = await asyncio.to_thread(
                dhan_instruments.resolve_daily_instruments, self._auto_syms, _get_spot)
            if self._auto_futs:                                # MCX futures-only: follow the roll instead of a hand-pinned security_id
                f_out, f_meta, f_inc = await asyncio.to_thread(
                    dhan_instruments.resolve_front_futures, self._auto_futs, _fut_min_days())
                auto, meta, incomplete = auto + f_out, {**meta, **f_meta}, incomplete + f_inc
        except Exception as e:
            self.last_error = f"instrument resolution: {type(e).__name__}: {e}"
            _log.warning("dhan_ws: %s", self.last_error)
            return
        static = instruments_from_env()
        try:                                                   # static extras need meta too, or their ticks persist with symbol NULL
            extra = await asyncio.to_thread(dhan_instruments.describe_static_instruments, static)
        except Exception as e:
            extra = {}
            _log.warning("dhan_ws: static instrument meta unavailable: %s: %s", type(e).__name__, e)
        today_iso = today.isoformat()
        have = {(i["exchange_segment"], str(i["security_id"])) for i in auto}
        kept = []
        for i in static:
            sid = str(i["security_id"])
            if (i["exchange_segment"], sid) in have:           # already covered by auto-resolution
                continue
            exp = (extra.get(sid) or {}).get("expiry")
            if exp and exp < today_iso:                        # a hand-pinned contract past its expiry is dead: never subscribe it
                _log.warning("dhan_ws: dropping expired static instrument %s (expiry %s)", sid, exp)
                continue
            kept.append(i)
        static = kept
        self._instruments = auto + static
        for sid, m in extra.items():
            meta.setdefault(sid, m)
        self._instrument_meta = meta
        if incomplete:
            # Don't lock in a partial result for the rest of the day (e.g. a
            # transient spot-lookup failure) -- keep whatever DID resolve so
            # the feed can still run, but retry the missing symbols on the
            # next loop iteration instead of waiting for tomorrow's refresh.
            self.last_error = f"instrument resolution incomplete, retrying: {incomplete}"
            _log.warning("dhan_ws: %s", self.last_error)
        else:
            self._instruments_day = today
        _log.info("dhan_ws: instruments refreshed for %s: auto=%s static=%s incomplete=%s",
                  today, {sid: m.get("trading_symbol") for sid, m in meta.items()}, static, incomplete)

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
            "enabled": self.enabled, "connected": self.connected, "socket_open": self.socket_open,
            "auth_state": self.auth_state, "reconnects": self.reconnects,
            "data_flowing": bool(self.last_msg_ts and now - self.last_msg_ts < 15 and self.connected),
            "consecutive_empty_sessions": self.empty_sessions, "last_close": self.last_close_info,
            "last_error": self.last_error,
            "last_msg_age_sec": round(now - self.last_msg_ts, 1) if self.last_msg_ts else None,
            "instruments": len(self._instruments), "frames": self._n_frames,
            "ticks_enqueued": self._n_ticks, "ticks_dropped_queue_full": self._n_dropped,
            "queue_depth": self.queue.qsize(),
            "instruments_day": self._instruments_day.isoformat() if self._instruments_day else None,
            "instrument_meta": self._instrument_meta,
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
            await self._refresh_instruments()
            if not self._instruments:
                self.last_error = "no instruments resolved yet"
                await self._sleep(15)
                continue
            from .. import dhan_client  # local: avoids a hard import cycle at module load
            token = dhan_client.get_access_token()
            client_id = _env("DHAN_CLIENT_ID")
            tstate = dhan_client.token_state()
            self.auth_state = tstate["state"]
            if token and tstate["state"] == "EXPIRED":
                # a connect attempt cannot succeed and only produces a reconnect storm
                self.last_error = (f"token expired ({tstate.get('expiry_time')}): "
                                   "reconnect via the Dhan OAuth login (no connection attempted)")
                self.connected = False
                await self._sleep(300)
                continue
            if not token or not client_id:
                self.last_error = "not connected -- complete the Dhan OAuth login (DHAN_CLIENT_ID not set, or no stored access token)"
                await self._sleep(15)
                continue
            try:
                frames_before = self._n_frames
                t_open = time.time()
                try:
                    await self._session(websockets, token, client_id)
                finally:
                    if self._n_frames == frames_before:       # opened but never produced a valid frame
                        self.empty_sessions += 1
                        self.auth_state = "REJECTED_OR_NO_DATA" if tstate["state"] != "EXPIRED" else "EXPIRED"
                        self.last_close_info = {"after_sec": round(time.time() - t_open, 1), "frames": 0}
                        backoff = min(max(backoff * 2, 30), 300)
                    else:
                        self.empty_sessions = 0
                        self.auth_state = "VALID"
                        backoff = 2
                self.reconnects += 1
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                _log.warning("dhan_ws: session ended with %s -- reconnect in %ds",
                             self.last_error, backoff)
            finally:
                self.connected = False
                self.socket_open = False
            await self._sleep(backoff)
            backoff = min(backoff * 2, 300)

    async def _session(self, websockets, token: str, client_id: str):
        url = WS_URL_FMT.format(token=token, client_id=client_id)
        session_day = self._instruments_day
        async with websockets.connect(url, ping_interval=None, max_size=None) as ws:
            self.socket_open = True
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
                # _refresh_instruments() only runs at the top of _loop()'s outer
                # reconnect cycle -- a long-lived, stable session (the normal
                # case) would otherwise never notice the IST day rolling over
                # and keep tracking yesterday's (possibly now-expired)
                # contracts indefinitely. Force a clean reconnect on day
                # change so the outer loop re-resolves and re-subscribes.
                if dhan_instruments.today_ist() != session_day:
                    _log.info("dhan_ws: IST day changed (%s -> %s), reconnecting to re-resolve instruments",
                             session_day, dhan_instruments.today_ist())
                    return
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=15)
                except asyncio.TimeoutError:
                    continue
                if not isinstance(raw, (bytes, bytearray)):
                    _log.debug("dhan_ws: non-binary frame: %s", str(raw)[:200])
                    continue
                self.last_msg_ts = time.time()
                self._n_frames += 1
                self.connected = True                          # first valid frame: authenticated + data flowing
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
