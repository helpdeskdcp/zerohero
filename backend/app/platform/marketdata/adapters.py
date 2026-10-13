"""Provider packet -> Tick adapters and an explicit instrument map (no guessed mappings).

Native keys: Dhan = (exchange_segment_code, security_id), Angel = (exchange_type_code, token). The
canonical key (e.g. "NSE:NIFTY-IDX") is registered explicitly; an unmapped native instrument yields
None (the tick is dropped and counted by the caller) -- a wrong mapping would silently corrupt candles.
"""
from __future__ import annotations

from .types import Tick


class InstrumentMap:
    def __init__(self):
        self._to_canon: dict = {}
        self._to_native: dict = {}

    def register(self, canonical: str, provider: str, native_key: tuple) -> None:
        k = (provider, tuple(str(x) for x in native_key))
        if k in self._to_canon and self._to_canon[k] != canonical:
            raise ValueError(f"{k} already mapped to {self._to_canon[k]!r}")
        self._to_canon[k] = canonical
        self._to_native[(canonical, provider)] = tuple(str(x) for x in native_key)

    def canonical(self, provider: str, native_key: tuple) -> str | None:
        return self._to_canon.get((provider, tuple(str(x) for x in native_key)))

    def native(self, canonical: str, provider: str):
        return self._to_native.get((canonical, provider))

    def canonicals(self) -> list[str]:
        return sorted({c for c in self._to_canon.values()})


def tick_from_dhan_packet(p: dict, recv_ts: float, imap: InstrumentMap) -> Tick | None:
    if p.get("type") != "FULL":
        return None
    key = imap.canonical("DHAN", (p["exchange_segment"], p["security_id"]))
    if key is None:
        return None
    ltt = p.get("ltt")
    return Tick("DHAN", key, float(p["ltp"]), float(ltt) if ltt else None, recv_ts, qty=p.get("ltq"),
                cum_volume=p.get("volume"), oi=p.get("oi"))


def tick_from_angel_tick(t: dict, recv_ts: float, imap: InstrumentMap) -> Tick | None:
    key = imap.canonical("ANGELONE", (t["exchange_type"], t["token"]))
    if key is None:
        return None
    ts_ms = t.get("ts_ms")
    # Angel LTP mode (mode 1) carries price and a timestamp only: no volume / OI / quantity.
    return Tick("ANGELONE", key, float(t["ltp"]), (ts_ms / 1000.0) if ts_ms else None, recv_ts)
