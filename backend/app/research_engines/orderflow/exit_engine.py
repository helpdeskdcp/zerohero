"""
Question 4 of the four:  WHEN SHOULD WE EXIT?

Bar-by-bar from the fill bar. 1/3 off at T1 (then SL -> break-even), 1/3 at T2,
last 1/3 at T3 / trail / session-end. SL-before-target within a bar (pessimistic).
Returns points, R multiple, MFE/MAE in R, exit reason, time-to-X, and the
T1/T2/T3-hit + path stats the learning dataset needs.
"""
from __future__ import annotations


def _mod(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[3:])


def simulate(bars: list[dict], setup: dict, cfg: dict) -> dict:
    c = cfg
    i0 = setup["fill_index"]
    long = setup["direction"] == "LONG"
    sgn = 1 if long else -1
    entry = setup["entry"]
    risk = setup["risk_points"]
    stop = setup["stop_loss"]
    t1, t2, t3 = setup["t1"], setup["t2"], setup["t3"]
    end_min = _mod(c["session_end_ist"])

    remaining = 1.0
    pnl = 0.0
    mfe_R = 0.0
    mae_R = 0.0
    t1_hit = t2_hit = t3_hit = False
    tt1 = tt2 = ttsl = None
    exit_reason = None
    held = 0
    be_done = False

    for j in range(i0, len(bars)):
        b = bars[j]
        held = j - i0
        fav = ((b["h"] - entry) if long else (entry - b["l"])) / risk
        adv = ((b["l"] - entry) if long else (entry - b["h"])) / risk
        mfe_R = max(mfe_R, fav)
        mae_R = min(mae_R, adv)

        hit_stop = (b["l"] <= stop) if long else (b["h"] >= stop)
        if hit_stop:
            pnl += remaining * (stop - entry) * sgn
            remaining = 0.0
            ttsl = held
            exit_reason = "STOP" if mfe_R < 1.0 else ("BREAKEVEN" if not t1_hit else "TRAIL")
            break

        # T1
        if not t1_hit:
            r = (b["h"] >= t1) if long else (b["l"] <= t1)
            if r:
                t1_hit = True
                tt1 = held
                frac = c["scale_out"][0][1] if c["scale_out"] else 1.0
                take = min(frac, remaining)
                pnl += take * (t1 - entry) * sgn
                remaining -= take
                if not be_done:
                    stop = entry if long else entry            # SL -> break-even
                    be_done = True
        # T2
        if t1_hit and not t2_hit and remaining > 0 and len(c["scale_out"]) > 1:
            r = (b["h"] >= t2) if long else (b["l"] <= t2)
            if r:
                t2_hit = True
                tt2 = held
                frac = c["scale_out"][1][1]
                take = min(frac, remaining)
                pnl += take * (t2 - entry) * sgn
                remaining -= take
        # T3 / runner cap
        if remaining > 0:
            r3 = (b["h"] >= t3) if long else (b["l"] <= t3)
            cap = entry + sgn * c["runner_cap_r"] * risk
            rcap = (b["h"] >= cap) if long else (b["l"] <= cap)
            if r3:
                t3_hit = True
                pnl += remaining * (t3 - entry) * sgn
                remaining = 0.0
                exit_reason = "TARGET_T3"
                break
            if rcap:
                pnl += remaining * (cap - entry) * sgn
                remaining = 0.0
                exit_reason = "RUNNER_CAP"
                break

        if b["minute_of_day"] >= end_min and remaining > 0:
            pnl += remaining * (b["c"] - entry) * sgn
            remaining = 0.0
            exit_reason = "SESSION_END"
            break

    if remaining > 0:
        pnl += remaining * (bars[-1]["c"] - entry) * sgn
        exit_reason = exit_reason or "EOD"

    pnl -= c["cost_points"]
    rmult = pnl / risk if risk else 0.0
    return {
        "points": round(pnl, 3), "r_multiple": round(rmult, 4),
        "win": pnl > 0.0, "mfe_R": round(mfe_R, 3), "mae_R": round(mae_R, 3),
        "t1_hit": t1_hit, "t2_hit": t2_hit, "t3_hit": t3_hit,
        "time_to_t1": tt1, "time_to_t2": tt2, "time_to_sl": ttsl,
        "held_bars": held, "exit_reason": exit_reason,
        "sl_rate": 1.0 if exit_reason == "STOP" else 0.0,
    }
