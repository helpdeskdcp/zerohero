"""Arena tick tape: broker-speed quotes for the contracts the arena is watching, from the Angel One WebSocket (SnapQuote, mode 3: last price + best bid / ask + volume + OI, millisecond exchange stamp).

Why: settlement fills a BUY at the first stored ask after signal time + the execution delay and exits at the bid. The dense capture polled the REST quote every 3 s (9 s old on a busy minute), so an order waited
for the next poll before it could fill. With this tape every price change of a watched contract is stored within a fraction of a second, and the settlement engine (unchanged: it reads quote_snapshots) fills in
about one second. Rows go to the same table with source ANGELONE_WS_SNAP (unique snap_key per tick); the REST dense capture keeps running as the fallback.

Socket: Angel refuses a third WebSocket (HTTP 429 observed 29-Oct), so the tape rides the L2 research worker's connection (l2capture.worker: frame hook + queued subscribe messages; the research store still keeps only its own
tokens). Without that worker it falls back to opening its own socket.

Feed choice (measured 29-Oct from stored ticks): Angel WS tick lag p50 1.7 s with ms stamps and best-five depth; Dhan WS lag p50 1.2 s but second-resolution stamps and the feed was down (daily token);
the connector has no Upstox stream. Angel is therefore the primary; the tape opens its OWN connection (Angel allows 3 per client: the shared LTP feed and the L2 research capture use two) and backs off if it is refused.

Tiers: a contract with a signal registered in the last 90 minutes is HOT (every change, at most 4 a second); every other watched contract is WARM (at most 1 a second); an unchanged quote is re-stored every 4 s.
Kill switch ARENA_TICK_TAPE=0. Read-only market data: no order path. The pure part (TapeCore) is unit-tested; the socket loop takes any object with async send / recv."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import threading
import time
from datetime import datetime, timezone

from ..connectors.angel_ws import EXCHANGE_TYPE
from ..histcap import normalize as N
from ..l2capture.snapquote import parse_snapquote

_log = logging.getLogger("arena.tick_tape")
WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
SOURCE = "ANGELONE_WS_SNAP"
HOT_GAP_S, WARM_GAP_S, HEARTBEAT_S, HOT_WINDOW_S = 0.25, 1.0, 4.0, 90 * 60
MAX_TOKENS, REFRESH_S, PING_S, QUEUE_MAX = 120, 1.0, 25.0, 20000
BATCH_ROWS, SLOW_BATCH_S, BACKLOG_ROWS, DEGRADE_S = 100, 0.4, 3000, 60.0       # short write transactions (the main capture shares the write lock); a slow batch or a backlog drops the tape to the WARM rate for a minute
DEPTH_LEVELS = 3                                    # best-3 per side is enough for settlement (best bid / ask) and keeps a row small


OFF_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "TICK_TAPE_OFF")


def enabled() -> bool: return os.environ.get("ARENA_TICK_TAPE", "1").strip().lower() not in ("0", "false", "off", "no")


def switched_off() -> bool:
    """Runtime kill switch without a restart: create data/arena/TICK_TAPE_OFF (the socket is closed within 2 s and not reopened until the file is removed)."""
    return os.path.exists(OFF_FILE)


def _iso_now() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def quote_from_tick(t: dict) -> dict:
    """A SnapQuote tick as the Angel FULL-quote shape the REST path normalises (so one normaliser, one column mapping)."""
    d = t.get("depth") or {}
    return {"ltp": t["ltp"], "open": t.get("open"), "high": t.get("high"), "low": t.get("low"), "close": t.get("close"), "tradeVolume": t.get("volume"), "opnInterest": t.get("oi"), "avgPrice": t.get("atp"),
            "lastTradeQty": t.get("ltq"), "totBuyQuan": t.get("tot_buy_qty"), "totSellQuan": t.get("tot_sell_qty"), "exchTradeTime": t.get("exch_ts_ms"), "status": "OK",
            "upperCircuit": t.get("upper_circuit"), "lowerCircuit": t.get("lower_circuit"), "52WeekHigh": t.get("week52_high"), "52WeekLow": t.get("week52_low"),
            "depth": {"buy": (d.get("buy") or [])[:DEPTH_LEVELS], "sell": (d.get("sell") or [])[:DEPTH_LEVELS]}}


class TapeCore:
    """token -> contract meta, per-contract throttle, rows to store. No I/O, no clock of its own (time is passed in)."""

    def __init__(self):
        self.meta: dict = {}                         # token -> meta (+ "hot", "exchange_type")
        self.degraded_until = 0.0                    # the governor: until this time every contract is throttled like a WARM one
        self._last: dict = {}                        # instrument_key -> (bid, ask, ltp, written_at)
        self._pending: dict = {}                     # instrument_key -> row held back by the throttle
        self.stats = {"ticks": 0, "rows": 0, "throttled": 0, "unknown_token": 0, "bad_tick": 0}; self._lock = threading.Lock()      # the socket thread and the writer thread both touch the throttle state

    def set_metas(self, metas: list[dict]) -> tuple[dict, dict]:
        """-> (added {token: exchange_type}, removed {token: exchange_type}) versus the previous set."""
        new = {}
        for m in metas[:MAX_TOKENS]:
            ex = EXCHANGE_TYPE.get(str(m.get("exchange") or "NSE").upper())
            if ex is None or not m.get("token"): continue
            new[str(m["token"])] = {**m, "exchange_type": int(ex)}
        added = {k: v["exchange_type"] for k, v in new.items() if k not in self.meta}
        removed = {k: v["exchange_type"] for k, v in self.meta.items() if k not in new}
        self.meta = new
        return added, removed

    def row(self, t: dict, meta: dict, recv_iso: str) -> dict | None:
        try: ltp = float(t["ltp"])
        except (KeyError, TypeError, ValueError): return None
        if not ltp > 0: return None
        nq = N.norm_quote(quote_from_tick(t), meta, recv_iso)
        nq["snap_key"] = f"WS|{t.get('exch_ts_ms')}|{t.get('seq')}"; nq["source"] = SOURCE; nq["quote_status"] = "OK"
        return nq

    def on_ticks(self, ticks: list[dict], now: float, recv_iso: str | None = None) -> list[dict]:
        """Rows to store now (the rest is held back and comes out of flush_due)."""
        recv_iso = recv_iso or _iso_now(); out = []
        with self._lock: return self._on_ticks(ticks, now, recv_iso, out)

    def _on_ticks(self, ticks, now, recv_iso, out):
        for t in ticks:
            self.stats["ticks"] += 1
            meta = self.meta.get(str(t.get("token")))
            if meta is None or int(t.get("exchange_type", meta["exchange_type"])) != meta["exchange_type"]: self.stats["unknown_token"] += 1; continue
            nq = self.row(t, meta, recv_iso)
            if nq is None: self.stats["bad_tick"] += 1; continue
            ik = nq["instrument_key"]; key = (nq.get("bid"), nq.get("ask"), nq["ltp"]); last = self._last.get(ik); gap = HOT_GAP_S if (meta.get("hot") and now >= self.degraded_until) else WARM_GAP_S
            changed = last is None or key != last[:3]
            if last is not None and not changed and now - last[3] < HEARTBEAT_S: continue                       # unchanged and not yet due for a heartbeat
            if last is not None and changed and now - last[3] < gap:                                              # changed too soon: keep the NEWEST, store it when the gap has passed
                self._pending[ik] = nq; self.stats["throttled"] += 1; continue
            self._pending.pop(ik, None); self._last[ik] = (*key, now); out.append(nq); self.stats["rows"] += 1
        return out

    def flush_due(self, now: float) -> list[dict]:
        """Held-back rows whose throttle gap has passed (so the last change of a quiet contract is never left unstored)."""
        out = []
        with self._lock: return self._flush_due(now, out)

    def _flush_due(self, now, out):
        for ik, nq in list(self._pending.items()):
            meta = self.meta.get(str(nq.get("token")))
            gap = HOT_GAP_S if ((meta or {}).get("hot") and now >= self.degraded_until) else WARM_GAP_S; last = self._last.get(ik)
            if last is None or now - last[3] >= gap:
                self._pending.pop(ik, None); self._last[ik] = (nq.get("bid"), nq.get("ask"), nq["ltp"], now); out.append(nq); self.stats["rows"] += 1
        return out


class TickTape:
    def __init__(self, *, store=None, targets_fn=None, creds_fn=None, market_open_fn=None):
        self.core = TapeCore(); self._store = store; self._targets_fn = targets_fn or self._default_targets; self._creds_fn = creds_fn or self._default_creds; self._open_fn = market_open_fn or self._default_open
        self._q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX); self._stop = threading.Event(); self._threads: list = []; self.connected = False; self.last_tick_t = 0.0; self.last_error = None
        self.written = 0; self.dropped = 0; self._run_id = None; self._run_day = None; self.reconnects = 0; self.lag_ms = []
        self.mode = "own"; self._shared = None; self._subscribed: dict = {}; self._last_session_no = -1

    # ------------------------------------------------------------- dependencies (real ones are imported lazily so the tests need none of them)
    def _default_targets(self) -> list[dict]:
        from ..histcap import priority as P
        now = time.time(); inst = P._instance
        if inst is None: return []
        ents = [e for e in P._load() if e.get("until", 0) > now]
        ents.sort(key=lambda e: (e.get("kind") != "signal", -e.get("added", 0))); out = []
        for e in ents[:MAX_TOKENS]:
            try: m = inst._meta(e)
            except Exception: m = None
            if m: out.append({**m, "hot": e.get("kind") == "signal" and e.get("added", 0) >= now - HOT_WINDOW_S})
        return out

    def _default_creds(self):
        from ..connectors import angelone
        return angelone.get_stream_credentials()

    def _default_open(self) -> bool:
        from .. import market_calendar as mc
        return any(mc.is_trading(x) for x in ("NSE", "BSE", "MCX"))

    def _get_store(self):
        if self._store is None:
            from ..histcap.store import hist_store
            self._store = hist_store()
        return self._store

    # ------------------------------------------------------------- writer thread: the receive loop never waits for the database
    def _write(self, rows: list[dict]) -> int:
        if not rows: return 0
        st = self._get_store(); day = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
        if self._run_id is None or self._run_day != day: self._run_id = st.start_run("ARENA_TICK_TAPE", {}, True); self._run_day = day
        with st.independent_transaction() as conn:
            n = st.write_quotes(conn, rows, self._run_id, {"issues": [], "rejected": [], "flagged": 0})
        self.written += n; return n

    def _writer(self):                                                                                       # pragma: no cover (thread)
        while not self._stop.is_set():
            rows = []
            try:
                rows.append(self._q.get(timeout=0.2))
                while len(rows) < BATCH_ROWS: rows.append(self._q.get_nowait())
            except queue.Empty:
                pass
            rows += self.core.flush_due(time.time())
            if not rows: continue
            t_w = time.time()
            try: self._write(rows)
            except Exception as e:
                self.last_error = f"write: {type(e).__name__}: {str(e)[:100]}"; time.sleep(0.5 if "locked" in str(e).lower() else 2.0)
            self.govern(time.time() - t_w, self._q.qsize())

    def govern(self, batch_seconds: float, backlog: int, now: float | None = None) -> bool:
        """Protect the rest of the system: a slow write batch or a deep backlog puts every contract on the WARM rate for DEGRADE_S. -> True if (still) degraded."""
        now = now or time.time()
        if batch_seconds > SLOW_BATCH_S or backlog > BACKLOG_ROWS:
            if now >= self.core.degraded_until: _log.warning("arena tick tape: slow writes (%.2fs / backlog %d) -> WARM rate for %ds", batch_seconds, backlog, DEGRADE_S)
            self.core.degraded_until = now + DEGRADE_S
        return now < self.core.degraded_until

    def push(self, rows: list[dict]) -> None:
        for r in rows:
            try: self._q.put_nowait(r)
            except queue.Full: self.dropped += 1                                                              # the newest rows win elsewhere; never block the socket

    # ------------------------------------------------------------- shared socket (rides the L2 worker's connection)
    def offer_frame(self, raw: bytes) -> None:
        """Frame hook, called on the event-loop thread for every binary frame of the shared socket: parse, throttle, queue. Must stay fast."""
        if self.mode != "shared" or switched_off(): return
        ticks = parse_snapquote(raw)
        if not ticks: return
        self.last_tick_t = time.time()
        for t in ticks:
            if t.get("exch_ts_ms"): self.lag_ms.append(self.last_tick_t * 1000 - t["exch_ts_ms"])
        del self.lag_ms[:-500]
        self.push(self.core.on_ticks(ticks, self.last_tick_t))

    def shared_cycle(self, w) -> None:
        """One subscription-sync pass on the shared worker `w` (connected, session_no, own_tokens(), request_send(text))."""
        if not getattr(w, "connected", False): self.connected = False; self._subscribed.clear(); return
        if w.session_no != self._last_session_no: self._subscribed.clear(); self._last_session_no = w.session_no          # a new connection forgets every subscription
        own = {str(x) for x in w.own_tokens()}
        if switched_off() or not enabled(): self.core.set_metas([]); want = {}
        else:
            self.core.set_metas(self._targets_fn()); want = {k: v["exchange_type"] for k, v in self.core.meta.items() if k not in own}      # the worker already streams its own tokens
        for action, grp in ((1, {k: v for k, v in want.items() if k not in self._subscribed}), (0, {k: v for k, v in self._subscribed.items() if k not in want})):
            if not grp: continue
            by_ex: dict = {}
            for tok, ex in grp.items(): by_ex.setdefault(ex, []).append(tok)
            msg = json.dumps({"correlationID": "arena-tick-tape", "action": action, "params": {"mode": 3, "tokenList": [{"exchangeType": ex, "tokens": toks} for ex, toks in by_ex.items()]}})
            if w.request_send(msg):
                for tok, ex in grp.items():
                    if action: self._subscribed[tok] = ex
                    else: self._subscribed.pop(tok, None)
        self.connected = True; self.last_error = None                                                          # the shared socket is up and the subscriptions are in sync

    def _shared_loop(self):                                                                                  # pragma: no cover (thread)
        while not self._stop.is_set():
            try:
                if self._shared is not None: self.shared_cycle(self._shared)
            except Exception as e: self.last_error = f"shared: {type(e).__name__}: {str(e)[:100]}"
            self._stop.wait(REFRESH_S)

    # ------------------------------------------------------------- one websocket session
    async def session(self, ws, *, now_fn=time.time, max_seconds: float | None = None) -> None:
        """Subscribe (mode 3), keep the subscription equal to the watch list, turn frames into rows. Returns when the socket ends. `ws` needs async send() / recv()."""
        t_end = None if max_seconds is None else now_fn() + max_seconds; subscribed: dict = {}; last_refresh = last_ping = 0.0
        while not self._stop.is_set() and (t_end is None or now_fn() < t_end):
            now = now_fn()
            if switched_off(): self.last_error = "switched off (TICK_TAPE_OFF)"; return
            if now - last_refresh >= REFRESH_S:
                last_refresh = now
                try: self.core.set_metas(self._targets_fn())
                except Exception as e: self.last_error = f"targets: {type(e).__name__}"
                want = {k: v["exchange_type"] for k, v in self.core.meta.items()}
                add = {k: v for k, v in want.items() if k not in subscribed}; rem = {k: v for k, v in subscribed.items() if k not in want}
                for action, grp in ((1, add), (0, rem)):
                    if not grp: continue
                    by_ex: dict = {}
                    for tok, ex in grp.items(): by_ex.setdefault(ex, []).append(tok)
                    await ws.send(json.dumps({"correlationID": "arena-tick-tape", "action": action, "params": {"mode": 3, "tokenList": [{"exchangeType": ex, "tokens": toks} for ex, toks in by_ex.items()]}}))
                subscribed = dict(want)
            if now - last_ping >= PING_S: await ws.send("ping"); last_ping = now
            try: raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError: raw = None
            if isinstance(raw, (bytes, bytearray)):
                ticks = parse_snapquote(bytes(raw)); self.last_tick_t = time.time()
                for t in ticks:
                    if t.get("exch_ts_ms"): self.lag_ms.append(time.time() * 1000 - t["exch_ts_ms"])
                del self.lag_ms[:-500]
                self.push(self.core.on_ticks(ticks, now_fn()))
            elif raw is not None and any(k in str(raw).lower() for k in ("error", "invalid", "reject", "unauth", "limit")): self.last_error = "server: " + str(raw)[:120]

    async def _main(self):                                                                                   # pragma: no cover (needs the broker)
        import websockets
        backoff = 5
        while not self._stop.is_set():
            try:
                if switched_off() or not await asyncio.to_thread(self._open_fn): self.connected = False; await asyncio.sleep(30 if not switched_off() else 5); continue
                status, creds = await asyncio.to_thread(self._creds_fn)
                if status != "OK" or not creds or not creds.get("feed_token"): self.last_error = f"credentials {status}"; await asyncio.sleep(10); continue
                hdr = {"Authorization": creds["jwt"], "x-api-key": creds["api_key"], "x-client-code": creds["client_code"], "x-feed-token": creds["feed_token"]}
                kw = "additional_headers" if tuple(int(x) for x in websockets.__version__.split(".")[:2]) >= (14, 0) else "extra_headers"
                async with websockets.connect(WS_URL, **{kw: hdr}, ping_interval=None, max_size=None) as ws:
                    self.connected = True; self.last_error = None; backoff = 5; _log.info("arena tick tape connected")
                    await self.session(ws)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:100]}"; _log.warning("arena tick tape session ended: %s (retry in %ss)", self.last_error, backoff)
            self.connected = False; self.reconnects += 1
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 60)

    def start(self):                                                                                         # pragma: no cover (threads)
        if not enabled() or self._threads: return self
        from ..l2capture import worker as L2
        w = L2.shared_worker()
        if w is not None and getattr(w, "enabled", False):
            self.mode = "shared"; self._shared = w; L2.register_frame_hook(self.offer_frame); _log.info("arena tick tape: riding the L2 worker's websocket")
            targets = ((self._shared_loop, "arena-tick-tape-sync"), (self._writer, "arena-tick-tape-writer"))
        else:
            targets = ((lambda: asyncio.run(self._main()), "arena-tick-tape"), (self._writer, "arena-tick-tape-writer"))
        for target, name in targets:
            th = threading.Thread(target=target, name=name, daemon=True); th.start(); self._threads.append(th)
        return self

    def status(self) -> dict:
        lag = sorted(self.lag_ms)
        return {"enabled": enabled(), "mode": self.mode, "connected": self.connected, "tokens": len(self.core.meta), "hot": sum(1 for m in self.core.meta.values() if m.get("hot")), "ticks": self.core.stats["ticks"], "rows_stored": self.written,
                "throttled": self.core.stats["throttled"], "degraded": time.time() < self.core.degraded_until, "dropped": self.dropped, "queue": self._q.qsize(), "last_tick_age_s": (round(time.time() - self.last_tick_t, 2) if self.last_tick_t else None),
                "lag_ms_p50": (round(lag[len(lag) // 2]) if lag else None), "reconnects": self.reconnects, "last_error": self.last_error}


_instance: TickTape | None = None


def start_background() -> TickTape | None:
    global _instance
    if not enabled() or _instance is not None: return _instance
    _instance = TickTape().start(); return _instance
