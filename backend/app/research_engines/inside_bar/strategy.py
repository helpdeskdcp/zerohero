"""
Inside-Bar 2m setup detection + trade lifecycle. Pure, causal, deterministic.
No parameters are fitted from data -- every threshold comes from config.py.
"""
from __future__ import annotations

from .config import merged


def ema_series(closes: list[float], period: int) -> list[float]:
    if not closes:
        return []
    k = 2.0 / (period + 1.0)
    out = [closes[0]]
    for c in closes[1:]:
        out.append(c * k + out[-1] * (1 - k))
    return out


def atr_at(bars: list[dict], i: int, period: int) -> float:
    lo = max(1, i - period + 1)
    trs = []
    for j in range(lo, i + 1):
        h, l, pc = bars[j]["h"], bars[j]["l"], bars[j - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return (sum(trs) / len(trs)) if trs else 0.0


def _rng(b):
    return b["h"] - b["l"]


def is_inside_bar(bars: list[dict], i: int, cfg: dict) -> bool:
    if i < 2:
        return False
    cur, prev, pprev = bars[i], bars[i - 1], bars[i - 2]
    e = cfg["eps"]
    if not (cur["h"] <= prev["h"] + e and cur["l"] >= prev["l"] - e):
        return False
    pr = _rng(prev)
    if pr <= e:
        return False
    if _rng(cur) > cfg["ib_max_range_frac_of_prev"] * pr:
        return False
    # the bar that formed the IB's mother candle should be a real move, not a doji
    if abs(prev["c"] - prev["o"]) < cfg["ib_min_prev_body_frac"] * pr:
        return False
    return True


def had_momentum(bars: list[dict], i: int, cfg: dict, side: str, atr: float) -> bool:
    lb = cfg["mom_lookback"]
    if i - lb < 0 or atr <= cfg["eps"]:
        return False
    move = bars[i]["c"] - bars[i - lb]["c"]
    if abs(move) < cfg["mom_min_move_atr"] * atr:
        return False
    if cfg["mom_dir_must_match_side"]:
        if side == "LONG" and move <= 0:
            return False
        if side == "SHORT" and move >= 0:
            return False
    return True


def _hhmm_le(a: str, b: str) -> bool:
    return (int(a[:2]) * 60 + int(a[3:])) <= (int(b[:2]) * 60 + int(b[3:]))


def find_setups(bars: list[dict], cfg: dict | None = None) -> list[dict]:
    """Scan one CONTIGUOUS session's 2m bars. Returns triggered setups (breakout
    within `breakout_window` bars of the inside bar), each with entry / stop / 1R."""
    c = cfg or merged()
    if len(bars) < c["warmup_bars"] + 5:
        return []
    closes = [b["c"] for b in bars]
    ema = ema_series(closes, c["ema_period"])
    out = []
    n = len(bars)
    for i in range(c["warmup_bars"], n - 2):
        if not is_inside_bar(bars, i, c):
            continue
        a = atr_at(bars, i, c["atr_period"])
        if a <= c["eps"]:
            continue
        close_i, ema_i = bars[i]["c"], ema[i]
        side = "LONG" if close_i > ema_i else "SHORT" if close_i < ema_i else None
        if side is None:
            continue
        if c["ema_side_strict"] and (
                (side == "LONG" and not close_i > ema_i) or
                (side == "SHORT" and not close_i < ema_i)):
            continue
        if not had_momentum(bars, i, c, side, a):
            continue
        ib_hi, ib_lo = bars[i]["h"], bars[i]["l"]
        buf = c["breakout_buffer_atr"] * a
        # look for breakout in the next `breakout_window` bars
        trig = None
        for k in range(1, c["breakout_window"] + 1):
            j = i + k
            if j >= n:
                break
            if side == "LONG" and bars[j]["h"] >= ib_hi + buf:
                trig = j
                break
            if side == "SHORT" and bars[j]["l"] <= ib_lo - buf:
                trig = j
                break
        if trig is None:
            continue
        if not _hhmm_le(bars[trig]["hhmm"], c["no_new_entry_after_ist"]):
            continue
        if not _hhmm_le(c["session_start_ist"], bars[trig]["hhmm"]):
            continue
        if side == "LONG":
            entry = ib_hi + buf
            stop = ib_lo - c["sl_buffer_atr"] * a
        else:
            entry = ib_lo - buf
            stop = ib_hi + c["sl_buffer_atr"] * a
        r = abs(entry - stop)
        if r <= c["eps"]:
            continue
        out.append({
            "ib_index": i, "entry_index": trig, "side": side,
            "entry": round(entry, 2), "stop": round(stop, 2), "r_points": round(r, 2),
            "ib_high": round(ib_hi, 2), "ib_low": round(ib_lo, 2),
            "atr": round(a, 3), "entry_hhmm": bars[trig]["hhmm"],
            "session_date": bars[trig]["session_date"],
        })
    return out
