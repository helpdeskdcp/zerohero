"""
Pure derivation math for the Greeks Engine.

Every function takes already-validated broker inputs and returns derived numbers
only. A missing Greek or a missing OI removes that (strike, side) pair from the
aggregate — it is NEVER replaced with an estimate. No Black-Scholes here.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .model import GREEKS, Quality


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def pair_exposure(oi, delta, gamma, theta, vega) -> dict | None:
    """OI × Greek for one (strike, side). Returns None if OI is missing or no
    Greek is present (nothing to derive — do not fabricate)."""
    oi = _f(oi)
    g = {k: _f(v) for k, v in (("delta", delta), ("gamma", gamma),
                               ("theta", theta), ("vega", vega))}
    if oi is None or all(v is None for v in g.values()):
        return None
    return {"oi": oi, **{f"{k}_exp": (oi * v if v is not None else None) for k, v in g.items()},
            **{f"{k}": v for k, v in g.items()}}


def build_snapshot(rows: list[dict], *, underlying: str, expiry: str,
                   underlying_price: float | None, underlying_price_src: str | None,
                   as_of_ts: str, expected_pairs: int, stale_sec_threshold: float,
                   now: datetime | None = None) -> dict:
    """rows: [{strike, option_type, oi, delta, gamma, theta, vega, iv}] — one per
    (strike, side) that has a broker Greek (broker_status OK). `oi` may be None.

    Returns one `greek_exposure` record dict (see model.EXPOSURE_COLS) plus a
    `per_strike` list. Quality reflects freshness + coverage; a low-coverage or
    stale result is still returned (append-only, auditable), just flagged.
    """
    now = now or datetime.now(timezone.utc)
    try:
        aged = (now - datetime.fromisoformat(as_of_ts.replace("Z", "+00:00"))).total_seconds()
    except (ValueError, AttributeError):
        aged = None

    by_strike: dict[float, dict] = {}
    used = 0
    for r in rows:
        k = _f(r.get("strike"))
        side = str(r.get("option_type") or "").upper()
        if k is None or side not in ("CE", "PE"):
            continue
        pe = pair_exposure(r.get("oi"), r.get("delta"), r.get("gamma"),
                           r.get("theta"), r.get("vega"))
        slot = by_strike.setdefault(k, {"strike": k, "ce": None, "pe": None})
        if pe is None:
            continue
        pe["iv"] = _f(r.get("iv"))
        slot["ce" if side == "CE" else "pe"] = pe
        used += 1

    def side_sum(side: str, field: str) -> float | None:
        vals = [s[side][field] for s in by_strike.values()
                if s[side] and s[side].get(field) is not None]
        return round(sum(vals), 6) if vals else None

    out: dict = {
        "computed_ts": now.isoformat().replace("+00:00", "Z"),
        "as_of_ts": as_of_ts, "underlying": underlying.upper(), "expiry": str(expiry).upper(),
        "session_date_ist": _ist_date(as_of_ts),
        "underlying_price": _f(underlying_price), "underlying_price_src": underlying_price_src,
        "n_pairs_used": used, "n_pairs_expected": int(expected_pairs),
        "n_pairs_missing": max(0, int(expected_pairs) - used),
        "coverage_pct": round(100.0 * used / expected_pairs, 2) if expected_pairs else None,
        "stale_sec": round(aged, 1) if aged is not None else None,
        "source": "DERIVED_FROM_ANGELONE_OPTION_GREEK",
    }

    if used == 0:
        out["quality"] = Quality.NO_DATA.value
        for c in ("ce_oi_total", "pe_oi_total", "pcr_oi", "oi_weighted_iv", "vega_weighted_iv",
                  "gamma_conc_strike", "gamma_conc_pct", "gamma_herfindahl"):
            out[c] = None
        for g in GREEKS:
            for p in ("ce", "pe", "net", "diff"):
                out[f"{p}_{g}_exp"] = None
        out["per_strike"] = []
        return out

    for g in GREEKS:
        ce = side_sum("ce", f"{g}_exp")
        pe = side_sum("pe", f"{g}_exp")
        out[f"ce_{g}_exp"] = ce
        out[f"pe_{g}_exp"] = pe
        out[f"net_{g}_exp"] = _round_add(ce, pe)                 # signed sum (PE deltas are < 0)
        out[f"diff_{g}_exp"] = _round_sub(ce, pe)                # CE − PE magnitude difference

    out["ce_oi_total"] = side_sum("ce", "oi")
    out["pe_oi_total"] = side_sum("pe", "oi")
    out["pcr_oi"] = (round(out["pe_oi_total"] / out["ce_oi_total"], 4)
                     if out["ce_oi_total"] else None)

    # OI-weighted IV and Vega-weighted IV over every valid (strike, side)
    num_oi = den_oi = num_v = den_v = 0.0
    for s in by_strike.values():
        for leg in (s["ce"], s["pe"]):
            if not leg or leg.get("iv") is None or leg.get("oi") is None:
                continue
            iv, oi = leg["iv"], leg["oi"]
            num_oi += iv * oi
            den_oi += oi
            v = leg.get("vega")
            if v is not None:
                num_v += iv * abs(v) * oi
                den_v += abs(v) * oi
    out["oi_weighted_iv"] = round(num_oi / den_oi, 6) if den_oi > 0 else None
    out["vega_weighted_iv"] = round(num_v / den_v, 6) if den_v > 0 else None

    # Greek-weighted OI concentration — per strike |gamma exposure| (CE+PE)
    gk = {}
    for k, s in by_strike.items():
        m = 0.0
        for leg in (s["ce"], s["pe"]):
            if leg and leg.get("gamma_exp") is not None:
                m += abs(leg["gamma_exp"])
        if m > 0:
            gk[k] = m
    tot = sum(gk.values())
    if tot > 0:
        top = max(gk, key=gk.get)
        out["gamma_conc_strike"] = top
        out["gamma_conc_pct"] = round(100.0 * gk[top] / tot, 3)
        out["gamma_herfindahl"] = round(sum((v / tot) ** 2 for v in gk.values()), 6)
    else:
        out["gamma_conc_strike"] = out["gamma_conc_pct"] = out["gamma_herfindahl"] = None

    # quality
    cov = out["coverage_pct"] or 0.0
    if aged is not None and aged > stale_sec_threshold:
        out["quality"] = Quality.STALE.value
    elif cov < 80.0:
        out["quality"] = Quality.PARTIAL.value
    elif any(out.get(f"net_{g}_exp") is not None and not math.isfinite(out[f"net_{g}_exp"])
             for g in GREEKS):
        out["quality"] = Quality.INVALID.value
    else:
        out["quality"] = Quality.VALID.value

    out["per_strike"] = [by_strike[k] for k in sorted(by_strike)]
    return out


def _round_add(a, b):
    if a is None and b is None:
        return None
    return round((a or 0.0) + (b or 0.0), 6)


def _round_sub(a, b):
    if a is None and b is None:
        return None
    return round((a or 0.0) - (b or 0.0), 6)


def _ist_date(utc_iso: str) -> str:
    from datetime import timedelta
    try:
        dt = datetime.fromisoformat((utc_iso or "").replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc)
    return (dt.astimezone(timezone(timedelta(hours=5, minutes=30)))).date().isoformat()
