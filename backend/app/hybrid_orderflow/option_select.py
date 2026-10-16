"""Policy-driven CE/PE + ITM/ATM/OTM contract selection. PURE function over a validated chain snapshot: no I/O, no broker, no orders.

Moneyness comes from strike vs underlying, never from delta alone. ATM = nearest LISTED strike (explicit tie-break from policy),
so irregular or missing strikes work. Anything unverifiable returns NO_TRADE with machine-readable reasons."""
from __future__ import annotations

from datetime import date


def atm_strike(listed: list[float], underlying: float, tie_break: str = "LOWER_STRIKE") -> float | None:
    if not listed:
        return None
    best = min(abs(s - underlying) for s in listed)
    ties = sorted(s for s in listed if abs(abs(s - underlying) - best) < 1e-9)
    return ties[0] if tie_break == "LOWER_STRIKE" else ties[-1]


def classify_moneyness(option_type: str, strike: float, underlying: float, atm: float) -> str:
    """CE ITM: strike below underlying; CE OTM: above. PE ITM: strike above underlying; PE OTM: below. ATM: the nearest listed strike."""
    if strike == atm:
        return "ATM"
    if option_type == "CE":
        return "ITM" if strike < underlying else "OTM"
    if option_type == "PE":
        return "ITM" if strike > underlying else "OTM"
    raise ValueError(f"option_type must be CE or PE, got {option_type!r}")


def band_strike(listed: list[float], option_type: str, band: str, underlying: float, atm: float) -> float | None:
    """The listed strike one step from ATM on the ITM/OTM side (steps follow the actual listing, not a fixed interval)."""
    if band == "ATM":
        return atm
    s = sorted(listed)
    i = s.index(atm)
    down = option_type == "CE"                      # ITM for CE is lower; OTM for CE is higher; PE mirrored
    step = -1 if ((band == "ITM") == down) else 1
    j = i + step
    return s[j] if 0 <= j < len(s) else None


def _no_trade(reasons, **extra):
    return {"status": "NO_TRADE", "reasons": list(reasons), "contract": None, **extra}


def select_contract(direction: str, underlying: float, underlying_age_sec: float, chain: list[dict], today: date, policy: dict) -> dict:
    """chain rows: option_type, strike, expiry (date or ISO str), bid, ask, volume, oi, delta (optional), quote_age_sec, lot_size, tick_size, symbol."""
    o, d = policy["options"], policy["data"]
    side = o["direction_map"].get(str(direction).upper())
    if side is None:
        return _no_trade(["CONFLICTING_EVIDENCE"], detail="direction must be BULLISH or BEARISH")
    if underlying is None or underlying <= 0 or underlying_age_sec is None or underlying_age_sec > d["max_quote_age_sec"]:
        return _no_trade(["DATA_STALE"], detail=f"underlying quote age {underlying_age_sec}")
    rows = []
    for r in chain:
        if r.get("option_type") != side:
            continue
        e = r["expiry"] if isinstance(r["expiry"], date) else date.fromisoformat(str(r["expiry"]))
        rows.append({**r, "_expiry": e})
    live = [r for r in rows if r["_expiry"] >= today and (r["_expiry"] - today).days >= o["min_dte_days"]]
    if not live:
        return _no_trade(["CONTRACT_EXPIRED" if rows else "NO_ELIGIBLE_CONTRACT"], detail="no unexpired contract for this side")
    expiry = min(r["_expiry"] for r in live)         # nearest validated expiry; the expiry list comes from the instrument master, not a weekday rule
    book = {r["strike"]: r for r in live if r["_expiry"] == expiry}
    listed = sorted(book)
    atm = atm_strike(listed, underlying, o["atm_tie_break"])
    rejected, tried = [], []
    for band in o["preference_order"]:
        k = band_strike(listed, side, band, underlying, atm)
        if k is None:
            rejected.append({"band": band, "reasons": ["NO_LISTED_STRIKE"]})
            continue
        r = book[k]
        why = []
        bid, ask = r.get("bid") or 0.0, r.get("ask") or 0.0
        if bid <= 0 or ask <= 0:
            why.append("DATA_MISSING")
        elif bid >= ask:
            why.append("CROSSED_OR_LOCKED_BOOK")
        else:
            if (ask - bid) / ((ask + bid) / 2) * 100 > o["max_spread_pct"]:
                why.append("SPREAD_TOO_WIDE")
        if (r.get("quote_age_sec") is None) or r["quote_age_sec"] > d["max_quote_age_sec"]:
            why.append("DATA_STALE")
        if (r.get("volume") or 0) < o["min_volume"] or (r.get("oi") or 0) < o["min_oi"]:
            why.append("ILLIQUID")
        dl = r.get("delta")
        lo_hi = o["delta_bands"].get(band)
        if dl is not None and lo_hi and not (lo_hi[0] <= abs(dl) <= lo_hi[1]):
            why.append("DELTA_OUT_OF_BAND")
        if not r.get("lot_size") or not r.get("tick_size") or r["tick_size"] <= 0:
            why.append("DATA_UNMAPPED")
        tried.append({"band": band, "strike": k})
        if why:
            rejected.append({"band": band, "strike": k, "reasons": why})
            continue
        return {"status": "SELECTED", "reasons": [], "contract": {k2: v for k2, v in r.items() if not k2.startswith("_")},
                "side": side, "band": band, "moneyness": classify_moneyness(side, k, underlying, atm), "atm_strike": atm,
                "expiry": expiry.isoformat(), "delta_verified": dl is not None, "rejected": rejected}
    return _no_trade(["NO_ELIGIBLE_CONTRACT"], rejected=rejected, atm_strike=atm, expiry=expiry.isoformat())
