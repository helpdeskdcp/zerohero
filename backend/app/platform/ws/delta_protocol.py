"""Live chart delta protocol (server side, transport-agnostic).

Per (instrument, timeframe) stream with a monotonically increasing `seq`:
  {"t":"snapshot","i":inst,"tf":tf,"seq":N,"candles":[...]}      full recent history (on subscribe / resync)
  {"t":"candle","i":inst,"tf":tf,"seq":N+1,"closed":bool,"c":{...}}  ONE changed candle (update/append)
A client that sees a seq gap asks for a resync and receives a fresh snapshot. Every subscriber has a
BOUNDED queue: a slow client is converted to "needs a snapshot" -- it can never block the hub, the
feed, or other subscribers. Market data is shared (master) data: tenant-private events must NOT go
through this hub (use PlatformEvent.visible_to).
"""
from __future__ import annotations

from collections import deque

IST_OFFSET = 19800


def candle_json(c) -> dict:
    d = {"ts": int(c.start_ts), "time": int(c.start_ts) + IST_OFFSET,        # `time` is shifted so lightweight-charts shows IST
         "open": c.o, "high": c.h, "low": c.l, "close": c.c, "ticks": c.tick_count, "provider": c.provider}
    if c.volume is not None:
        d["volume"], d["volume_complete"] = c.volume, bool(c.volume_complete)
    if c.oi is not None:
        d["oi"] = c.oi
        if c.oi_change is not None:
            d["oi_change"] = c.oi_change
    return d


class Subscriber:
    def __init__(self, hub: "DeltaHub", instrument: str, tf: int, max_queue: int):
        self.hub, self.instrument, self.tf = hub, instrument, tf
        self._q: deque = deque()
        self.max_queue = max_queue
        self.resyncs = 0

    def push(self, msg: dict) -> None:
        if len(self._q) >= self.max_queue:                  # slow consumer: drop backlog, replace with a fresh snapshot
            self._q.clear()
            self.resyncs += 1
            self._q.append(self.hub.snapshot_message(self.instrument, self.tf))
            return
        self._q.append(msg)

    def drain(self) -> list[dict]:
        out = list(self._q)
        self._q.clear()
        return out

    def request_resync(self) -> None:
        self._q.clear()
        self.resyncs += 1
        self._q.append(self.hub.snapshot_message(self.instrument, self.tf))

    def close(self) -> None:
        self.hub.unsubscribe(self)


class DeltaHub:
    def __init__(self, history: int = 600, max_queue: int = 256):
        self.history, self.max_queue = history, max_queue
        self._seq: dict = {}
        self._buf: dict = {}                                  # (inst, tf) -> deque of candle json (closed + latest partial)
        self._subs: list[Subscriber] = []

    def publish(self, candle) -> int:
        key = (candle.instrument, candle.tf_sec)
        seq = self._seq.get(key, 0) + 1
        self._seq[key] = seq
        j = candle_json(candle)
        buf = self._buf.setdefault(key, deque(maxlen=self.history))
        if buf and buf[-1]["ts"] == j["ts"]:
            buf[-1] = j                                       # same bar updated (partial -> closed)
        else:
            buf.append(j)
        msg = {"t": "candle", "i": candle.instrument, "tf": candle.tf_sec, "seq": seq, "closed": bool(candle.closed), "c": j}
        for s in self._subs:
            if s.instrument == candle.instrument and s.tf == candle.tf_sec:
                s.push(msg)
        return seq

    def snapshot_message(self, instrument: str, tf: int) -> dict:
        key = (instrument, tf)
        return {"t": "snapshot", "i": instrument, "tf": tf, "seq": self._seq.get(key, 0),
                "candles": list(self._buf.get(key, []))}

    def subscribe(self, instrument: str, tf: int) -> Subscriber:
        s = Subscriber(self, instrument, tf, self.max_queue)
        s._q.append(self.snapshot_message(instrument, tf))
        self._subs.append(s)
        return s

    def unsubscribe(self, s: Subscriber) -> None:
        if s in self._subs:
            self._subs.remove(s)

    def status(self) -> dict:
        return {"streams": len(self._buf), "subscribers": len(self._subs),
                "queued": sum(len(s._q) for s in self._subs), "resyncs": sum(s.resyncs for s in self._subs)}
