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


def adx_at(bars: list[dict], i: int, period: int) -> float:
    """Wilder's ADX over the `period` bars ending at i (simple-average form --
    deterministic, causal, good enough for a trend-strength gate)."""
    lo = i - 2 * period + 1
    if lo < 1:
        return 0.0
    plus_dm, minus_dm, tr = [], [], []
    for j in range(i - period + 1, i + 1):
        up = bars[j]["h"] - bars[j - 1]["h"]
        dn = bars[j - 1]["l"] - bars[j]["l"]
        plus_dm.append(up if (up > dn and up > 0) else 0.0)
        minus_dm.append(dn if (dn > up and dn > 0) else 0.0)
        h, l, pc = bars[j]["h"], bars[j]["l"], bars[j - 1]["c"]
        tr.append(max(h - l, abs(h - pc), abs(l - pc)))
    atr = sum(tr) / len(tr)
    if atr <= 1e-9:
        return 0.0
    pdi = 100.0 * (sum(plus_dm) / len(plus_dm)) / atr
    mdi = 100.0 * (sum(minus_dm) / len(minus_dm)) / atr
    denom = pdi + mdi
    return (100.0 * abs(pdi - mdi) / denom) if denom > 1e-9 else 0.0


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
        # optional trend-strength gate (canonical inside-bar teaching: ADX > 20-25)
        if c.get("adx_min", 0) > 0 and adx_at(bars, i, c.get("adx_period", 14)) < c["adx_min"]:
            continue
        ib_hi, ib_lo = bars[i]["h"], bars[i]["l"]
        mother_hi, mother_lo = bars[i - 1]["h"], bars[i - 1]["l"]
        # which candle's extreme is the breakout / entry reference?
        if c.get("entry_ref", "ib") == "mother":
            brk_hi, brk_lo = mother_hi, mother_lo
        else:
            brk_hi, brk_lo = ib_hi, ib_lo
        buf = c["breakout_buffer_atr"] * a
        confirm = c.get("breakout_confirm", "touch")   # 'touch' | 'close'
        # look for breakout in the next `breakout_window` bars
        trig = None
        for k in range(1, c["breakout_window"] + 1):
            j = i + k
            if j >= n:
                break
            px_up = bars[j]["c"] if confirm == "close" else bars[j]["h"]
            px_dn = bars[j]["c"] if confirm == "close" else bars[j]["l"]
            if side == "LONG" and px_up >= brk_hi + buf:
                trig = j
                break
            if side == "SHORT" and px_dn <= brk_lo - buf:
                trig = j
                break
        if trig is None:
            continue
        # 'close' confirm only KNOWS the breakout once bar `trig` has closed, so
        # the fill + the P&L simulation must start on the NEXT bar. 'touch' fills
        # intrabar on `trig` itself (stop-order semantics).
        if confirm == "close":
            entry_idx = trig + 1
            if entry_idx >= n:
                continue
        else:
            entry_idx = trig
        if not _hhmm_le(bars[entry_idx]["hhmm"], c["no_new_entry_after_ist"]):
            continue
        if not _hhmm_le(c["session_start_ist"], bars[entry_idx]["hhmm"]):
            continue
        stop_ref = c.get("stop_ref", "ib")            # 'ib' | 'mother' | 'mother_mid'
        if stop_ref == "mother":
            s_lo, s_hi = mother_lo, mother_hi
        elif stop_ref == "mother_mid":
            mid = 0.5 * (mother_hi + mother_lo)
            s_lo, s_hi = mid, mid
        else:
            s_lo, s_hi = ib_lo, ib_hi
        if confirm == "close":
            # fill at the confirmed close (no mid-bar time-travel)
            entry = bars[trig]["c"]
        elif side == "LONG":
            entry = brk_hi + buf
        else:
            entry = brk_lo - buf
        if side == "LONG":
            stop = s_lo - c["sl_buffer_atr"] * a
        else:
            stop = s_hi + c["sl_buffer_atr"] * a
        r = abs(entry - stop)
        if r <= c["eps"]:
            continue
        out.append({
            "ib_index": i, "entry_index": entry_idx, "side": side,
            "entry": round(entry, 2), "stop": round(stop, 2), "r_points": round(r, 2),
            "ib_high": round(ib_hi, 2), "ib_low": round(ib_lo, 2),
            "atr": round(a, 3), "entry_hhmm": bars[entry_idx]["hhmm"],
            "session_date": bars[entry_idx]["session_date"],
        })
    return out
