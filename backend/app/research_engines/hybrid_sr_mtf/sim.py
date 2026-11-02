"""Trade simulation on 1-minute bars. Conservative and causal: the entry is the OPEN of the bar after the decision; a bar that touches both the stop and a target counts the STOP first (flagged ambiguous);
after a leg fills the stop moves from the NEXT bar (T1 -> entry, T2 -> T1). Legs 40 / 30 / 30 %. Time exit at the open of the first bar at/after `exit_min`. A gap through the stop fills at the open."""
from __future__ import annotations

LEGS = (0.4, 0.3, 0.3)


def simulate(o, h, l, c, mins, day, i0: int, side: int, entry: float, sl: float, t1: float, t2: float, t3: float, exit_min: int = 15 * 60 + 15) -> dict:
    """side +1 long / -1 short; i0 = index of the entry bar (the entry price is passed in: that bar's open). -> composite points per unit, R, MAE / MFE in points, ambiguity flag, exit reason."""
    risk = abs(entry - sl); d0 = day[i0]; stop = sl; tgt = (t1, t2, t3); done = [False, False, False]; left = 1.0; pts = 0.0; mae = mfe = 0.0; amb = False; reason = "DATA_END"; i = i0
    n = len(o)
    while i < n and day[i] == d0:
        if i > i0 and mins[i] >= exit_min:
            pts += left * (o[i] - entry) * side; left = 0.0; reason = "TIME"; break
        hi, lo = h[i], l[i]
        mae = max(mae, (entry - lo) if side > 0 else (hi - entry)); mfe = max(mfe, (hi - entry) if side > 0 else (entry - lo))
        if (lo <= stop) if side > 0 else (hi >= stop):
            amb = amb or any((not done[k]) and ((hi >= tgt[k]) if side > 0 else (lo <= tgt[k])) for k in range(3))
            px = min(o[i], stop) if side > 0 else max(o[i], stop)
            pts += left * (px - entry) * side; left = 0.0; reason = "SL" if not any(done) else "SL_AFTER_TARGET"; break
        for k in range(3):
            if not done[k] and ((hi >= tgt[k]) if side > 0 else (lo <= tgt[k])):
                done[k] = True; pts += LEGS[k] * (tgt[k] - entry) * side; left -= LEGS[k]
        if left <= 1e-9: reason = "T3"; break
        if done[1]: stop = max(stop, t1) if side > 0 else min(stop, t1)
        elif done[0]: stop = max(stop, entry) if side > 0 else min(stop, entry)
        i += 1
    last = min(i, n - 1)
    if left > 1e-9:                                                              # the session ended / data ended without an exit: the last close
        last = i - 1 if (i >= n or day[min(i, n - 1)] != d0) else last
        pts += left * (c[last] - entry) * side; reason = "SESSION_END"
    return {"pts": pts, "R": pts / risk if risk > 0 else 0.0, "risk": risk, "mae": mae, "mfe": mfe, "ambiguous": amb, "reason": reason, "last_i": last, "hit_t1": done[0], "hit_t2": done[1], "hit_t3": done[2]}
