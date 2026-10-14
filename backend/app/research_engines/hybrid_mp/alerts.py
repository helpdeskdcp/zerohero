"""Alert mapping + idempotent de-duplication for engine events (replaying the same candle must not alert twice).

Pine alert conditions: BUY/SELL Confirmed (buySignal/sellSignal - they also fire when candidateR < mintick and no trade is created: flagged
`no_trade`), T1..T4 Reached, Stop Loss Hit, Reversal Confirmed (= a CHoCH on a confirmed bar). Alert ids are deterministic:
sha1(symbol | tf | bar_open_epoch | name | direction)."""
from __future__ import annotations

import hashlib

NAMES = {"T1": "T1 Reached", "T2": "T2 Reached", "T3": "T3 Reached", "T4": "T4 Reached", "SL_HIT": "Stop Loss Hit", "CHOCH": "Reversal Confirmed"}


def events_to_alerts(events: list, bar_times, symbol: str, tf: int) -> list[dict]:
    out = []
    for e in events:
        i, kind = e[0], e[1]
        if kind in ("SIGNAL", "SIGNAL_NO_TRADE"):
            d = e[2]
            name = "BUY Confirmed" if d == 1 else "SELL Confirmed"
            extra = dict(no_trade=(kind == "SIGNAL_NO_TRADE"))
            if kind == "SIGNAL":
                extra.update(entry_type=e[3], sl=float(e[4]), risk=float(e[5]))
        elif kind in NAMES:
            d = e[2] if len(e) > 2 else 0
            name, extra = NAMES[kind], {}
        else:
            continue
        t = int(bar_times[i])
        aid = hashlib.sha1(f"{symbol}|{tf}|{t}|{name}|{d}".encode()).hexdigest()[:20]
        out.append(dict(id=aid, name=name, symbol=symbol, tf=tf, bar_open=t, bar_index=int(i), direction=int(d), **extra))
    return out


class AlertDeduper:
    """Remembers delivered alert ids (bounded); `fresh()` returns only alerts never seen before. Safe to call repeatedly on overlapping windows."""
    def __init__(self, max_keep: int = 50_000):
        self.seen: dict = {}
        self.max_keep = max_keep

    def fresh(self, alerts: list[dict]) -> list[dict]:
        new = []
        for a in alerts:
            if a["id"] not in self.seen:
                self.seen[a["id"]] = a["bar_open"]
                new.append(a)
        if len(self.seen) > self.max_keep:
            for k in sorted(self.seen, key=self.seen.get)[: len(self.seen) - self.max_keep]:
                del self.seen[k]
        return new
