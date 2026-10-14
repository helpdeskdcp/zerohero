"""Bar-by-bar Python port of "Hybrid Market Profile Engine v13.0 | Candlestick Edition" (Pine v6) + a trade simulator.

RESEARCH ONLY: no broker/order path, nothing here is imported by live code.

Fidelity rules (the order of Pine sections 04 -> 06 -> 08 -> 09 -> 10/11 -> 12 is preserved bar by bar):
* structure (BoS/CHoCH) is updated BEFORE trendDir is read, so a continuation entry can fire on the BoS bar itself;
* new signals are evaluated with the `active` flag as it stood at the START of the bar; trade management for a trade
  entered on an earlier bar runs AFTER signal/entry evaluation (so a trade stopped on bar j can only be replaced from j+1);
* management starts on bar_index > entryBar; stop is tested BEFORE targets inside a bar (adverse-first);
* pending setup: expiry (retest_bars), session-close cancel, new-day clear only when not active, opposite break ignored
  while a pending setup exists, pending consumed by any signal / rejected retest.

Things Pine leaves undefined (an indicator has no P&L) are explicit parameters, never hidden:
  exit_model  PINE (literal: SL or T4, targets are milestones)  | T1/T2/T3 (all out) | SCALE (25% at T1..T4) | SCALE_BE
  fill        "close" (as plotted: entry at the signal bar close)  | "next_open" (realistic)
  gap_fills   False = exits at the level (as plotted) | True = gaps fill at the open
  eod_flat    False = trades may be held overnight (as the script behaves) | True = flat at the session's last bar
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from ..smart_bos.data import ist_minutes
from ..smart_bos.engine import _atr, _pivots
from ..smart_bos.sim import Trade

NAN = float("nan")


@dataclass
class Params:
    # --- Pine inputs (defaults identical to the script) ---
    pivot_len: int = 5
    atr_len: int = 14
    retest_bars: int = 8
    retest_tol_atr: float = 0.20
    use_vwap: bool = False
    use_candle_sl: bool = True
    use_pattern_filter: bool = False
    maru_body_ratio: float = 0.85
    max_wick_ratio: float = 0.08
    entry_mode: str = "hybrid"                 # "hybrid" | "retest"
    continuation_lookback: int = 5
    require_mtf: bool = False
    min_agree: int = 3
    mtf_lookback: int = 5
    mtf_tfs: tuple = (3, 5, 15, 40)
    use_session: bool = True
    session: tuple = (555, 930)                # minute-of-day [start, end): NSE 09:15-15:30 IST (MCX: (540, 1410))
    mintick: float = 0.05
    pivot_ties: str = "allow"                  # Pine tie behaviour for ta.pivothigh/low is not specified in the reference we have
    # --- execution assumptions (NOT in Pine) ---
    chart_tf: int = 1                          # chart timeframe in minutes (bars must already be this timeframe)
    exit_model: str = "PINE"
    fill: str = "close"
    gap_fills: bool = False
    eod_flat: bool = False
    weekdays_only: bool = True


@dataclass
class HTrade(Trade):
    max_t: int = 0                             # highest target reached before the exit (0..4)
    overnight_days: int = 0
    entry_type: str = ""


def resample(b1: dict, tf: int, open_min: int = 555, close_min: int = 930) -> dict:
    """1-minute bars -> tf-minute bars anchored at the session open (bar time = bucket start). tf=1 returns session bars."""
    t = b1["t"]
    days, mod = ist_minutes(t)
    ins = (mod >= open_min) & (mod < close_min)
    sel = np.flatnonzero(ins)
    key = days[sel] * 10000 + (mod[sel] - open_min) // tf
    if len(sel) == 0:
        return {k: np.array([]) for k in ("t", "o", "h", "l", "c", "v")}
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:] - 1, len(sel) - 1]
    idx = sel[starts]
    out = {
        "t": (t[idx] - ((mod[idx] - open_min) % tf) * 60).astype(np.int64),
        "o": b1["o"][idx],
        "h": np.maximum.reduceat(b1["h"][sel], starts),
        "l": np.minimum.reduceat(b1["l"][sel], starts),
        "c": b1["c"][sel[ends]],
        "v": np.add.reduceat(b1["v"][sel], starts) if "v" in b1 else np.zeros(len(starts)),
    }
    return out


def mtf_dirs(chart: dict, b1: dict, p: Params) -> np.ndarray:
    """(n, 4) int8: direction of the LAST COMPLETED higher-timeframe bar before the one containing each chart bar
    (== request.security(..., f(len)[1], lookahead_on)). f = close beyond prior-N extreme, else candle colour."""
    n = len(chart["t"])
    out = np.zeros((n, len(p.mtf_tfs)), dtype=np.int8)
    cdays, cmod = ist_minutes(chart["t"])
    for j, N in enumerate(p.mtf_tfs):
        H = resample(b1, N, p.session[0], p.session[1])
        hh = len(H["t"])
        if hh == 0:
            continue
        hd, hm = ist_minutes(H["t"])
        hkey = hd * 10000 + (hm - p.session[0]) // N
        L = p.mtf_lookback
        hi, lo, cl, op = H["h"], H["l"], H["c"], H["o"]
        d = np.zeros(hh, dtype=np.int8)
        for k in range(hh):
            hmax = hi[k - L:k].max() if k >= L else NAN
            lmin = lo[k - L:k].min() if k >= L else NAN
            if not math.isnan(hmax) and cl[k] > hmax:
                d[k] = 1
            elif not math.isnan(lmin) and cl[k] < lmin:
                d[k] = -1
            elif cl[k] > op[k]:
                d[k] = 1
            elif cl[k] < op[k]:
                d[k] = -1
        ckey = cdays * 10000 + (cmod - p.session[0]) // N
        pos = np.searchsorted(hkey, ckey)                       # bucket containing the chart bar's OPEN
        pos = np.clip(pos, 0, hh - 1)
        ok = (hkey[pos] == ckey) & (pos >= 1)
        out[ok, j] = d[pos[ok] - 1]
    return out


def _flags(chart: dict, p: Params) -> dict:
    o, h, l, c = chart["o"], chart["h"], chart["l"], chart["c"]
    n = len(o)
    rng = np.maximum(h - l, p.mintick)
    body = np.abs(c - o)
    uw = h - np.maximum(o, c)
    lw = np.minimum(o, c) - l
    br = body / rng
    po, pc = np.r_[NAN, o[:-1]], np.r_[NAN, c[:-1]]
    with np.errstate(invalid="ignore"):
        bull_eng = (c > o) & (pc < po) & (o <= pc) & (c >= po)
        bear_eng = (c < o) & (pc > po) & (o >= pc) & (c <= po)
        bull_maru = (c > o) & (br >= p.maru_body_ratio) & (uw / rng <= p.max_wick_ratio) & (lw / rng <= p.max_wick_ratio)
        bear_maru = (c < o) & (br >= p.maru_body_ratio) & (uw / rng <= p.max_wick_ratio) & (lw / rng <= p.max_wick_ratio)
    bull_ham = (c > o) & (lw >= body * 2) & (uw <= body)
    bear_star = (c < o) & (uw >= body * 2) & (lw <= body)
    bull_pat, bear_pat = bull_eng | bull_maru | bull_ham, bear_eng | bear_maru | bear_star
    bull_ok = (c > o) & (~p.use_pattern_filter | bull_pat)
    bear_ok = (c < o) & (~p.use_pattern_filter | bear_pat)
    L = p.continuation_lookback
    hhL, llL = np.full(n, NAN), np.full(n, NAN)
    if n > L:
        hhL[L:] = sliding_window_view(h, L).max(axis=1)[:n - L]
        llL[L:] = sliding_window_view(l, L).min(axis=1)[:n - L]
    return dict(bull_eng=bull_eng, bear_eng=bear_eng, bull_maru=bull_maru, bear_maru=bear_maru, bull_ham=bull_ham,
                bear_star=bear_star, bull_ok=bull_ok, bear_ok=bear_ok, hhL=hhL, llL=llL)


def _vwap(chart: dict, days: np.ndarray) -> np.ndarray:
    v = chart.get("v")
    n = len(days)
    if v is None or not np.any(v > 0):
        return np.full(n, NAN)
    hlc3 = (chart["h"] + chart["l"] + chart["c"]) / 3.0
    out = np.full(n, NAN)
    start = 0
    for i in range(1, n + 1):
        if i == n or days[i] != days[start]:
            pv, vv = np.cumsum(hlc3[start:i] * v[start:i]), np.cumsum(v[start:i])
            with np.errstate(invalid="ignore", divide="ignore"):
                out[start:i] = np.where(vv > 0, pv / vv, NAN)
            start = i
    return out


def run(chart: dict, p: Params | None = None, b1: dict | None = None) -> tuple[list[HTrade], dict]:
    """chart: bars at p.chart_tf (t = bar OPEN epoch). b1: 1-minute bars (only needed when require_mtf)."""
    p = p or Params()
    t, o, h, l, c = (chart[k] for k in ("t", "o", "h", "l", "c"))
    n = len(t)
    days, mod = ist_minutes(t)
    wd = ((t + 19800) // 86400 + 4) % 7
    sess_ok = np.ones(n, dtype=bool) if not p.use_session else ((mod >= p.session[0]) & (mod < p.session[1]) &
                                                                  ((wd < 5) if p.weekdays_only else True))
    F = _flags(chart, p)
    atr = _atr(h, l, c, p.atr_len)
    ph, pl = _pivots(h, p.pivot_len, True, p.pivot_ties), _pivots(l, p.pivot_len, False, p.pivot_ties)
    vwap = _vwap(chart, days)
    mt = mtf_dirs(chart, b1, p) if (p.require_mtf and b1 is not None) else np.zeros((n, 4), dtype=np.int8)
    bull_agree, bear_agree = (mt == 1).sum(axis=1), (mt == -1).sum(axis=1)
    L_, ol, hl, ll, cl_ = (x.tolist() for x in (atr, o, h, l, c))
    phl, pll, hhL, llL = ph.tolist(), pl.tolist(), F["hhL"].tolist(), F["llL"].tolist()
    vw = vwap.tolist()
    day_l, mod_l, sess_l = days.tolist(), mod.tolist(), sess_ok.tolist()
    bull_ok, bear_ok = F["bull_ok"].tolist(), F["bear_ok"].tolist()
    b_eng, s_eng, b_maru, s_maru = F["bull_eng"].tolist(), F["bear_eng"].tolist(), F["bull_maru"].tolist(), F["bear_maru"].tolist()
    b_ham, s_star = F["bull_ham"].tolist(), F["bear_star"].tolist()
    ba, sa = bull_agree.tolist(), bear_agree.tolist()
    hybrid = p.entry_mode == "hybrid"

    swingH = swingL = NAN
    structure = 0
    pend_dir, pend_level, pend_bar = 0, NAN, -1
    active, side, entry, sl, R = False, 0, NAN, NAN, NAN
    tg = [NAN] * 4
    done = [False] * 4
    entry_bar, sig_bar, fill_pending = -1, -1, False
    remain, parts, cur_sl, mae, mfe, ent_type, ent_day = 0.0, [], NAN, 0.0, 0.0, "", 0
    trades: list[HTrade] = []
    st = dict(bars=n, signals_buy=0, signals_sell=0, rejected=0, no_trade_small_R=0, expired=0, cancelled_session=0,
              pending_set=0, skipped_gap_fill=0, skipped_eod=0, breaks_up=0, breaks_dn=0, choch=0, bos=0)

    def close_trade(j, reason, px_parts, max_t):
        nonlocal active
        pnl = sum(w * side * (px - entry) for w, px in px_parts)
        trades.append(HTrade(sig_bar, entry_bar + (1 if p.fill == "next_open" else 0), j, side, entry, px_parts[-1][1], R, pnl,
                             pnl / R, 1e4 * pnl / entry, mae, mfe, reason, int(ent_day), ent_type, tuple(px_parts),
                             max_t=max_t, overnight_days=int(day_l[j] - ent_day), entry_type=ent_type))
        active = False

    for i in range(n):
        sess = sess_l[i]
        newday = i == 0 or day_l[i] != day_l[i - 1]
        # ---- 04 structure ----
        if not math.isnan(phl[i]):
            swingH = phl[i]
        if not math.isnan(pll[i]):
            swingL = pll[i]
        prev_c = cl_[i - 1] if i > 0 else NAN
        break_h = (not math.isnan(swingH)) and cl_[i] > swingH and (not math.isnan(prev_c)) and prev_c <= swingH
        break_l = (not math.isnan(swingL)) and cl_[i] < swingL and (not math.isnan(prev_c)) and prev_c >= swingL
        bull_ch, bear_ch = break_h and structure == -1, break_l and structure == 1
        if break_h:
            structure = 1; st["breaks_up"] += 1
        if break_l:
            structure = -1; st["breaks_dn"] += 1
        if break_h or break_l:
            st["choch" if (bull_ch or bear_ch) else "bos"] += 1
        allow_buy, allow_sell = structure == 1, structure == -1
        # ---- 06 / 08 pending housekeeping ----
        if newday and not active:
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        if pend_dir != 0 and pend_bar >= 0 and i - pend_bar > p.retest_bars:
            st["expired"] += 1
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        if pend_dir != 0 and not sess:
            st["cancelled_session"] += 1
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        new_up = sess and break_h and not active and pend_dir == 0
        new_dn = sess and break_l and not active and pend_dir == 0
        if new_up:
            pend_dir, pend_level, pend_bar = 1, swingH, i; st["pending_set"] += 1
        if new_dn:
            pend_dir, pend_level, pend_bar = -1, swingL, i; st["pending_set"] += 1
        tol = 0.0 if math.isnan(L_[i]) else L_[i] * p.retest_tol_atr
        bull_retest = pend_dir == 1 and i > pend_bar and ll[i] <= pend_level + tol and cl_[i] > pend_level and bull_ok[i]
        bear_retest = pend_dir == -1 and i > pend_bar and hl[i] >= pend_level - tol and cl_[i] < pend_level and bear_ok[i]
        bull_cont = hybrid and (not math.isnan(hhL[i])) and cl_[i] > hhL[i] and bull_ok[i]
        bear_cont = hybrid and (not math.isnan(llL[i])) and cl_[i] < llL[i] and bear_ok[i]
        vw_i = vw[i]
        vwap_long = (not p.use_vwap) or math.isnan(vw_i) or cl_[i] >= vw_i
        vwap_short = (not p.use_vwap) or math.isnan(vw_i) or cl_[i] <= vw_i
        mtf_long = (not p.require_mtf) or ba[i] >= p.min_agree
        mtf_short = (not p.require_mtf) or sa[i] >= p.min_agree
        buy_ok, sell_ok = vwap_long and mtf_long, vwap_short and mtf_short
        buy_sig = sess and not active and allow_buy and (bull_retest or bull_cont) and buy_ok
        sell_sig = sess and not active and allow_sell and (bear_retest or bear_cont) and sell_ok
        buy_rej = bull_retest and (not allow_buy or not buy_ok)
        sell_rej = bear_retest and (not allow_sell or not sell_ok)
        if buy_rej or sell_rej:
            st["rejected"] += 1
        if buy_sig or sell_sig or buy_rej or sell_rej:
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        # ---- 10/11 entries ----
        if buy_sig or sell_sig:
            d = 1 if buy_sig else -1
            st["signals_buy" if d == 1 else "signals_sell"] += 1
            retest = bull_retest if d == 1 else bear_retest
            if d == 1:
                cand = (ll[i - 1] if i > 0 else NAN) if not p.use_candle_sl else (
                    (ll[i - 1] if i > 0 else NAN) if b_eng[i] else (ll[i] if (b_maru[i] or b_ham[i]) else (ll[i - 1] if i > 0 else NAN)))
                candR = cl_[i] - cand
            else:
                cand = (hl[i - 1] if i > 0 else NAN) if not p.use_candle_sl else (
                    (hl[i - 1] if i > 0 else NAN) if s_eng[i] else (hl[i] if (s_maru[i] or s_star[i]) else (hl[i - 1] if i > 0 else NAN)))
                candR = cand - cl_[i]
            if (not math.isnan(cand)) and candR >= p.mintick:
                last_bar = (i + 1 >= n) or day_l[i + 1] != day_l[i]
                if p.eod_flat and last_bar:
                    st["skipped_eod"] += 1                      # entry on the day's last bar would be flat at once
                else:
                    active, side, R, sig_bar, entry_bar = True, d, candR, i, i
                    sl = cand
                    base = cl_[i]
                    tg = [base + d * R * k for k in (1, 2, 3, 4)]
                    entry = base
                    done = [False] * 4
                    remain, parts, cur_sl, mae, mfe = 1.0, [], sl, 0.0, 0.0
                    ent_type, ent_day = ("RETEST" if retest else "CONT"), day_l[i]
                    fill_pending = p.fill == "next_open"
            else:
                st["no_trade_small_R"] += 1
        # ---- 12 management (trade entered on an EARLIER bar) ----
        if active and i > entry_bar:
            if fill_pending:                                   # realistic fill at this bar's open
                if day_l[i] != ent_day and p.eod_flat:
                    active = False; st["skipped_eod"] += 1
                    fill_pending = False
                else:
                    fpx = ol[i]
                    if (side == 1 and fpx <= sl) or (side == -1 and fpx >= sl):
                        active = False; st["skipped_gap_fill"] += 1
                        fill_pending = False
                    else:
                        entry = fpx; fill_pending = False
            if active:
                lo, hi, op = ll[i], hl[i], ol[i]
                mae = min(mae, side * ((lo if side == 1 else hi) - entry))
                mfe = max(mfe, side * ((hi if side == 1 else lo) - entry))
                hit_sl = (lo <= cur_sl) if side == 1 else (hi >= cur_sl)
                if hit_sl:
                    px = cur_sl
                    if p.gap_fills:
                        px = min(op, cur_sl) if side == 1 else max(op, cur_sl)
                    parts.append((remain, px))
                    close_trade(i, "BE" if (cur_sl == entry and any(done)) else "SL", parts, sum(done))
                else:
                    ended = False
                    for k in range(4):
                        if done[k]:
                            continue
                        reached = (hi >= tg[k]) if side == 1 else (lo <= tg[k])
                        if not reached:
                            break                              # targets are ordered; a farther one cannot be reached first
                        done[k] = True
                        px = tg[k]
                        if p.gap_fills:
                            px = max(op, tg[k]) if side == 1 else min(op, tg[k])
                        m = p.exit_model
                        if m == "PINE":
                            if k == 3:
                                parts.append((remain, px)); close_trade(i, "T4", parts, 4); ended = True
                        elif m in ("T1", "T2", "T3", "T4"):
                            if k + 1 == int(m[1]):
                                parts.append((remain, px)); close_trade(i, m, parts, k + 1); ended = True
                        else:                                  # SCALE / SCALE_BE: equal quarters at T1..T4
                            q = min(0.25, remain)
                            parts.append((q, px)); remain -= q
                            if k == 0 and m == "SCALE_BE":
                                cur_sl = entry
                            if k == 3 or remain <= 1e-9:
                                close_trade(i, "T4", parts, 4); ended = True
                        if ended:
                            break
                    if not ended and p.eod_flat and ((i + 1 >= n) or day_l[i + 1] != day_l[i]):
                        parts.append((remain, cl_[i])); close_trade(i, "EOD", parts, sum(done))
    if active and n:                                          # data ended with a position open
        if fill_pending:
            active = False
        else:
            parts.append((remain, cl_[n - 1])); close_trade(n - 1, "EOD_DATA", parts, sum(done))
    st["trades"] = len(trades)
    return trades, st
