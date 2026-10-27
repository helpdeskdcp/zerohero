"""Symbol resolution. Only instruments we can both name and fetch from Upstox are exposed:
five NSE/BSE indices and NSE cash equities (EQ series) from the Upstox NSE instrument master.
Futures/options/MCX are NOT offered (contract rolls need a chaining design of their own)."""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass

from . import config

INDICES = {
    "NIFTY": ("NSE_INDEX|Nifty 50", "NSE", "Nifty 50"),
    "BANKNIFTY": ("NSE_INDEX|Nifty Bank", "NSE", "Nifty Bank"),
    "FINNIFTY": ("NSE_INDEX|Nifty Fin Service", "NSE", "Nifty Financial Services"),
    "MIDCPNIFTY": ("NSE_INDEX|NIFTY MID SELECT", "NSE", "Nifty Midcap Select"),
    "SENSEX": ("BSE_INDEX|SENSEX", "BSE", "S&P BSE Sensex"),
}
_ID = re.compile(r"^[A-Z0-9&_-]{1,30}$")
_MASTER = os.path.join(config.BACKEND, "data", "scanner", "nse_master.json.gz")
_lock = threading.Lock()
_idx_cache: dict = {"mtime": None, "eq": {}}
_last_refresh_try = 0.0


class SymbolError(ValueError):
    pass


@dataclass(frozen=True)
class Instrument:
    id: str              # NIFTY | NSE:RELIANCE
    key: str             # Upstox instrument_key
    kind: str            # INDEX | EQUITY
    exchange: str        # NSE | BSE
    name: str
    has_volume: bool     # index volume is not published by Upstox (always 0)

    def public(self) -> dict:
        return asdict(self)


def _equities() -> dict:
    """{TRADING_SYMBOL: (instrument_key, name)} from the cached master; refreshed (best effort, <=1/h) when missing/old."""
    global _last_refresh_try
    with _lock:
        stale = (not os.path.exists(_MASTER)) or (time.time() - os.path.getmtime(_MASTER) > 24 * 3600)
        if stale and time.time() - _last_refresh_try > 3600:
            _last_refresh_try = time.time()
            try:
                import requests
                r = requests.get("https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz", timeout=20)
                r.raise_for_status()
                os.makedirs(os.path.dirname(_MASTER), exist_ok=True)
                tmp = _MASTER + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(r.content)
                os.replace(tmp, _MASTER)
            except Exception:  # noqa: BLE001 - fall back to whatever file exists
                pass
        if not os.path.exists(_MASTER):
            return {}
        mt = os.path.getmtime(_MASTER)
        if _idx_cache["mtime"] != mt:
            rows = json.load(gzip.open(_MASTER))
            _idx_cache["eq"] = {r["trading_symbol"]: (r["instrument_key"], r.get("name") or r["trading_symbol"])
                                for r in rows if r.get("segment") == "NSE_EQ" and r.get("instrument_type") == "EQ"}
            _idx_cache["mtime"] = mt
        return _idx_cache["eq"]


def resolve(symbol: str) -> Instrument:
    """Validate and resolve a symbol id; raises SymbolError for anything we cannot fetch."""
    s = (symbol or "").strip().upper()
    if s.startswith("NSE:"):
        base = s[4:]
        if not _ID.match(base):
            raise SymbolError(f"invalid symbol {symbol!r}")
        eq = _equities().get(base)
        if not eq:
            raise SymbolError(f"{base} is not an NSE equity (EQ series) in the Upstox instrument master")
        return Instrument(f"NSE:{base}", eq[0], "EQUITY", "NSE", eq[1], True)
    if s in INDICES:
        key, ex, name = INDICES[s]
        return Instrument(s, key, "INDEX", ex, name, False)
    if _ID.match(s) and s in _equities():           # bare equity ticker convenience
        key, name = _equities()[s]
        return Instrument(f"NSE:{s}", key, "EQUITY", "NSE", name, True)
    raise SymbolError(f"unsupported symbol {symbol!r}: supported are {', '.join(INDICES)} and NSE:<EQ ticker>")


def search(q: str, limit: int = 25) -> list[dict]:
    q = (q or "").strip().upper()
    out = [Instrument(k, v[0], "INDEX", v[1], v[2], False).public() for k, v in INDICES.items() if not q or q in k or q in v[2].upper()]
    if q:
        eq = _equities()
        starts = [t for t in eq if t.startswith(q)]
        contains = [t for t in eq if q in t and not t.startswith(q)]
        named = [t for t in eq if q in eq[t][1].upper() and q not in t]
        for t in (sorted(starts) + sorted(contains) + sorted(named))[: max(0, limit - len(out))]:
            out.append(Instrument(f"NSE:{t}", eq[t][0], "EQUITY", "NSE", eq[t][1], True).public())
    return out[:limit]


def all_symbols(limit: int = 5000) -> list[dict]:
    """Every symbol we can serve, as chart-picker descriptors {ticker, description, type, prefix} (indices first, then NSE EQ)."""
    out = [{"ticker": k, "description": v[2], "type": "index", "prefix": v[1]} for k, v in INDICES.items()]
    for t, (_key, name) in sorted(_equities().items()):
        out.append({"ticker": t, "description": name, "type": "stock", "prefix": "NSE"})
        if len(out) >= limit:
            break
    return out
