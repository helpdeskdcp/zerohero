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
import logging
import os
import random
import time
from collections import Counter
from datetime import datetime, timezone

from .. import db
from ..connectors import angelone
from ..connectors.angel_ws import EXCHANGE_TYPE
from .snapquote import parse_snapquote
from .store import L2Store

_log = logging.getLogger("l2capture")

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
_LEASE_KEY = "l2_capture_lease"
_LEASE_TTL_SEC = 45
_HEARTBEAT_SEC = 25

# ---- packet validity: never write stale / invalid data as if it were live ----
_MAX_PACKET_LAG_SEC = 150.0     # exch_ts older than this vs wall clock -> STALE, rejected
_MAX_PACKET_SKEW_SEC = 15.0     # exch_ts ahead of wall clock by more than this -> BAD clock, rejected
_MIN_EXCH_TS_MS = 1_600_000_000_000   # ~2020-09; anything below = missing/garbage


def _validate_packet(rec: dict, *, now_ms: int) -> tuple[bool, str]:
    """(ok, reason). Rejects packets we must not persist as live capture."""
    ts = rec.get("exch_ts_ms")
    if not isinstance(ts, (int, float)) or ts < _MIN_EXCH_TS_MS:
        return False, "no_exch_ts"
    lag = (now_ms - ts) / 1000.0
    if lag > _MAX_PACKET_LAG_SEC:
        return False, "stale"
    if lag < -_MAX_PACKET_SKEW_SEC:
        return False, "future_ts"
    ltp = rec.get("ltp")
    if not isinstance(ltp, (int, float)) or ltp <= 0:
        return False, "bad_ltp"
    bid, ask = rec.get("bid"), rec.get("ask")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and bid > 0 and ask > 0 and bid >= ask:
        return False, "crossed_book"
    return True, ""

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
        except Exception as e:
            _log.warning("l2capture: resolve %s raised %s: %s", sym, type(e).__name__, e)
            m = None
        if not m or m.get("status") != "OK" or not m.get("symboltoken"):
            _log.warning("l2capture: no front-month FUTURE for %s (status=%s)",
                         sym, (m or {}).get("status"))
            continue
        tok = str(m["symboltoken"])
        ex = str(m.get("exchange") or "").upper()
        et = EXCHANGE_TYPE.get(ex, 5 if sym in _MCX_SYMBOLS else 2)
        want.append({"token": tok, "exchange_type": et})
        meta[tok] = {"symbol": sym, "kind": "FUTURE", "exchange": ex,
                     "expiry": m.get("expiry"), "tradingsymbol": m.get("symbol") or m.get("tradingsymbol")}
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
        self._n_rejected = 0
        self._reject_reasons: Counter = Counter()
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
            "rejected_this_run": self._n_rejected,
            "reject_reasons": dict(self._reject_reasons),
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
        want = _symbols()
        toks, meta = _resolve_tokens(want)
        got = sorted(m["symbol"] for m in meta.values())
        missing = [s for s in want if s not in got]
        if toks:
            self._tokens, self._meta, self._tokens_day = toks, meta, day
            _log.info("l2capture tokens resolved for %s: %s%s", day,
                      {m["symbol"]: f'{t}@{m.get("expiry")}' for t, m in meta.items()},
                      f"  MISSING={missing}" if missing else "")
        else:
            _log.warning("l2capture token resolution returned NOTHING for %s (wanted %s)", day, want)
        if missing:
            _log.warning("l2capture: no front-month FUTURE token for %s -- not captured", missing)

    async def _loop(self):
        try:
            import websockets  # noqa: F401
        except Exception as e:  # pragma: no cover
            self.last_error = f"websockets import failed: {e}"
            _log.error("l2capture: %s", self.last_error)
            return
        _log.info("l2capture worker loop started (owner=%s, symbols=%s)", self._owner, _symbols())
        backoff = 2
        _last_wait_reason = None
        while not self._stop.is_set():
            try:
                self.is_leader = bool(await asyncio.to_thread(
                    db.lease_acquire, _LEASE_KEY, self._owner, _LEASE_TTL_SEC))
            except Exception as e:
                self.last_error = f"lease: {type(e).__name__}: {e}"
                _log.warning("l2capture: lease error: %s", self.last_error)
                await self._sleep(10)
                continue
            if not self.is_leader:
                if _last_wait_reason != "not_leader":
                    _log.info("l2capture: another instance holds the capture lease -- standing by")
                    _last_wait_reason = "not_leader"
                await self._sleep(10)
                continue
            if not await asyncio.to_thread(_any_market_trading):
                if _last_wait_reason != "closed":
                    _log.info("l2capture: markets closed -- idle")
                    _last_wait_reason = "closed"
                await self._sleep(30)
                continue
            await asyncio.to_thread(self._refresh_tokens)
            if not self._tokens:
                self.last_error = "no front-month FUTURE tokens resolved"
                _log.warning("l2capture: %s -- retrying in 30s", self.last_error)
                await self._sleep(30)
                continue
            status, creds = await asyncio.to_thread(angelone.get_stream_credentials)
            if status != "OK" or not creds or not creds.get("feed_token"):
                self.last_error = f"stream credentials unavailable ({status})"
                if _last_wait_reason != f"creds:{status}":
                    _log.warning("l2capture: %s -- retrying", self.last_error)
                    _last_wait_reason = f"creds:{status}"
                await self._sleep(5)
                continue
            _last_wait_reason = None
            try:
                await self._session(creds)
                backoff = 2
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                _log.warning("l2capture: session ended with %s -- reconnect in %ds",
                             self.last_error, backoff)
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
        _log.info("l2capture: connecting %s (websockets %s, kw=%s)", WS_URL,
                  getattr(websockets, "__version__", "?"), _hdr_kw)
        async with ws_cm as ws:
            self.connected = True
            self.last_error = None
            _log.info("l2capture: connected; run_id=%s", self._run_id)
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
            _log.info("l2capture: subscribed mode 3, %d tokens across %d exchanges: %s",
                      len(self._tokens), len(by_ex),
                      {m.get("symbol"): t for t, m in self._meta.items()})
            buf: list[bytes] = []
            last_flush = time.time()
            last_hb = 0.0
            last_data = time.time()
            while not self._stop.is_set():
                now = time.time()
                if now - last_hb >= _HEARTBEAT_SEC:
                    try:
                        await ws.send("ping")
                    except Exception as e:
                        _log.warning("l2capture: heartbeat send failed (%s) -> reconnect", e)
                        return
                    last_hb = now
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=2)
                except asyncio.TimeoutError:
                    raw = None
                except Exception as e:
                    self.last_error = f"recv: {type(e).__name__}: {e}"
                    _log.warning("l2capture: recv error (%s) -> reconnect", self.last_error)
                    return
                if isinstance(raw, (bytes, bytearray)):
                    buf.append(bytes(raw))
                    self.last_msg_ts = last_data = time.time()
                elif raw is not None:
                    # Angel sends TEXT frames for pings/acks/errors -- log, never silent
                    txt = str(raw)[:300]
                    if any(k in txt.lower() for k in ("error", "invalid", "fail", "reject", "unauth")):
                        _log.warning("l2capture: control/error frame: %s", txt)
                    else:
                        _log.debug("l2capture: control frame: %s", txt)
                if buf and (len(buf) >= 200 or (time.time() - last_flush) >= self._flush_sec):
                    await asyncio.to_thread(self._flush, list(buf))
                    buf.clear()
                    last_flush = time.time()
                if time.time() - last_data > 90 and _any_market_trading():
                    _log.warning("l2capture: no binary data for %.0fs during market hours "
                                 "-> reconnect (subscription may have dropped)", time.time() - last_data)
                    return
                if not await asyncio.to_thread(_any_market_trading):
                    _log.info("l2capture: markets closed -> dropping socket, worker idles")
                    return

    def _flush(self, frames: list[bytes]):
        """Sync -- runs in a thread. Preserve each raw frame, VALIDATE, append.
        Invalid/stale packets are counted + logged, never written as live data."""
        now_ms = int(time.time() * 1000)
        for fr in frames:
            recs = parse_snapquote(fr)
            self._n_frames += 1
            if not recs:
                continue
            good, rejected = [], 0
            for rec in recs:
                ok, why = _validate_packet(rec, now_ms=now_ms)
                if ok:
                    good.append(rec)
                else:
                    rejected += 1
                    self._n_rejected += 1
                    self._reject_reasons[why] += 1
            if rejected:
                _log.warning("l2capture: rejected %d/%d packet(s) this frame (%s); "
                             "run totals reject=%d reasons=%s",
                             rejected, len(recs),
                             Counter(self._reject_reasons).most_common(3),
                             self._n_rejected, dict(self._reject_reasons))
            if not good:
                continue
            raw_id = None
            if self._store_raw:
                try:
                    raw_id = self.store.put_raw_frame(fr, n_packets=len(good), run_id=self._run_id)
                except Exception as e:
                    self.last_error = f"put_raw: {type(e).__name__}: {e}"
                    _log.warning("l2capture: put_raw_frame failed: %s", self.last_error)
            try:
                self._n_ticks += self.store.insert_ticks(
                    good, raw_id=raw_id, run_id=self._run_id, meta=self._meta)
            except Exception as e:
                self.last_error = f"insert: {type(e).__name__}: {e}"
                _log.warning("l2capture: insert_ticks failed: %s", self.last_error)
        if self._run_id:
            try:
                self.store.finish_run(
                    self._run_id, n_frames=self._n_frames, n_ticks=self._n_ticks,
                    note=json.dumps({"state": "running", "rejected": self._n_rejected,
                                     "reject_reasons": dict(self._reject_reasons)}))
            except Exception:
                pass

    async def _sleep(self, secs: float):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, secs))
        except asyncio.TimeoutError:
            pass
