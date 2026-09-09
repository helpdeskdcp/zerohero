"""
L2 SnapQuote capture worker -- Angel One WebSocket subscription mode 3.

RESEARCH DATA CAPTURE ONLY. This opens its OWN WebSocket connection (it does not
touch the scalp runner's mode-1 feed), subscribes the front-month FUTURE of a
small symbol set in mode 3, and appends every SnapQuote packet to a SEPARATE
research DB via app/l2capture/store.L2Store. It emits no BUY/SELL/order/signal
and changes nothing on the trading / execution / H1-H7 / frozen path.

Enable with  L2_CAPTURE_ENABLED=1  (default 0 -> the worker is inert). See
backend/L2_SNAPQUOTE_CAPTURE.md and ORDERFLOW_STAGE9_L2_RESEARCH.md section 6b.

Design mirrors app/histcap/worker.CaptureWorker: an async task, a cross-process
leader lease so `uvicorn --workers N` still captures once, market-hours gating,
reconnect with backoff.
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone

from .. import db
from ..connectors import angelone
from ..connectors.angel_ws import EXCHANGE_TYPE
from .snapquote import parse_snapquote
from .store import L2Store

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
_LEASE_KEY = "l2_capture_lease"
_LEASE_TTL_SEC = 45
_HEARTBEAT_SEC = 25

_MCX_SYMBOLS = {"CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI", "GOLD",
                "GOLDM", "SILVER", "SILVERM", "COPPER", "ZINC", "ALUMINIUM"}


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v is not None and v != "" else default


def _enabled() -> bool:
    return _env("L2_CAPTURE_ENABLED", "0").lower() not in ("0", "false", "no", "off")


def _symbols() -> list[str]:
    return [s.strip().upper() for s in _env("L2_CAPTURE_SYMBOLS", "NIFTY,CRUDEOIL,NATURALGAS").split(",")
            if s.strip()]


def _resolve_tokens(symbols: list[str]) -> tuple[list[dict], dict]:
    """[{token, exchange_type}], {token: {symbol, kind, exchange}} for the
    front-month FUTURE of each symbol. Registry-only; nothing here feeds an order."""
    from .. import instruments
    want, meta = [], {}
    for sym in symbols:
        try:
            if sym in _MCX_SYMBOLS:
                m = instruments.resolve_mcx_future(sym)
            else:
                m = instruments.resolve_index_future(sym)
        except Exception:
            m = None
        if not m or m.get("status") != "OK" or not m.get("symboltoken"):
            continue
        tok = str(m["symboltoken"])
        ex = str(m.get("exchange") or "").upper()
        et = EXCHANGE_TYPE.get(ex, 5 if sym in _MCX_SYMBOLS else 2)
        want.append({"token": tok, "exchange_type": et})
        meta[tok] = {"symbol": sym, "kind": "FUTURE", "exchange": ex}
    return want, meta


def _any_market_trading() -> bool:
    try:
        from .. import market_calendar
        return bool(market_calendar.is_trading("NSE") or market_calendar.is_trading("MCX"))
    except Exception:
        return True  # fail open -- capture is harmless; the socket just idles


class L2CaptureWorker:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._owner = f"{os.getpid()}-{random.randint(1000, 9999)}"
        self.is_leader = False
        self.connected = False
        self.last_error: str | None = None
        self.last_msg_ts: float | None = None
        self.enabled = _enabled()
        self.store: L2Store | None = None
        self._tokens: list[dict] = []
        self._meta: dict = {}
        self._tokens_day: str | None = None
        self._run_id: int | None = None
        self._n_frames = 0
        self._n_ticks = 0
        self._store_raw = _env("L2_CAPTURE_STORE_RAW", "1").lower() not in ("0", "false", "no")
        self._flush_sec = float(_env("L2_CAPTURE_FLUSH_SEC", "2.0"))

    # ---------------- lifecycle ----------------
    def start(self):
        if not self.enabled:
            self.last_error = "disabled (L2_CAPTURE_ENABLED=0)"
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.store = L2Store()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=6)
            except Exception:
                pass
        try:
            db.lease_release(_LEASE_KEY, self._owner)
        except Exception:
            pass
        if self._run_id and self.store:
            self.store.finish_run(self._run_id, n_frames=self._n_frames, n_ticks=self._n_ticks,
                                  note="worker stopped")
        if self.store:
            self.store.close()
        self.is_leader = self.connected = False

    def status(self) -> dict:
        now = time.time()
        s = {
            "enabled": self.enabled, "is_leader": self.is_leader, "connected": self.connected,
            "lease_owner": (db.lease_owner(_LEASE_KEY) if self.enabled else None),
            "last_error": self.last_error,
            "last_msg_age_sec": round(now - self.last_msg_ts, 1) if self.last_msg_ts else None,
            "tokens": self._tokens, "run_id": self._run_id,
            "frames_this_run": self._n_frames, "ticks_this_run": self._n_ticks,
        }
        if self.store:
            try:
                s["store"] = self.store.summary()
            except Exception as e:
                s["store_error"] = f"{type(e).__name__}: {e}"
        return s

    # ---------------- internals ----------------
    def _refresh_tokens(self):
        day = datetime.now(timezone.utc).date().isoformat()
        if self._tokens and self._tokens_day == day:
            return
        toks, meta = _resolve_tokens(_symbols())
        if toks:
            self._tokens, self._meta, self._tokens_day = toks, meta, day

    async def _loop(self):
        try:
            import websockets  # noqa: F401
        except Exception as e:  # pragma: no cover
            self.last_error = f"websockets import failed: {e}"
            return
        backoff = 2
        while not self._stop.is_set():
            try:
                self.is_leader = bool(await asyncio.to_thread(
                    db.lease_acquire, _LEASE_KEY, self._owner, _LEASE_TTL_SEC))
            except Exception as e:
                self.last_error = f"lease: {type(e).__name__}: {e}"
                await self._sleep(10)
                continue
            if not self.is_leader:
                await self._sleep(10)
                continue
            if not await asyncio.to_thread(_any_market_trading):
                await self._sleep(30)
                continue
            await asyncio.to_thread(self._refresh_tokens)
            if not self._tokens:
                self.last_error = "no front-month FUTURE tokens resolved"
                await self._sleep(30)
                continue
            status, creds = await asyncio.to_thread(angelone.get_stream_credentials)
            if status != "OK" or not creds or not creds.get("feed_token"):
                self.last_error = f"stream credentials unavailable ({status})"
                await self._sleep(5)
                continue
            try:
                await self._session(creds)
                backoff = 2
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            finally:
                self.connected = False
            await self._sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _session(self, creds: dict):
        import websockets
        headers = {
            "Authorization": creds["jwt"], "x-api-key": creds["api_key"],
            "x-client-code": creds["client_code"], "x-feed-token": creds["feed_token"],
        }
        # websockets >= 14 renamed extra_headers -> additional_headers, and the
        # kwarg is NOT validated until the connection actually opens -- so a
        # try/except around connect() is too early. Pick by version.
        try:
            _wsver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
        except Exception:
            _wsver = (0, 0)
        _hdr_kw = "additional_headers" if _wsver >= (14, 0) else "extra_headers"
        ws_cm = websockets.connect(WS_URL, **{_hdr_kw: headers},
                                   ping_interval=None, max_size=None)
        async with ws_cm as ws:
            self.connected = True
            self.last_error = None
            if self._run_id is None:
                self._run_id = await asyncio.to_thread(
                    self.store.start_run, "capture", self._tokens)
            # subscribe mode 3
            by_ex: dict[int, list] = {}
            for t in self._tokens:
                by_ex.setdefault(int(t["exchange_type"]), []).append(str(t["token"]))
            await ws.send(json.dumps({
                "correlationID": "l2-snapquote-capture",
                "action": 1,
                "params": {"mode": 3, "tokenList": [
                    {"exchangeType": ex, "tokens": toks} for ex, toks in by_ex.items()]},
            }))
            buf: list[bytes] = []
            last_flush = time.time()
            last_hb = 0.0
            while not self._stop.is_set():
                now = time.time()
                if now - last_hb >= _HEARTBEAT_SEC:
                    try:
                        await ws.send("ping")
                    except Exception:
                        return
                    last_hb = now
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    raw = None
                except Exception as e:
                    self.last_error = f"recv: {type(e).__name__}: {e}"
                    return
                if isinstance(raw, (bytes, bytearray)):
                    buf.append(bytes(raw))
                    self.last_msg_ts = time.time()
                if buf and (len(buf) >= 200 or (time.time() - last_flush) >= self._flush_sec):
                    await asyncio.to_thread(self._flush, list(buf))
                    buf.clear()
                    last_flush = time.time()
                if not await asyncio.to_thread(_any_market_trading):
                    return  # markets closed -> drop the socket, loop will idle

    def _flush(self, frames: list[bytes]):
        """Sync -- runs in a thread. Preserve each raw frame, parse, append ticks."""
        for fr in frames:
            recs = parse_snapquote(fr)
            self._n_frames += 1
            if not recs:
                continue
            raw_id = None
            if self._store_raw:
                try:
                    raw_id = self.store.put_raw_frame(fr, n_packets=len(recs), run_id=self._run_id)
                except Exception as e:
                    self.last_error = f"put_raw: {type(e).__name__}: {e}"
            try:
                self._n_ticks += self.store.insert_ticks(
                    recs, raw_id=raw_id, run_id=self._run_id, meta=self._meta)
            except Exception as e:
                self.last_error = f"insert: {type(e).__name__}: {e}"
        if self._run_id:
            try:
                self.store.finish_run(self._run_id, n_frames=self._n_frames,
                                      n_ticks=self._n_ticks, note="running")
            except Exception:
                pass

    async def _sleep(self, secs: float):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, secs))
        except asyncio.TimeoutError:
            pass
