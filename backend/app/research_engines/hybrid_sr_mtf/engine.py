"""Candidates + NO-TRADE filters. All thresholds are fixed in Params BEFORE any result was looked at (pre-registered); nothing here is fitted."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from .data import OPEN_MIN, Prep
from .sim import simulate

CANDIDATES = ("S1_SWEEP_REVERSAL", "S2_BREAK_RETEST", "S3_TREND_PULLBACK")


@dataclass(frozen=True)
class Params:
    win_from: int = 9 * 60 + 35; win_to: int = 14 * 60 + 30; exit_min: int = 15 * 60 + 15
    sweep_lookback: int = 48; sweep_min_atr: float = 0.05; zone_atr15: float = 0.5
    brk_min_atr: float = 0.10; retest_bars: int = 8; brk_body: float = 0.60; brk_range_atr: float = 1.0; retest_tol_atr: float = 0.10
    pull_atr: float = 0.25; trend_ctx_bars: int = 12
    trig_bars: int = 10; trig_back: int = 3; trig_close_loc: float = 0.6
    sl_buf_atr: float = 0.2; min_rr: float = 1.2; max_risk_atr: float = 2.5; min_risk_atr: float = 0.4
    chop_bars: int = 20; chop_range_atr: float = 2.5
    htf_filter: bool = True                         # False only for the "what do the filters buy" control run
    room_levels: str = "all"                      # 'all' = every pivot counts as an opposing level for the room test; 'major' = only 15m pivots / previous-day / opening-range levels
    max_trades_day: int = 2; max_consec_loss: int = 2; cooldown_min: int = 10


def _bias15(prep: Prep, i15: int) -> int:
    t = prep.tf15
    if i15 < 55 or np.isnan(t.ema50[i15]): return 0
    up = t.c[i15] > t.ema20[i15] > t.ema50[i15] and t.ema20[i15] > t.ema20[i15 - 3]
    dn = t.c[i15] < t.ema20[i15] < t.ema50[i15] and t.ema20[i15] < t.ema20[i15 - 3]
    return 1 if up else -1 if dn else 0


def _recent_pivots(arr: np.ndarray, upto: int, lookback: int) -> list[float]:
    lo = max(0, upto - lookback + 1); seg = arr[lo:upto + 1]; return [float(x) for x in seg[~np.isnan(seg)]]


def levels_at(prep: Prep, i5: int, i15: int, day: int, minute: int) -> tuple[list[float], list[float]]:
    """(major levels, minor levels) known at the decision time. Major = confirmed 15m pivots (last ~80 bars), previous day high / low, opening-range high / low (from 09:30)."""
    maj = _recent_pivots(prep.tf15.sh, i15, 80) + _recent_pivots(prep.tf15.sl, i15, 80)
    if day in prep.pdh: maj += [prep.pdh[day], prep.pdl[day]]
    if minute >= 9 * 60 + 30:
        m = (prep.day == day); idx = np.flatnonzero(m)[:15]
        if len(idx) >= 12: maj += [float(prep.h[idx].max()), float(prep.l[idx].min())]
    minor = _recent_pivots(prep.tf5.sh, i5, 48) + _recent_pivots(prep.tf5.sl, i5, 48)
    return maj, minor


def _flat_run(prep: Prep, j: int, n: int = 10) -> bool:
    lo = max(0, j - n + 1); seg_c = prep.c[lo:j + 1]
    if len(seg_c) < n: return True
    if prep.t1[j] - prep.t1[lo] != n - 1: return True                      # a missing minute inside the last 10
    return bool((np.diff(seg_c) == 0).sum() >= 5)                          # >= 5 unchanged closes in 10 minutes: stale feed


def _targets(side: int, entry: float, risk: float, maj: list[float], minor: list[float], atr15: float, p: Params):
    allv = sorted(set(maj + (minor if p.room_levels == 'all' else []))); above = [x for x in allv if (x - entry) * side > 0]; above.sort(key=lambda x: (x - entry) * side)
    if above and (above[0] - entry) * side < p.min_rr * risk: return None, "ROOM"                       # the nearest opposing level is too close
    cand = [x for x in above if (x - entry) * side >= p.min_rr * risk]
    t1 = cand[0] if cand else entry + side * max(p.min_rr * risk, 1.0 * risk)
    t2c = [x for x in cand if (x - t1) * side >= 0.5 * risk]
    t2 = t2c[0] if t2c else entry + side * max(2.0 * atr15, abs(t1 - entry) + 0.5 * risk)                       # no further level: an ATR-based extension
    t3 = entry + side * max(3.0 * risk, abs(t2 - entry) + 0.5 * risk)
    return (t1, t2, t3), None


def scan(prep: Prep, cand: str, p: Params = Params(), collect: bool = True, start_day: int | None = None, end_day: int | None = None, events: list | None = None) -> tuple[list[dict], Counter, dict]:
    """-> (trades, reject reasons, stats). A 'setup' is a 5-minute structure event; it becomes a trade only if it passes every filter and a 1-minute trigger fires within `trig_bars` minutes."""
    t5, t15 = prep.tf5, prep.tf15; n5 = len(t5.c); rej: Counter = Counter(); trades: list[dict] = []; stats = Counter()
    mins1 = (prep.t1 % 1440); pending: dict = {}; busy_until = -1; day_trades: Counter = Counter(); day_loss: Counter = Counter(); last_exit_abs = -10 ** 9
    j15 = 0
    for i5 in range(60, n5):
        day = int(t5.day[i5])
        if start_day and day < start_day: continue
        if end_day and day > end_day: break
        end_abs = int(t5.end[i5]); minute = end_abs % 1440
        while j15 + 1 < len(t15.end) and t15.end[j15 + 1] <= end_abs: j15 += 1
        i15 = j15
        if t15.end[i15] > end_abs: continue
        atr5 = t5.atr[i5]
        if not np.isfinite(atr5) or atr5 <= 0: continue
        setups: list[tuple[int, float, float, str]] = []                    # (side, sl_anchor, level, note)
        if cand == "S1_SWEEP_REVERSAL":
            maj, _ = levels_at(prep, i5, i15, day, minute)
            ph = _recent_pivots(t5.sh, i5 - 1, p.sweep_lookback); pl = _recent_pivots(t5.sl, i5 - 1, p.sweep_lookback)
            zone = p.zone_atr15 * (t15.atr[i15] if np.isfinite(t15.atr[i15]) else atr5)
            if ph:
                L = max(ph)
                if t5.h[i5] > L + p.sweep_min_atr * atr5 and t5.c[i5] < L and t5.c[i5 - 1] <= L and any(abs(x - L) <= zone for x in maj): setups.append((-1, t5.h[i5], L, "bsl_sweep"))
            if pl:
                L = min(pl)
                if t5.l[i5] < L - p.sweep_min_atr * atr5 and t5.c[i5] > L and t5.c[i5 - 1] >= L and any(abs(x - L) <= zone for x in maj): setups.append((1, t5.l[i5], L, "ssl_sweep"))
        elif cand == "S2_BREAK_RETEST":
            maj, minor = levels_at(prep, i5, i15, day, minute); allv = maj + minor
            rng = t5.h[i5] - t5.l[i5]; body = abs(t5.c[i5] - t5.o[i5]) / rng if rng > 0 else 0
            for side in (1, -1):                                                # new breakout?
                lv = [x for x in allv if (t5.c[i5 - 1] - x) * side <= 0 and (t5.c[i5] - x) * side >= p.brk_min_atr * atr5]
                if lv and rng >= p.brk_range_atr * atr5 and body >= p.brk_body and (t5.c[i5] - t5.o[i5]) * side > 0:
                    L = max(lv) if side > 0 else min(lv); pending[side] = (L, i5); stats["breakouts"] += 1
            for side in (1, -1):                                                # retest of an earlier breakout?
                if side in pending:
                    L, ib = pending[side]
                    if i5 == ib: continue
                    if i5 - ib > p.retest_bars: stats["breakout_no_retest"] += 1; del pending[side]; continue
                    if (t5.c[i5] - L) * side < -p.retest_tol_atr * atr5: stats["breakout_failed"] += 1; del pending[side]; continue                      # closed back through the level
                    touched = (t5.l[i5] <= L + p.retest_tol_atr * atr5) if side > 0 else (t5.h[i5] >= L - p.retest_tol_atr * atr5)
                    if touched and (t5.c[i5] - L) * side > 0:
                        anchor = min(t5.l[i5], L) if side > 0 else max(t5.h[i5], L); setups.append((side, anchor, L, "retest")); stats["breakout_retested"] += 1; del pending[side]
        elif cand == "S3_TREND_PULLBACK":
            e20 = t5.ema20[i5]
            for side in (1, -1):
                trend = (e20 > t5.ema50[i5]) if side > 0 else (e20 < t5.ema50[i5])
                if not trend: continue
                ctx = t5.h[i5 - p.trend_ctx_bars:i5].max() if side > 0 else t5.l[i5 - p.trend_ctx_bars:i5].min()
                made_ext = (ctx > e20 + 2 * atr5) if side > 0 else (ctx < e20 - 2 * atr5)
                touch = (t5.l[i5] <= e20 + p.pull_atr * atr5) if side > 0 else (t5.h[i5] >= e20 - p.pull_atr * atr5)
                if made_ext and touch and (t5.c[i5] - e20) * side > 0 and (t5.c[i5] - t5.o[i5]) * side > 0:
                    anchor = t5.l[i5 - 2:i5 + 1].min() if side > 0 else t5.h[i5 - 2:i5 + 1].max(); setups.append((side, anchor, e20, "pullback"))
        for side, anchor, lvl, note in setups:
            stats["setups"] += 1; r = _filter_and_trade(prep, p, cand, i5, i15, day, minute, end_abs, side, anchor, lvl, note, mins1, busy_until, day_trades, day_loss, last_exit_abs, rej, events)
            if r is not None:
                trades.append(r); busy_until = r["last_i"]; day_trades[day] += 1; last_exit_abs = int(prep.t1[r["last_i"]]); day_loss[day] = (day_loss[day] + 1) if r["R"] < 0 else 0
    return trades, rej, dict(stats)


def _filter_and_trade(prep, p, cand, i5, i15, day, minute, end_abs, side, anchor, lvl, note, mins1, busy_until, day_trades, day_loss, last_exit_abs, rej, events=None):
    def no(reason):
        rej[reason] += 1
        if events is not None: events.append({"cand": cand, "t_setup": end_abs, "side": side, "day": day, "note": note, "outcome": reason})
        return None
    t5 = prep.tf5; t15 = prep.tf15
    if not (p.win_from <= minute <= p.win_to): return no("TIME_WINDOW")
    if t5.n[i5] < 5 or t15.n[i15] < 15: return no("INCOMPLETE_BAR")
    if day_trades[day] >= p.max_trades_day: return no("DAY_TRADE_LIMIT")
    if day_loss[day] >= p.max_consec_loss: return no("DAY_LOSS_LIMIT")
    b = _bias15(prep, i15)
    if p.htf_filter:
        if cand == "S1_SWEEP_REVERSAL":
            if b == -side: return no("HTF_CONTRADICTION")
        elif b != side: return no("HTF_CONTRADICTION")                  # continuation setups need the 15m bias strictly in their direction
    rng = t5.h[i5 - p.chop_bars + 1:i5 + 1].max() - t5.l[i5 - p.chop_bars + 1:i5 + 1].min()
    if cand != "S1_SWEEP_REVERSAL" and rng <= p.chop_range_atr * t5.atr[i5]: return no("CHOP_RANGE")
    j0 = int(np.searchsorted(prep.t1, end_abs, side="left"))                    # first 1-minute bar starting at/after the completion of the 5m bar
    for j in range(j0, min(j0 + p.trig_bars, len(prep.c) - 1)):
        if prep.day[j] != day or prep.day[j + 1] != day: break
        if mins1[j] > p.win_to + p.trig_bars: break
        rg = prep.h[j] - prep.l[j]
        if rg <= 0 or j < 4: continue
        loc = (prep.c[j] - prep.l[j]) / rg if side > 0 else (prep.h[j] - prep.c[j]) / rg
        brk = (prep.c[j] > prep.h[j - p.trig_back:j].max()) if side > 0 else (prep.c[j] < prep.l[j - p.trig_back:j].min())
        if not (brk and loc >= p.trig_close_loc and (prep.c[j] - prep.o[j]) * side > 0): continue
        if j <= busy_until: return no("IN_POSITION")
        if prep.t1[j + 1] - last_exit_abs < p.cooldown_min: return no("COOLDOWN")
        if _flat_run(prep, j): return no("STALE_OR_GAPPED_DATA")
        entry = float(prep.o[j + 1]); atr5 = float(t5.atr[i5]); sl = anchor - side * p.sl_buf_atr * atr5; risk = abs(entry - sl)
        if (entry - sl) * side <= 0: return no("ENTRY_BEYOND_STOP")
        if risk > p.max_risk_atr * atr5: return no("RISK_TOO_WIDE")
        if risk < p.min_risk_atr * atr5: return no("RISK_TOO_NARROW")
        maj, minor = levels_at(prep, i5, i15, day, minute); a15 = float(t15.atr[i15]) if np.isfinite(t15.atr[i15]) else atr5 * 2
        tg, why = _targets(side, entry, risk, maj, minor, a15, p)
        if tg is None: return no("NO_ROOM_TO_OPPOSING_LEVEL")
        res = simulate(prep.o, prep.h, prep.l, prep.c, mins1, prep.day, j + 1, side, entry, sl, tg[0], tg[1], tg[2], p.exit_min)
        if events is not None: events.append({"cand": cand, "t_setup": end_abs, "side": side, "day": day, "note": note, "outcome": "TRADE"})
        return {"cand": cand, "day": day, "setup": note, "side": side, "t_setup": end_abs, "t_entry": int(prep.t1[j + 1]), "entry": entry, "sl": sl, "t1": tg[0], "t2": tg[1], "t3": tg[2], "atr5": atr5,
                "bias15": b, "entry_i": j + 1, **res}
    return no("NO_1M_TRIGGER")
