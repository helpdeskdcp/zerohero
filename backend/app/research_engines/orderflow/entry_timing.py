"""
Question 3 of the four:  WHEN IS THE OPTIMAL ENTRY?

The MATHEMATICAL OPTIMAL ENTRY ENGINE. A confirmed signal is NEVER an entry.
Two-stage state machine per signal:

  SIGNAL_DETECTED
    -> WAITING_FOR_RETEST      (price must pull back into the calibrated zone)
       -> ENTRY_READY          (zone tagged + momentum recovery + no-chase + RR ok)
       -> EXPIRED_TIMEOUT      (max_wait_bars passed, no fill)
       -> EXPIRED_INVALIDATED  (price broke the signal-candle origin first)
       -> EXPIRED_NOCHASE      (price ran too far from vwap/baseline/origin, never retraced)

For a LONG signal candle N:  signal_high=H, signal_low=L, range=H-L, origin=L.
  entry_zone_price(z) = H - z*range          (z=0 -> chase the high; z large -> deep pullback)
The engine fills a limit at entry_zone_price(z*) when a later bar's low reaches it.
SHORT is the exact mirror.

Also computes the ENTRY_QUALITY_SCORE (0-100) at ENTRY_READY.
"""
from __future__ import annotations

from .config import merged


def _mod(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _nochase_ok(px, row, origin, atr, c) -> tuple[bool, str]:
    if atr is None or atr <= 0:
        return True, ""
    if abs(px - origin) > c["max_entry_extension_atr"] * atr:
        return False, "EXT_ORIGIN"
    if row["vwap"] is not None and abs(px - row["vwap"]) > c["nochase_vwap_atr"] * atr:
        return False, "EXT_VWAP"
    if row["ema_slow"] is not None and abs(px - row["ema_slow"]) > c["nochase_baseline_atr"] * atr:
        return False, "EXT_BASELINE"
    if row["ema_fast"] is not None and abs(px - row["ema_fast"]) > c["nochase_fast_atr"] * atr:
        return False, "EXT_FAST"
    return True, ""


def _entry_quality(*, rk_at_signal, retest_overshoot_frac, whipsaws, recovery_strength,
                   flow_confirm, structure_abs, vwap_dev_abs_atr, regime_trending, rr, c) -> float:
    w = c["eq_weights"]
    s = {
        "signal_quality": max(0.0, min(1.0, rk_at_signal / 100.0)),
        "retest_quality": max(0.0, 1.0 - retest_overshoot_frac - 0.15 * whipsaws),
        "momentum_recovery": max(0.0, min(1.0, recovery_strength)),
        "orderflow_confirm": max(0.0, min(1.0, flow_confirm)),
        "structure": max(0.0, min(1.0, structure_abs)),
        "vwap_location": max(0.0, 1.0 - min(1.0, vwap_dev_abs_atr / max(1e-9, c["nochase_vwap_atr"]))),
        "volatility_fit": 1.0 if regime_trending else 0.4,
        "rr": max(0.0, min(1.0, (rr - c["min_rr"]) / max(1e-9, 3.0 - c["min_rr"]))),
    }
    return round(sum(w[k] * s[k] for k in w), 1)


def run(bars: list[dict], frame: list[dict], sig_i: int, direction: str,
        rk_at_signal: float, cfg: dict, *, zone_frac: float | None = None) -> dict:
    """One signal -> entry-timing outcome + the full lifecycle record."""
    c = cfg
    z = c["entry_zone_frac"] if zone_frac is None else zone_frac
    tol = c["entry_zone_tol"]
    sig = bars[sig_i]
    long = direction == "LONG"
    frow = frame[sig_i]
    atr0 = frow["atr"]
    signal_origin = sig["c"]                      # where the signal fired (no-chase ref)
    lb = c.get("entry_lookback_bars", 20)

    # SIGNAL LEG = last confirmed opposing pivot -> the signal bar. A single 5m
    # candle range is too small to define retracement zones; the *leg* is not.
    if long:
        piv = frow.get("last_lo_piv")
        p0 = piv[1] if (piv and piv[1] <= sig_i) else max(0, sig_i - lb)
        L = piv[0] if (piv and piv[1] <= sig_i) else min(b["l"] for b in bars[p0:sig_i + 1])
        H = max(b["h"] for b in bars[p0:sig_i + 1])
    else:
        piv = frow.get("last_hi_piv")
        p0 = piv[1] if (piv and piv[1] <= sig_i) else max(0, sig_i - lb)
        H = piv[0] if (piv and piv[1] <= sig_i) else max(b["h"] for b in bars[p0:sig_i + 1])
        L = min(b["l"] for b in bars[p0:sig_i + 1])
    rng = H - L
    origin = L if long else H                     # leg origin (invalidation ref)
    if rng <= c["eps"] or (atr0 and rng < 0.6 * atr0):
        return {"status": "EXPIRED_INVALIDATED", "reason": "LEG_TOO_SMALL",
                "signal_index": sig_i, "signal_high": H, "signal_low": L, "signal_range": rng}

    zone_px = (H - z * rng) if long else (L + z * rng)
    zone_lo = zone_px - tol * rng
    zone_hi = zone_px + tol * rng

    # walk forward
    fill_i = None
    fill_px = None
    protective = L if long else H          # extreme of the pullback
    overshoot = 0.0
    whipsaws = 0
    end_min = _mod(c["session_end_ist"])
    last = min(len(bars) - 1, sig_i + c["entry_max_wait_bars"])
    for j in range(sig_i + 1, last + 1):
        b = bars[j]
        if b["minute_of_day"] >= end_min:
            return {"status": "EXPIRED_TIMEOUT", "reason": "SESSION_END",
                    "signal_index": sig_i, "signal_high": H, "signal_low": L,
                    "signal_range": rng, "zone_frac": z}
        # invalidation: origin broken before a fill
        if c["invalidate_on_origin_break"]:
            if long and b["l"] < origin:
                return {"status": "EXPIRED_INVALIDATED", "reason": "ORIGIN_BREAK",
                        "signal_index": sig_i, "signal_high": H, "signal_low": L,
                        "signal_range": rng, "zone_frac": z, "bars_waited": j - sig_i}
            if (not long) and b["h"] > origin:
                return {"status": "EXPIRED_INVALIDATED", "reason": "ORIGIN_BREAK",
                        "signal_index": sig_i, "signal_high": H, "signal_low": L,
                        "signal_range": rng, "zone_frac": z, "bars_waited": j - sig_i}
        # track pullback extreme
        if long:
            protective = min(protective, b["l"])
        else:
            protective = max(protective, b["h"])
        # zone reached? (phase A -- pullback into the zone)
        touched = (b["l"] <= zone_hi) if long else (b["h"] >= zone_hi)
        reached = (b["l"] <= zone_px) if long else (b["h"] >= zone_px)
        if touched and not reached:
            whipsaws += 1
        if reached:
            reach_j = j
            reach_ext = b["l"] if long else b["h"]
            overshoot = max(0.0, (zone_px - reach_ext) / rng) if long else max(0.0, (reach_ext - zone_px) / rng)
            # phase B: wait for a MOMENTUM-RECOVERY bar within recovery_window
            rw = c.get("entry_recovery_window", 3)
            recovery = 0.0
            for k in range(reach_j, min(len(bars), reach_j + rw + 1)):
                rb = bars[k]
                if long and rb["l"] < origin:
                    return {"status": "EXPIRED_INVALIDATED", "reason": "ORIGIN_BREAK",
                            "signal_index": sig_i, "signal_high": H, "signal_low": L,
                            "signal_range": rng, "zone_frac": z}
                if (not long) and rb["h"] > origin:
                    return {"status": "EXPIRED_INVALIDATED", "reason": "ORIGIN_BREAK",
                            "signal_index": sig_i, "signal_high": H, "signal_low": L,
                            "signal_range": rng, "zone_frac": z}
                rb_rng = max(1e-9, rb["h"] - rb["l"])
                rec = ((rb["c"] - rb["l"]) if long else (rb["h"] - rb["c"])) / rb_rng
                dir_ok = (rb["c"] > rb["o"]) if long else (rb["c"] < rb["o"])
                back = (rb["c"] >= zone_px) if long else (rb["c"] <= zone_px)
                if (not c["entry_require_momentum_recovery"]) or (dir_ok and back and rec >= 0.5):
                    fill_i = k
                    fill_px = rb["c"] if c["entry_require_momentum_recovery"] else zone_px
                    recovery = rec
                    break
                protective = min(protective, rb["l"]) if long else max(protective, rb["h"])
            if fill_i is None:
                return {"status": "EXPIRED_TIMEOUT", "reason": "NO_MOMENTUM_RECOVERY",
                        "signal_index": sig_i, "signal_high": H, "signal_low": L,
                        "signal_range": rng, "zone_frac": z, "fill_index": reach_j}
            break

    if fill_i is None:
        ran = False
        for j in range(sig_i + 1, last + 1):
            b = bars[j]
            far = (b["h"] - signal_origin) if long else (signal_origin - b["l"])
            if atr0 and far > c["max_entry_extension_atr"] * atr0:
                ran = True
                break
        return {"status": "EXPIRED_NOCHASE" if ran else "EXPIRED_TIMEOUT",
                "reason": "RAN_AWAY" if ran else "NO_RETEST",
                "signal_index": sig_i, "signal_high": H, "signal_low": L,
                "signal_range": rng, "zone_frac": z}

    frow_fill = frame[fill_i]
    atr = frow_fill["atr"] or atr0 or (rng * 0.5)

    # no-chase at the fill price
    ok, why = _nochase_ok(fill_px, frow_fill, signal_origin, atr, c)
    if not ok:
        return {"status": "EXPIRED_NOCHASE", "reason": why, "signal_index": sig_i,
                "signal_high": H, "signal_low": L, "signal_range": rng, "zone_frac": z,
                "fill_index": fill_i}

    # risk : protective pullback extreme, but capped so a deep pullback doesn't
    # give an absurd stop; floored so it is not absurdly tight.
    sl_min = c.get("sl_min_atr", 0.4) * atr
    sl_cap = c.get("max_risk_atr", 1.5) * atr
    buf = c["sl_buffer_atr"] * atr
    if long:
        sl = max(protective - buf, fill_px - sl_cap - buf)
        sl = min(sl, fill_px - sl_min)
        risk = fill_px - sl
        # targets = measured-move Fib extensions of the leg from the leg high
        t1, t2, t3 = H + 0.30 * rng, H + 0.80 * rng, H + 1.50 * rng
        reward_t1 = t1 - fill_px
    else:
        sl = min(protective + buf, fill_px + sl_cap + buf)
        sl = max(sl, fill_px + sl_min)
        risk = sl - fill_px
        t1, t2, t3 = L - 0.30 * rng, L - 0.80 * rng, L - 1.50 * rng
        reward_t1 = fill_px - t1
    if risk <= c["eps"]:
        return {"status": "EXPIRED_INVALIDATED", "reason": "ZERO_RISK",
                "signal_index": sig_i, "zone_frac": z}
    rr = reward_t1 / risk
    if rr < c["min_rr"]:
        return {"status": "EXPIRED_NOCHASE", "reason": "RR_TOO_LOW",
                "signal_index": sig_i, "signal_high": H, "signal_low": L,
                "signal_range": rng, "zone_frac": z, "fill_index": fill_i, "rr": round(rr, 3)}

    flow_confirm = frow_fill["flow_proxy"] * (1.0 if long else -1.0)
    flow_confirm = 0.5 + 0.5 * max(-1.0, min(1.0, flow_confirm))       # -> [0,1]
    struct_abs = abs({"HH_HL": 0.8, "BOS_UP": 1.0, "LH_LL": 0.8, "BOS_DN": 1.0}
                     .get(frow_fill["structure_state"], 0.3))
    dev_abs = abs(frow_fill["vwap_dev_atr"] or 0.0)
    eq = _entry_quality(rk_at_signal=rk_at_signal, retest_overshoot_frac=overshoot,
                        whipsaws=whipsaws, recovery_strength=recovery,
                        flow_confirm=flow_confirm, structure_abs=struct_abs,
                        vwap_dev_abs_atr=dev_abs,
                        regime_trending=frow_fill["regime_trend"] == "TRENDING",
                        rr=rr, c=c)

    return {
        "status": "ENTRY_READY",
        "direction": direction,
        "signal_index": sig_i, "fill_index": fill_i,
        "signal_high": round(H, 2), "signal_low": round(L, 2), "signal_range": round(rng, 2),
        "zone_frac": z, "zone_price": round(zone_px, 2),
        "entry": round(fill_px, 2), "stop_loss": round(sl, 2), "risk_points": round(risk, 3),
        "t1": round(t1, 2), "t2": round(t2, 2), "t3": round(t3, 2),
        "rr": round(rr, 3), "entry_quality": eq,
        "bars_waited": fill_i - sig_i, "retest_overshoot_frac": round(overshoot, 3),
        "whipsaws": whipsaws, "momentum_recovery": round(recovery, 3),
        "session_date": bars[fill_i]["session_date"], "entry_hhmm": bars[fill_i]["hhmm"],
        "regime_trend": frow_fill["regime_trend"], "regime_vol": frow_fill["regime_vol"],
        "structure_state": frow_fill["structure_state"],
        "vwap_dev_atr": round(frow_fill["vwap_dev_atr"], 3) if frow_fill["vwap_dev_atr"] is not None else None,
    }
