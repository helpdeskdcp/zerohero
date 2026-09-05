"""
Volume Profile + Market Profile (TPO) — pure, deterministic math over captured
OHLCV bars. No I/O, no broker calls.

IMPORTANT — what this is and is NOT:
  * We have periodic OHLCV bars (histcap, ~5m), NOT tick-by-tick trades. A true
    volume profile needs every trade's price+size; we approximate by
    DISTRIBUTING each bar's volume across the price bins its [low, high] range
    spans. Every result carries `method: "OHLCV_RANGE_DISTRIBUTION"` and a
    `note` so the dashboard never presents it as trade-level truth.
  * TPO (Market Profile) counts, per price bin, how many time brackets touched
    that price — this one IS well-defined from bars (it only needs the bar's
    high/low + timestamp), so the TPO profile is exact, not approximate.

POC / Value Area follow the standard method: POC = the bin with the most
volume (or TPO count); the Value Area grows outward from the POC, at each step
absorbing whichever adjacent bin (one above the current top, or one below the
current bottom) holds more volume, until the cumulative reaches `value_pct`
(0.70 by convention).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

_IST = timezone(timedelta(hours=5, minutes=30))

# Per-symbol default price-bin size (the "tick" of the profile grid). Not the
# exchange tick — a sensible profile granularity. Overridable via the API.
_DEFAULT_TICK = {
    "NIFTY": 5.0, "BANKNIFTY": 10.0, "FINNIFTY": 5.0, "MIDCPNIFTY": 5.0,
    "SENSEX": 10.0, "BANKEX": 10.0, "NATURALGAS": 0.5, "CRUDEOIL": 5.0,
}


def _num(x) -> Optional[float]:
    try:
        f = float(x)
        return f if f == f and abs(f) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _tick_for(symbol: str, bars: list, override: Optional[float]) -> float:
    if override and override > 0:
        return float(override)
    t = _DEFAULT_TICK.get(str(symbol or "").upper())
    if t:
        return t
    # derive: aim for ~50-80 bins across the session range
    lo = min((_num(b.get("l")) for b in bars if _num(b.get("l")) is not None), default=None)
    hi = max((_num(b.get("h")) for b in bars if _num(b.get("h")) is not None), default=None)
    if lo is None or hi is None or hi <= lo:
        return 1.0
    raw = (hi - lo) / 60.0
    # snap to a clean-ish step
    for step in (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0):
        if step >= raw:
            return step
    return 100.0


def _bin_index(price: float, floor_price: float, tick: float) -> int:
    return int((price - floor_price) // tick)


def _bin_price(idx: int, floor_price: float, tick: float) -> float:
    # centre of the bin
    return round(floor_price + (idx + 0.5) * tick, 6)


def _value_area(rows: list, value_key: str, value_pct: float) -> tuple:
    """rows: list of {"price", <value_key>}, ascending by price. Returns
    (poc_price, vah_price, val_price). Deterministic tie-break: on equal
    adjacent values, expand DOWN first (lower price), matching common
    footprint-tool behaviour."""
    if not rows:
        return (None, None, None)
    vals = [r[value_key] or 0.0 for r in rows]
    total = sum(vals)
    poc_i = max(range(len(rows)), key=lambda i: (vals[i], -i))  # highest value, lowest index on tie
    if total <= 0:
        p = rows[poc_i]["price"]
        return (p, p, p)
    lo_i = hi_i = poc_i
    acc = vals[poc_i]
    target = total * value_pct
    while acc < target and (lo_i > 0 or hi_i < len(rows) - 1):
        up = vals[hi_i + 1] if hi_i < len(rows) - 1 else -1.0
        dn = vals[lo_i - 1] if lo_i > 0 else -1.0
        if up < 0 and dn < 0:
            break
        if dn >= up:                      # tie -> expand down
            lo_i -= 1
            acc += vals[lo_i]
        else:
            hi_i += 1
            acc += vals[hi_i]
    return (rows[poc_i]["price"], rows[hi_i]["price"], rows[lo_i]["price"])


def _grid(bars: list, symbol: str, tick_override: Optional[float]):
    clean = []
    for b in bars:
        h, l, c, v = (_num(b.get("h")), _num(b.get("l")), _num(b.get("c")), _num(b.get("v")))
        if h is None or l is None or h < l:
            continue
        clean.append({"h": h, "l": l, "c": c if c is not None else (h + l) / 2,
                      "v": v if (v is not None and v > 0) else 0.0,
                      "bar_start": b.get("bar_start")})
    if not clean:
        return None
    tick = _tick_for(symbol, clean, tick_override)
    lo = min(b["l"] for b in clean)
    hi = max(b["h"] for b in clean)
    floor_price = (int(lo / tick)) * tick
    n_bins = max(1, _bin_index(hi, floor_price, tick) + 1)
    return {"bars": clean, "tick": tick, "floor": floor_price, "n_bins": n_bins,
            "session_high": hi, "session_low": lo}


def volume_profile(bars: list, *, symbol: str = "", tick_size: Optional[float] = None,
                   value_pct: float = 0.70) -> dict:
    """Volume distributed across each bar's [low, high] range (uniform split)."""
    g = _grid(bars, symbol, tick_size)
    if g is None:
        return {"status": "NO_DATA", "bins": [], "method": "OHLCV_RANGE_DISTRIBUTION"}
    tick, floor_price, n_bins = g["tick"], g["floor"], g["n_bins"]
    vol = [0.0] * n_bins
    for b in g["bars"]:
        i0 = _bin_index(b["l"], floor_price, tick)
        i1 = _bin_index(b["h"], floor_price, tick)
        i0, i1 = max(0, min(i0, i1)), min(n_bins - 1, max(i0, i1))
        span = i1 - i0 + 1
        share = b["v"] / span
        for i in range(i0, i1 + 1):
            vol[i] += share
    rows = [{"price": _bin_price(i, floor_price, tick), "volume": round(vol[i], 4)}
            for i in range(n_bins)]
    poc, vah, val = _value_area(rows, "volume", value_pct)
    total = sum(vol)
    return {
        "status": "OK",
        "method": "OHLCV_RANGE_DISTRIBUTION",
        "note": ("volume is spread evenly across each ~5m bar's high-low range; "
                 "this is an OHLCV approximation, NOT trade-level volume-at-price"),
        "tick_size": tick, "n_bins": n_bins,
        "session_high": g["session_high"], "session_low": g["session_low"],
        "total_volume": round(total, 2),
        "poc": poc, "vah": vah, "val": val, "value_pct": value_pct,
        "bins": rows,
    }


def market_profile(bars: list, *, symbol: str = "", tick_size: Optional[float] = None,
                   tpo_minutes: int = 30, value_pct: float = 0.70) -> dict:
    """TPO count per price bin: for each `tpo_minutes` bracket, +1 for every
    price bin the bracket's combined [low, high] range touched. Exact from bars
    (needs only high/low + time), not an approximation."""
    g = _grid(bars, symbol, tick_size)
    if g is None:
        return {"status": "NO_DATA", "bins": [], "tpo_minutes": tpo_minutes}
    tick, floor_price, n_bins = g["tick"], g["floor"], g["n_bins"]

    def _bracket(bs) -> int:
        try:
            dt = datetime.fromisoformat(str(bs).replace("Z", "+00:00")).astimezone(_IST)
        except Exception:
            return 0
        return (dt.hour * 60 + dt.minute) // max(1, tpo_minutes)

    brackets: dict = {}
    for b in g["bars"]:
        brackets.setdefault(_bracket(b["bar_start"]), []).append(b)

    tpo = [0] * n_bins
    letters = [""] * n_bins
    for bi, (brk, bs) in enumerate(sorted(brackets.items())):
        lo = min(x["l"] for x in bs)
        hi = max(x["h"] for x in bs)
        i0 = max(0, _bin_index(lo, floor_price, tick))
        i1 = min(n_bins - 1, _bin_index(hi, floor_price, tick))
        ch = chr(ord("A") + bi) if bi < 26 else chr(ord("a") + bi - 26) if bi < 52 else "+"
        for i in range(i0, i1 + 1):
            tpo[i] += 1
            letters[i] += ch
    rows = [{"price": _bin_price(i, floor_price, tick), "tpo": tpo[i], "letters": letters[i]}
            for i in range(n_bins)]
    single_prints = [r["price"] for r in rows if r["tpo"] == 1]
    poc, vah, val = _value_area([{"price": r["price"], "tpo": r["tpo"]} for r in rows],
                                "tpo", value_pct)
    return {
        "status": "OK",
        "tick_size": tick, "n_bins": n_bins, "tpo_minutes": tpo_minutes,
        "n_brackets": len(brackets),
        "session_high": g["session_high"], "session_low": g["session_low"],
        "poc": poc, "vah": vah, "val": val, "value_pct": value_pct,
        "single_prints": single_prints,
        "bins": rows,
    }
