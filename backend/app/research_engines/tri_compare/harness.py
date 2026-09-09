"""
COMMON backtest harness. Every engine's ENTRY_READY signals go through the
IDENTICAL: slippage, transaction cost, session rules, ATR-based risk model,
mathematical RR gate (room-to-barrier), scale-out / trailing exit and
one-position-at-a-time replay. Engines differ ONLY in when + which direction.
"""
from __future__ import annotations

from ..orderflow import exit_engine as EX


def _mod(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def _room_rr(frame, fill_i, direction, entry, risk, cfg) -> tuple[float, float | None]:
    """(rr, barrier). reward = distance to the nearest opposing structural
    barrier in the trade direction. 'open road' -> capped at 4R."""
    r = frame[fill_i]
    cands = []
    for piv in (r.get("last_hi_piv"), r.get("last_lo_piv")):
        if piv:
            cands.append(piv[0])
    va = r.get("value_area")
    if va:
        cands += [va["vah"], va["val"], va["poc"]]
    # the just-broken Donchian extreme is NOT a barrier in the breakout direction;
    # only the opposite extreme matters as a reversal target reference.
    if direction == "LONG" and r.get("don_lo") is not None:
        cands.append(r["don_lo"])
    if direction == "SHORT" and r.get("don_hi") is not None:
        cands.append(r["don_hi"])
    if direction == "LONG":
        ups = [p for p in cands if p and p > entry + 0.1 * risk]
        barrier = min(ups) if ups else entry + 4.0 * risk
        reward = barrier - entry
    else:
        dns = [p for p in cands if p and p < entry - 0.1 * risk]
        barrier = max(dns) if dns else entry - 4.0 * risk
        reward = entry - barrier
    return (reward / risk if risk > 0 else 0.0), round(barrier, 2)


def _exit_cfg(cfg: dict) -> dict:
    return {
        "scale_out": cfg["scale_out"],
        "move_sl_to_be_after_r": cfg["move_sl_to_be_after_r"],
        "runner_cap_r": cfg["runner_cap_r"],
        "session_end_ist": cfg["hard_exit_ist"],
        "cost_points": cfg["cost_points"],
    }


def run_engine(name: str, signals: list[dict], bars: list[dict], frame: list[dict],
               cfg: dict) -> dict:
    """-> {trades: [...], rejected: {reason: n}, signals_total, entries_total}."""
    exc = _exit_cfg(cfg)
    slip = cfg["slippage_points"]
    tmults = cfg["target_r_multiples"]
    no_after = _mod(cfg["no_new_entry_after_ist"])
    rej: dict = {}
    trades: list[dict] = []
    last_exit_i = -1
    n_ready = 0

    for s in signals:
        if s.get("status") not in (None, "ENTRY_READY") or s.get("fill_index") is None:
            rej[s.get("status") or "NO_FILL"] = rej.get(s.get("status") or "NO_FILL", 0) + 1
            continue
        n_ready += 1
        fi = s["fill_index"]
        b = bars[fi]
        if b["minute_of_day"] > no_after:
            rej["AFTER_CUTOFF"] = rej.get("AFTER_CUTOFF", 0) + 1
            continue
        if cfg["one_position_at_a_time"] and fi <= last_exit_i:
            rej["POSITION_OPEN"] = rej.get("POSITION_OPEN", 0) + 1
            continue
        long = s["direction"] == "LONG"
        sgn = 1 if long else -1
        atr = frame[fi].get("atr") or 0.0
        if atr <= 0:
            rej["NO_ATR"] = rej.get("NO_ATR", 0) + 1
            continue
        entry = s["entry_ref"] + sgn * slip            # slippage adverse on entry
        risk = atr * cfg["sl_atr_mult"]
        sl = entry - sgn * risk
        t1, t2, t3 = (entry + sgn * tmults[0] * risk,
                      entry + sgn * tmults[1] * risk,
                      entry + sgn * tmults[2] * risk)
        rr, barrier = _room_rr(frame, fi, s["direction"], entry, risk, cfg)
        if rr < cfg["min_rr"]:
            rej["RR_TOO_LOW"] = rej.get("RR_TOO_LOW", 0) + 1
            continue

        setup = {"fill_index": fi, "direction": s["direction"], "entry": round(entry, 2),
                 "risk_points": round(risk, 3), "stop_loss": round(sl, 2),
                 "t1": round(t1, 2), "t2": round(t2, 2), "t3": round(t3, 2)}
        ex = EX.simulate(bars, setup, exc)
        last_exit_i = fi + ex["held_bars"]
        trades.append({
            "engine": name, "session_date": b["session_date"], "entry_hhmm": b["hhmm"],
            "minute_of_day": b["minute_of_day"], "year": b["session_date"][:4],
            "direction": s["direction"], "kind": s.get("kind"),
            "entry": setup["entry"], "stop_loss": setup["stop_loss"],
            "risk_points": setup["risk_points"], "rr_room": round(rr, 2), "barrier": barrier,
            "t1": setup["t1"], "t2": setup["t2"], "t3": setup["t3"],
            "points": ex["points"], "r_multiple": ex["r_multiple"], "win": ex["win"],
            "mfe_R": ex["mfe_R"], "mae_R": ex["mae_R"],
            "t1_hit": ex["t1_hit"], "t2_hit": ex["t2_hit"], "t3_hit": ex["t3_hit"],
            "time_to_t1": ex["time_to_t1"], "time_to_sl": ex["time_to_sl"],
            "held_bars": ex["held_bars"], "exit_reason": ex["exit_reason"],
            "sl_rate": ex["sl_rate"],
            "entry_quality": s.get("entry_quality"),
            "regime_trend": frame[fi].get("regime_trend"),
            "regime_vol": frame[fi].get("regime_vol"),
            "adx": round(frame[fi].get("adx") or 0, 1),
            "atr_pct_rank": round(frame[fi].get("atr_pct_rank") or 0, 3),
            "signal_index": s["signal_index"], "context": s.get("context"),
        })

    return {"trades": trades, "rejected": rej, "signals_total": len(signals),
            "entries_total": len(trades), "ready_total": n_ready}
