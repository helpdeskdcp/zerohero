"""Futures + option-chain confirmation from adapter capabilities. Returns a bias in [-1, +1] plus the facts used; unavailable/invalid data -> available=False
(never fabricated). OI is context, not direction: OI change is only read together with the price change, and never alone."""
from __future__ import annotations

import os

MAX_AGE = float(os.environ.get("NIFTY_DIR_MAX_DERIV_AGE_SEC", "60"))


def futures_view(fut: dict | None, now: float, spot: float | None = None, expected_underlying: str = "NIFTY") -> dict:
    if not fut: return {"available": False, "reason": "NO_FUTURES"}
    need = ("symbol", "expiry", "ltp", "prev_close", "oi", "prev_oi", "ts")
    miss = [k for k in need if fut.get(k) is None]
    if miss: return {"available": False, "reason": "INCOMPLETE", "missing": miss}
    if not str(fut["symbol"]).upper().startswith(expected_underlying): return {"available": False, "reason": "WRONG_INSTRUMENT", "symbol": fut["symbol"]}
    if now - fut["ts"] > MAX_AGE: return {"available": False, "reason": "STALE"}
    if fut["ltp"] <= 0 or fut["prev_close"] <= 0: return {"available": False, "reason": "BAD_PRICE"}
    pchg = fut["ltp"] / fut["prev_close"] - 1; oichg = (fut["oi"] / fut["prev_oi"] - 1) if fut["prev_oi"] else None
    basis = (fut["ltp"] - spot) if spot else None
    # price up + OI up = long build-up, price up + OI down = short covering, price down + OI up = short build-up, price down + OI down = long unwinding
    if oichg is None: act = "UNKNOWN"
    else: act = ("LONG_BUILDUP" if pchg > 0 else "SHORT_BUILDUP") if oichg > 0 else ("SHORT_COVERING" if pchg > 0 else "LONG_UNWINDING")
    bias = 0.0 if abs(pchg) < 0.0005 else (1.0 if pchg > 0 else -1.0) * (1.0 if act in ("LONG_BUILDUP", "SHORT_BUILDUP") else 0.5)
    return {"available": True, "price_chg_pct": pchg * 100, "oi_chg_pct": None if oichg is None else oichg * 100, "basis": basis, "volume": fut.get("volume"), "activity": act, "bias": bias, "expiry": fut["expiry"]}


def chain_view(chain: dict | None, now: float, spot: float | None = None, expected_underlying: str = "NIFTY", min_rows: int = 6) -> dict:
    if not chain: return {"available": False, "reason": "NO_CHAIN"}
    if str(chain.get("underlying", "")).upper() != expected_underlying: return {"available": False, "reason": "WRONG_UNDERLYING"}
    if not chain.get("expiry") or chain.get("ts") is None: return {"available": False, "reason": "INCOMPLETE", "missing": [k for k in ("expiry", "ts") if not chain.get(k) and chain.get(k) != 0]}
    if now - chain["ts"] > MAX_AGE: return {"available": False, "reason": "STALE"}
    rows = [r for r in chain.get("rows", []) if r.get("strike") is not None and r.get("ce_oi") is not None and r.get("pe_oi") is not None]
    if len(rows) < min_rows: return {"available": False, "reason": "TOO_FEW_STRIKES", "have": len(rows)}
    ce, pe = sum(r["ce_oi"] for r in rows), sum(r["pe_oi"] for r in rows)
    if ce <= 0: return {"available": False, "reason": "ZERO_CALL_OI"}
    pcr = pe / ce
    top_pe = max(rows, key=lambda r: r["pe_oi"])["strike"]; top_ce = max(rows, key=lambda r: r["ce_oi"])["strike"]
    ivs = [r["ce_iv"] for r in rows if r.get("ce_iv")] + [r["pe_iv"] for r in rows if r.get("pe_iv")]
    out = {"available": True, "pcr": pcr, "support_strike": top_pe, "resistance_strike": top_ce, "iv_mean": sum(ivs) / len(ivs) if ivs else None, "expiry": chain["expiry"],
           "greeks": any(r.get("ce_delta") is not None for r in rows), "strikes": len(rows)}
    # PCR is a weak, contrarian-ish context signal: only the extremes are read, and with the spot's position vs the OI walls.
    bias = 0.0
    if pcr > 1.3: bias += 0.5
    elif pcr < 0.7: bias -= 0.5
    if spot:
        if spot > top_ce: bias += 0.5           # spot above the heaviest call wall
        elif spot < top_pe: bias -= 0.5
    out["bias"] = max(-1.0, min(1.0, bias)); return out


def confirm(fut: dict | None, chain: dict | None, now: float, spot: float | None = None) -> dict:
    f, c = futures_view(fut, now, spot), chain_view(chain, now, spot)
    parts = [v["bias"] for v in (f, c) if v.get("available")]
    return {"futures": f, "chain": c, "bias": (sum(parts) / len(parts)) if parts else None, "available": bool(parts)}
