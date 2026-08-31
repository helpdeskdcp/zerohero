"""
Angel One SmartWebSocketV2 market-data feed + in-memory cache.

    Angel One WebSocket  ->  LTP cache (+ light 1m OHLC)  ->  ScalpRunner

Primary job: give the scalp runner a FRESH last-traded price for every open
scalp so target / stop / trailing / time exits fire on live ticks instead of
minute-lagged REST candles. Entry signals still run off REST candles (which
carry volume); this feed is the exit-management clock.

Protocol (per Angel's "Binary Market Data" spec):
  * wss://smartapisocket.angelone.in/smart-stream
  * handshake headers: Authorization / x-api-key / x-client-code / x-feed-token
  * subscribe frame (JSON): {"action":1,"params":{"mode":1,"tokenList":[...]}}
  * server pushes BINARY frames; LTP packet (mode 1) is 51 bytes, little-endian:
      off 0   u8   subscription mode
      off 1   u8   exchange type
      off 2   char[25] token (null-padded ascii)
      off 27  i64  sequence number
      off 35  i64  exchange timestamp (ms)
      off 43  i64  last traded price  (price * 100)
    multiple packets may be concatenated in one frame.
  * client sends the text "ping" every ~25s; server replies "pong".

Degrades safely: if creds are missing or the socket can't connect it simply
reports not-connected and the runner falls back to REST / replay marks.
"""
from __future__ import annotations
import json
import time
import struct
import asyncio
import math
from collections import deque

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"

# our exchange string -> Angel exchangeType code
EXCHANGE_TYPE = {
    "NSE": 1, "NSE_CM": 1,
    "NFO": 2, "NSE_FO": 2,
    "BSE": 3, "BSE_CM": 3,
    "BFO": 4, "BSE_FO": 4,
    "MCX": 5, "MCX_FO": 5,
    "NCX": 7,
    "CDS": 13, "CDE_FO": 13,
}

_LTP_PACKET = 51
_HEARTBEAT_SEC = 25
LTP_MAX_AGE_SEC = 12           # a mark older than this is considered stale


def is_ltp_fresh(age_sec, max_age_sec: float = LTP_MAX_AGE_SEC) -> bool:
    """Return whether a quoted LTP is safe to use for live monitoring.

    This is deliberately the single freshness rule used by both the execution
    feed and API presentation.  Missing, malformed and non-finite ages fail
    closed rather than being treated as current market data.
    """
    try:
        age = float(age_sec)
    except (TypeError, ValueError):
        return False
    return math.isfinite(age) and 0 <= age <= max_age_sec


def parse_binary(payload: bytes) -> list[dict]:
    """Split a binary frame into LTP ticks. Tolerates concatenated packets and
    ignores trailing bytes / larger (quote/snapquote) packets we didn't ask for."""
    out = []
    n = len(payload)
    i = 0
    while i + _LTP_PACKET <= n:
        mode = payload[i]
        exch = payload[i + 1]
        token = payload[i + 2:i + 27].split(b"\x00", 1)[0].decode("ascii", "ignore").strip()
        # sequence @ i+27 (i64) — unused
        ts_ms = struct.unpack_from("<q", payload, i + 35)[0]
        ltp_raw = struct.unpack_from("<q", payload, i + 43)[0]
        if token:
            out.append({
                "token": token, "exchange_type": exch, "mode": mode,
                "ltp": ltp_raw / 100.0, "ts_ms": ts_ms,
            })
        i += _LTP_PACKET
    return out


class AngelMarketFeed:
    def __init__(self, cred_provider=None):
        # cred_provider() -> (status, {jwt, feed_token, api_key, client_code})
        self._cred_provider = cred_provider
        self.ltp: dict[str, dict] = {}          # token -> {ltp, ts_ms, recv}
        self._candles: dict[str, deque] = {}    # token -> deque[[t,o,h,l,c,v]]
        self._desired: dict[str, int] = {}      # token -> exchange_type (union of all owners)
        self._desired_by_owner: dict[str, dict[str, int]] = {}   # owner -> its token set
        self._active: set[str] = set()          # tokens currently subscribed on the wire
        self.connected = False
        self.last_error = None
        self.last_msg_ts = None
        self.subscribe_generation = 0
        self._ws = None
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ---------------- public ----------------
    def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                pass

    def subscribe(self, tokens: list[dict], *, owner: str = "default"):
        """tokens: [{'token': '99926000', 'exchange_type': 1}, ...]. Sets THIS
        owner's desired set; the wire subscription is the UNION across owners so
        two engines sharing one feed (autoscalp + scalp_runner) never clobber
        each other. The run loop reconciles on its next pass."""
        new = {}
        for t in tokens or []:
            tok = str(t.get("token") or "").strip()
            if not tok:
                continue
            ex = t.get("exchange_type")
            if ex is None:
                ex = EXCHANGE_TYPE.get(str(t.get("exchange") or "NSE").upper(), 1)
            new[tok] = int(ex)
        if self._desired_by_owner.get(owner) == new:
            return
        self._desired_by_owner[owner] = new
        union: dict[str, int] = {}
        for s in self._desired_by_owner.values():
            union.update(s)
        if union != self._desired:
            self._desired = union
            self.subscribe_generation += 1

    def get_ltp(self, token: str, max_age_sec: float = LTP_MAX_AGE_SEC):
        rec = self.ltp.get(str(token))
        if not rec:
            return None
        if not is_ltp_fresh(time.time() - rec["recv"], max_age_sec):
            return None
        return rec["ltp"]

    def get_candles(self, token: str) -> list[list]:
        return list(self._candles.get(str(token), []))

    def status(self) -> dict:
        now = time.time()
        def mark_status(r):
            age = now - r["recv"]
            return {"ltp": r["ltp"], "age_sec": age, "fresh": is_ltp_fresh(age)}
        return {
            "connected": self.connected,
            "last_error": self.last_error,
            "last_msg_age_sec": round(now - self.last_msg_ts, 1) if self.last_msg_ts else None,
            "desired_tokens": sorted(self._desired.keys()),
            "active_tokens": sorted(self._active),
            "marks": {
                tok: mark_status(r)
                for tok, r in sorted(self.ltp.items())
            },
        }

    # ---------------- internals ----------------
    def _ingest(self, tick: dict):
        tok = tick["token"]
        ltp = tick["ltp"]
        ts_ms = tick["ts_ms"] or int(time.time() * 1000)
        self.ltp[tok] = {"ltp": ltp, "ts_ms": ts_ms, "recv": time.time()}
        # light 1-minute OHLC (no volume in LTP mode)
        bucket = (ts_ms // 60000) * 60
        dq = self._candles.setdefault(tok, deque(maxlen=240))
        if dq and dq[-1][0] == bucket:
            c = dq[-1]
            c[2] = max(c[2], ltp)
            c[3] = min(c[3], ltp)
            c[4] = ltp
        else:
            dq.append([bucket, ltp, ltp, ltp, ltp, 0])

    async def _send_sub(self, ws, action: int, desired: dict):
        by_ex: dict[int, list] = {}
        for tok, ex in desired.items():
            by_ex.setdefault(ex, []).append(tok)
        if not by_ex:
            return
        msg = {
            "correlationID": "chanakya-scalp",
            "action": action,  # 1 subscribe, 0 unsubscribe
            "params": {"mode": 1, "tokenList": [
                {"exchangeType": ex, "tokens": toks} for ex, toks in by_ex.items()
            ]},
        }
        await ws.send(json.dumps(msg))

    async def _run(self):
        try:
            import websockets
        except Exception as e:  # pragma: no cover
            self.last_error = f"websockets import failed: {e}"
            return

        backoff = 2
        while not self._stop.is_set():
            if not self._desired:
                await self._sleep(1)
                continue
            if not self._cred_provider:
                self.last_error = "no credential provider"
                await self._sleep(5)
                continue
            status, creds = self._cred_provider()
            if status != "OK" or not creds or not creds.get("feed_token"):
                self.last_error = f"stream credentials unavailable ({status})"
                await self._sleep(5)
                continue

            headers = {
                "Authorization": creds["jwt"],
                "x-api-key": creds["api_key"],
                "x-client-code": creds["client_code"],
                "x-feed-token": creds["feed_token"],
            }
            try:
                async with websockets.connect(
                    WS_URL, additional_headers=headers, ping_interval=None, max_size=None
                ) as ws:
                    self._ws = ws
                    self.connected = True
                    self.last_error = None
                    backoff = 2
                    self._active = set()
                    await self._session(ws)
            except TypeError:
                # older websockets uses extra_headers=
                try:
                    async with websockets.connect(
                        WS_URL, extra_headers=headers, ping_interval=None, max_size=None
                    ) as ws:
                        self._ws = ws
                        self.connected = True
                        self.last_error = None
                        backoff = 2
                        self._active = set()
                        await self._session(ws)
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {e}"
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
            finally:
                self.connected = False
                self._ws = None
            await self._sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def _session(self, ws):
        gen = -1
        last_hb = 0.0
        while not self._stop.is_set():
            # reconcile subscriptions
            if gen != self.subscribe_generation:
                desired = dict(self._desired)
                add = {t: e for t, e in desired.items() if t not in self._active}
                drop = {t: self._desired.get(t, 1) for t in self._active if t not in desired}
                if drop:
                    await self._send_sub(ws, 0, drop)
                if add:
                    await self._send_sub(ws, 1, add)
                self._active = set(desired.keys())
                gen = self.subscribe_generation

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
                continue
            except Exception as e:
                self.last_error = f"recv: {type(e).__name__}: {e}"
                return

            self.last_msg_ts = time.time()
            if isinstance(raw, (bytes, bytearray)):
                for tick in parse_binary(bytes(raw)):
                    self._ingest(tick)
            # text frames ("pong" / errors) are ignored

    async def _sleep(self, secs):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=secs)
        except asyncio.TimeoutError:
            pass
