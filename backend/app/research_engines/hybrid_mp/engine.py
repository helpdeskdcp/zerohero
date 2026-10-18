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
from .smc import SMCParams, smc_context

NAN = float("nan")


@dataclass
class Params:
    # --- Pine inputs (defaults identical to the script) ---
    pivot_len: int = 5
    atr_len: int = 14
    retest_bars: int = 8
    retest_tol_atr: float = 0.20
    use_vwap: bool = False
    vwap_missing_blocks: bool = False          # Pine parity = False: a missing VWAP (no volume) silently lets the trade through; True = block instead
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
    pivot_ties: str = "tv"                     # "tv" = TradingView rule (left ties allowed, right ties NOT); "allow"/"strict" kept for comparison
    # --- execution assumptions (NOT in Pine) ---
    chart_tf: int = 1                          # chart timeframe in minutes (bars must already be this timeframe)
    exit_model: str = "PINE"
    fill: str = "close"
    gap_fills: bool = False
    eod_flat: bool = False
    weekdays_only: bool = True
    # --- corrections of the reviewed flaws (all default OFF = literal Pine behaviour) ---
    swing_used_flag: bool = False              # each swing level can be broken once (until a new pivot forms)
    opposite_replaces_pending: bool = False    # a reversal break replaces an open pending setup instead of being ignored
    min_risk_atr: float | None = None          # skip entries whose stop distance is < x * ATR (cost-dominated micro stops)
    max_risk_atr: float | None = None          # skip entries whose stop distance is > x * ATR (gap-candle stops)
    no_entry_first_min: int = 0                # no NEW entries in the first N minutes of the session
    no_entry_last_min: int = 0                 # ... and in the last N minutes
    max_trades_day: int | None = None
    daily_loss_r: float | None = None          # stop trading for the day once gross day R <= -x
    favorable_first: bool = False              # SENSITIVITY ONLY: when a bar touches both the stop and T4, credit T4 (default False = adverse-first)
    target_mults: tuple = (1, 2, 3, 4)          # T1..T4 distances in units of the risk R (Pine = 1,2,3,4); a research knob for the win-rate/payoff frontier
    mtf_mode: str = "pine"                     # "pine" (colour fallback, noisy) | "state" (carry last breakout direction)
    vwap_proxy: bool = False                   # no volume (index): use session mean of typical price (TWAP proxy), flagged
    pointvalue: float = 1.0
    capital: float = 12000.0
    risk_pct: float = 1.0
    lot_size: int | None = None
    # --- optional SMC confirmation (smc.py) ---
    smc_filter: bool = False
    smc_min: int = 2
    smc_require: tuple = ()                    # components that must ALL be true (sweep, inducement, displacement, fvg_or_ob_touch, discount_premium); overrides smc_min
    smc: SMCParams = field(default_factory=SMCParams)


def fixed_params(**kw) -> "Params":
    """The corrected version. Choices were made from the review BEFORE any result of this version was seen:
    executable fills, hard intraday exit, partials with break-even after T1, bounded stop distance, one break per swing,
    reversal replaces pending, no entries in the first 5 / last 15 minutes, 5 trades and -3R per day max,
    MTF as a trend STATE (not candle colour) with >=3 of 4 agreeing, VWAP filter on (TWAP proxy when volume is missing)."""
    base = dict(fill="next_open", gap_fills=True, eod_flat=True, exit_model="SCALE_BE", swing_used_flag=True,
                opposite_replaces_pending=True, min_risk_atr=0.5, max_risk_atr=3.0, no_entry_first_min=5, no_entry_last_min=15,
                max_trades_day=5, daily_loss_r=3.0, mtf_mode="state", require_mtf=True, min_agree=3, use_vwap=True,
                vwap_proxy=True)
    base.update(kw)
    return Params(**base)


def corrected_params(**kw) -> "Params":
    """fixed_params + the COST-AWARE STOP FILTER found by the 2026-10-18 failure analysis (scripts/hybrid_mp_failure_analysis.py, hybrid_mp_cost_filter_test.py):
    the corrected engine earned only +0.9 bps gross per trade against a 2 bps round-trip cost because 2/3 of its trades had stops of 7-15 bps (cost = 0.14-0.27 R)
    and zero gross edge; only wide-stop trades carry gross edge (+2.7 bps). Raising min_risk_atr from 0.5 to 2.0 (max 3.0) moves net per trade monotonically:
    NIFTY TRAIN -1.1 -> +3.1 bps, VALID -1.4 -> +0.4, 2024+ -1.1 -> +2.9; replicated with unchanged parameters on SENSEX (+1.75 bps, p=0.03) and BANKNIFTY (+0.15).
    EVIDENCE IS WEAK (VALID p=0.38, ~400 trades per period) and the 2024+ period is not independent for this idea: research-only, NOT promoted, forward test pending."""
    base = dict(min_risk_atr=2.0, max_risk_atr=3.0)
    base.update(kw)
    return fixed_params(**base)


def highwin_params(**kw) -> "Params":
    """HIGH-WIN-RATE profile (measured, executable, no look-ahead): corrected entries + ONE exit at T1 = 0.4 R (stop = candle stop 1 R). NIFTY 5m 2017-2026,
    2,135 trades: win rate 71-72% (74% / 67% / 71.5% in TRAIN / VALID / 2024+) -- BUT every win is small (+8..10 bps) and every loss large (-24..-25 bps), so the
    expectancy is NEGATIVE at any realistic cost: +1.0 bps gross, 0.0 at 1 bps cost, -1.0 at 2 bps, -3.0 at 4 bps (profit factor 0.86 / 0.61). The break-even win rate
    for this payoff is ~75%, the strategy delivers 71%. It exists so the high-win-rate request is implemented for real and can be inspected; it LOSES MONEY after costs.
    The profitable profile is corrected_params() (53% win, +2.4 bps at 2 bps cost). A high win rate is not an edge: see scripts/hybrid_mp_winrate_frontier.py."""
    base = dict(exit_model="T1", target_mults=(0.4, 0.8, 1.2, 1.6))
    base.update(kw)
    return corrected_params(**base)


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
        carry = 0
        for k in range(hh):
            hmax = hi[k - L:k].max() if k >= L else NAN
            lmin = lo[k - L:k].min() if k >= L else NAN
            if not math.isnan(hmax) and cl[k] > hmax:
                d[k] = 1
            elif not math.isnan(lmin) and cl[k] < lmin:
                d[k] = -1
            elif p.mtf_mode == "state":
                d[k] = carry                                  # no breakout: keep the last breakout direction (a trend state)
            elif cl[k] > op[k]:
                d[k] = 1
            elif cl[k] < op[k]:
                d[k] = -1
            carry = int(d[k]) if d[k] != 0 else carry
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
    bull_ok = (c > o) & ((not p.use_pattern_filter) | bull_pat)
    bear_ok = (c < o) & ((not p.use_pattern_filter) | bear_pat)
    L = p.continuation_lookback
    hhL, llL = np.full(n, NAN), np.full(n, NAN)
    if n > L:
        hhL[L:] = sliding_window_view(h, L).max(axis=1)[:n - L]
        llL[L:] = sliding_window_view(l, L).min(axis=1)[:n - L]
    return dict(bull_eng=bull_eng, bear_eng=bear_eng, bull_maru=bull_maru, bear_maru=bear_maru, bull_ham=bull_ham,
                bear_star=bear_star, bull_ok=bull_ok, bear_ok=bear_ok, hhL=hhL, llL=llL)


def round_trip_cost_bps(commission_bps_per_side: float = 0.0, slippage_bps_per_side: float = 0.0, spread_bps: float = 0.0) -> float:
    """Round-trip cost in bps of price: both sides pay commission + slippage, the bid-ask spread is crossed once (half on entry, half on exit)."""
    return 2.0 * (commission_bps_per_side + slippage_bps_per_side) + spread_bps


def weekday_ist(t) -> np.ndarray:
    """Mon=0 .. Sun=6 for epoch seconds in IST (1970-01-01 was a Thursday, i.e. weekday 3)."""
    return (((np.asarray(t) + 19800) // 86400) + 3) % 7


def pivots_tv(a: np.ndarray, n: int, high: bool) -> np.ndarray:
    """ta.pivothigh/pivotlow with TradingView's tie rule (confirmed from the TradingView-validated PyneCore implementation, which keeps
    the NEWEST of equal extremes): pivot high iff v >= every LEFT bar and v > every RIGHT bar (low: <= left, < right).
    out[i] = pivot value of bar i-n, known at bar i (nan otherwise)."""
    out = np.full(len(a), NAN)
    if len(a) < 2 * n + 1:
        return out
    w = sliding_window_view(a, 2 * n + 1)
    v, left, right = w[:, n], w[:, :n], w[:, n + 1:]
    ok = (v >= left.max(axis=1)) & (v > right.max(axis=1)) if high else (v <= left.min(axis=1)) & (v < right.min(axis=1))
    out[2 * n:] = np.where(ok, v, NAN)
    return out


def vol_usable(chart: dict) -> bool:
    """Index data carries almost no volume: a VWAP from the few non-zero bars would be garbage, so require >=50% positive bars."""
    v = chart.get("v")
    return v is not None and len(v) > 0 and float(np.mean(np.asarray(v) > 0)) >= 0.5


def _vwap(chart: dict, days: np.ndarray, proxy: bool = False) -> np.ndarray:
    v = chart.get("v")
    n = len(days)
    hlc3 = (chart["h"] + chart["l"] + chart["c"]) / 3.0
    if not vol_usable(chart):
        if not proxy:
            return np.full(n, NAN)
        v = np.ones(n)                                       # TWAP proxy: equal weights (flagged in the dashboard)
    out = np.full(n, NAN)
    start = 0
    for i in range(1, n + 1):
        if i == n or days[i] != days[start]:
            pv, vv = np.cumsum(hlc3[start:i] * v[start:i]), np.cumsum(v[start:i])
            with np.errstate(invalid="ignore", divide="ignore"):
                out[start:i] = np.where(vv > 0, pv / vv, NAN)
            start = i
    return out


def run(chart: dict, p: Params | None = None, b1: dict | None = None, want_state: bool = False, want_events: bool = False) -> tuple[list[HTrade], dict]:
    """chart: bars at p.chart_tf (t = bar OPEN epoch). b1: 1-minute bars (only needed when require_mtf)."""
    p = p or Params()
    t, o, h, l, c = (chart[k] for k in ("t", "o", "h", "l", "c"))
    n = len(t)
    days, mod = ist_minutes(t)
    wd = weekday_ist(t)
    sess_ok = np.ones(n, dtype=bool) if not p.use_session else ((mod >= p.session[0]) & (mod < p.session[1]) &
                                                                  ((wd < 5) if p.weekdays_only else True))
    F = _flags(chart, p)
    atr = _atr(h, l, c, p.atr_len)
    if p.pivot_ties == "tv":
        ph, pl = pivots_tv(h, p.pivot_len, True), pivots_tv(l, p.pivot_len, False)
    else:
        ph, pl = _pivots(h, p.pivot_len, True, p.pivot_ties), _pivots(l, p.pivot_len, False, p.pivot_ties)
    vwap = _vwap(chart, days, p.vwap_proxy)
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
    EV = [] if want_events else None            # Pine-semantic event log used by the parity audit
    smc = smc_context(chart, p.smc) if (p.smc_filter or want_state) else None
    sbull = smc["bull_score"].tolist() if smc else [0] * n
    sbear = smc["bear_score"].tolist() if smc else [0] * n
    SMC_COMPONENTS = ("sweep", "inducement", "displacement", "fvg_or_ob_touch", "discount_premium")
    req_idx = [SMC_COMPONENTS.index(x) for x in p.smc_require]
    comp_b = smc["comp_bull"].tolist() if smc else None
    comp_s = smc["comp_bear"].tolist() if smc else None

    swingH = swingL = NAN
    hi_used = lo_used = False
    structure = 0
    day_trades, day_r = 0, 0.0
    last_result, last_setup, last_block, last_dir, last_type, last_setup_bar = "NO CLOSED TRADE", "NO SETUP YET", "WAITING FOR BREAKOUT", 0, "NONE", -1
    prior_close, today_open = NAN, NAN
    pend_dir, pend_level, pend_bar = 0, NAN, -1
    active, side, entry, sl, R = False, 0, NAN, NAN, NAN
    tg = [NAN] * 4
    done = [False] * 4
    entry_bar, sig_bar, fill_pending = -1, -1, False
    remain, parts, cur_sl, mae, mfe, ent_type, ent_day = 0.0, [], NAN, 0.0, 0.0, "", 0
    trades: list[HTrade] = []
    st = dict(bars=n, signals_buy=0, signals_sell=0, rejected=0, no_trade_small_R=0, expired=0, cancelled_session=0,
              pending_set=0, skipped_gap_fill=0, skipped_eod=0, skipped_risk=0, blocked_limits=0, blocked_smc=0, blocked_window=0, breaks_up=0, breaks_dn=0, choch=0, bos=0)

    def close_trade(j, reason, px_parts, max_t):
        nonlocal active
        pnl = sum(w * side * (px - entry) for w, px in px_parts)
        trades.append(HTrade(sig_bar, entry_bar + (1 if p.fill == "next_open" else 0), j, side, entry, px_parts[-1][1], R, pnl,
                             pnl / R, 1e4 * pnl / entry, mae, mfe, reason, int(ent_day), ent_type, tuple(px_parts),
                             max_t=max_t, overnight_days=int(day_l[j] - ent_day), entry_type=ent_type))
        active = False
        nonlocal day_r, last_result
        day_r += pnl / R
        last_result = "STOPPED" if reason in ("SL", "BE") else ("T4 COMPLETE" if reason == "T4" else reason)

    for i in range(n):
        sess = sess_l[i]
        newday = i == 0 or day_l[i] != day_l[i - 1]
        if newday:
            day_trades, day_r = 0, 0.0
            prior_close, today_open = (cl_[i - 1] if i > 0 else NAN), ol[i]
        # ---- 04 structure ----
        if not math.isnan(phl[i]):
            swingH = phl[i]; hi_used = False
        if not math.isnan(pll[i]):
            swingL = pll[i]; lo_used = False
        prev_c = cl_[i - 1] if i > 0 else NAN
        break_h = (not math.isnan(swingH)) and cl_[i] > swingH and (not math.isnan(prev_c)) and prev_c <= swingH and not (p.swing_used_flag and hi_used)
        break_l = (not math.isnan(swingL)) and cl_[i] < swingL and (not math.isnan(prev_c)) and prev_c >= swingL and not (p.swing_used_flag and lo_used)
        bull_ch, bear_ch = break_h and structure == -1, break_l and structure == 1
        if break_h:
            structure = 1; hi_used = True; st["breaks_up"] += 1
        if break_l:
            structure = -1; lo_used = True; st["breaks_dn"] += 1
        if break_h or break_l:
            st["choch" if (bull_ch or bear_ch) else "bos"] += 1
            if EV is not None:
                EV.append((i, "CHOCH" if (bull_ch or bear_ch) else "BOS", 1 if break_h else -1))
        allow_buy, allow_sell = structure == 1, structure == -1
        # ---- 06 / 08 pending housekeeping ----
        if newday and not active:
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        if pend_dir != 0 and pend_bar >= 0 and i - pend_bar > p.retest_bars:
            st["expired"] += 1; last_setup, last_block = "SETUP EXPIRED", "RETEST WINDOW EXPIRED"
            if EV is not None:
                EV.append((i, "PENDING_EXPIRED", pend_dir))
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        if pend_dir != 0 and not sess:
            st["cancelled_session"] += 1; last_setup, last_block = "SETUP CANCELLED", "SESSION CLOSED"
            if EV is not None:
                EV.append((i, "PENDING_CANCELLED", pend_dir))
            pend_dir, pend_level, pend_bar = 0, NAN, -1
        new_up = sess and break_h and not active and (pend_dir == 0 or (p.opposite_replaces_pending and pend_dir == -1))
        new_dn = sess and break_l and not active and (pend_dir == 0 or (p.opposite_replaces_pending and pend_dir == 1))
        if new_up:
            pend_dir, pend_level, pend_bar = 1, swingH, i; st["pending_set"] += 1
            if EV is not None:
                EV.append((i, "PENDING_SET", 1, swingH))
            last_dir, last_type, last_setup, last_block, last_setup_bar = 1, "BREAKOUT", "WAITING FOR BUY RETEST", "RETEST NOT CONFIRMED", i
        if new_dn:
            pend_dir, pend_level, pend_bar = -1, swingL, i; st["pending_set"] += 1
            if EV is not None:
                EV.append((i, "PENDING_SET", -1, swingL))
            last_dir, last_type, last_setup, last_block, last_setup_bar = -1, "BREAKOUT", "WAITING FOR SELL RETEST", "RETEST NOT CONFIRMED", i
        tol = 0.0 if math.isnan(L_[i]) else L_[i] * p.retest_tol_atr
        bull_retest = pend_dir == 1 and i > pend_bar and ll[i] <= pend_level + tol and cl_[i] > pend_level and bull_ok[i]
        bear_retest = pend_dir == -1 and i > pend_bar and hl[i] >= pend_level - tol and cl_[i] < pend_level and bear_ok[i]
        bull_cont = hybrid and (not math.isnan(hhL[i])) and cl_[i] > hhL[i] and bull_ok[i]
        bear_cont = hybrid and (not math.isnan(llL[i])) and cl_[i] < llL[i] and bear_ok[i]
        vw_i = vw[i]
        vw_ok_missing = not p.vwap_missing_blocks
        vwap_long = (not p.use_vwap) or (math.isnan(vw_i) and vw_ok_missing) or (not math.isnan(vw_i) and cl_[i] >= vw_i)
        vwap_short = (not p.use_vwap) or (math.isnan(vw_i) and vw_ok_missing) or (not math.isnan(vw_i) and cl_[i] <= vw_i)
        mtf_long = (not p.require_mtf) or ba[i] >= p.min_agree
        mtf_short = (not p.require_mtf) or sa[i] >= p.min_agree
        if p.smc_filter and p.smc_require:
            smc_long = all(comp_b[i][k] for k in req_idx)
            smc_short = all(comp_s[i][k] for k in req_idx)
        else:
            smc_long = (not p.smc_filter) or sbull[i] >= p.smc_min
            smc_short = (not p.smc_filter) or sbear[i] >= p.smc_min
        buy_ok, sell_ok = vwap_long and mtf_long and smc_long, vwap_short and mtf_short and smc_short
        win_ok = (mod_l[i] >= p.session[0] + p.no_entry_first_min) and (mod_l[i] + p.chart_tf <= p.session[1] - p.no_entry_last_min)
        lim_ok = (p.max_trades_day is None or day_trades < p.max_trades_day) and (p.daily_loss_r is None or day_r > -p.daily_loss_r)
        gate = win_ok and lim_ok
        buy_raw = sess and not active and allow_buy and (bull_retest or bull_cont) and buy_ok
        sell_raw = sess and not active and allow_sell and (bear_retest or bear_cont) and sell_ok
        if (buy_raw or sell_raw) and not gate:
            st["blocked_window" if not win_ok else "blocked_limits"] += 1
        if sess and not active and ((allow_buy and (bull_retest or bull_cont) and vwap_long and mtf_long and not smc_long) or
                                    (allow_sell and (bear_retest or bear_cont) and vwap_short and mtf_short and not smc_short)):
            st["blocked_smc"] += 1
        buy_sig = buy_raw and gate
        sell_sig = sell_raw and gate
        buy_rej = bull_retest and (not allow_buy or not buy_ok)
        sell_rej = bear_retest and (not allow_sell or not sell_ok)
        if buy_rej or sell_rej:
            st["rejected"] += 1
            if EV is not None:
                EV.append((i, "REJECTED", 1 if buy_rej else -1))
            last_dir, last_type = (1 if buy_rej else -1), "RETEST"
            last_setup = "BUY REJECTED" if buy_rej else "SELL REJECTED"
            last_block = ("STRUCTURE TREND NOT UP" if not allow_buy else "PRICE BELOW VWAP" if not vwap_long else "MTF AGREEMENT FAILED" if not mtf_long else "SMC CONFIRMATION FAILED") if buy_rej else ("STRUCTURE TREND NOT DOWN" if not allow_sell else "PRICE ABOVE VWAP" if not vwap_short else "MTF AGREEMENT FAILED" if not mtf_short else "SMC CONFIRMATION FAILED")
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
            risk_ok = True
            if (not math.isnan(cand)) and candR >= p.mintick:
                a0 = L_[i]
                if p.min_risk_atr is not None and (math.isnan(a0) or candR < p.min_risk_atr * a0):
                    risk_ok = False
                if p.max_risk_atr is not None and (math.isnan(a0) or candR > p.max_risk_atr * a0):
                    risk_ok = False
            if not risk_ok:
                st["skipped_risk"] += 1
            elif (not math.isnan(cand)) and candR >= p.mintick:
                last_bar = mod_l[i] + p.chart_tf >= p.session[1]        # clock rule: no peeking at bar i+1
                if p.eod_flat and last_bar:
                    st["skipped_eod"] += 1                      # entry on the day's last bar would be flat at once
                else:
                    active, side, R, sig_bar, entry_bar = True, d, candR, i, i
                    sl = cand
                    base = cl_[i]
                    tg = [base + d * R * k for k in p.target_mults]
                    entry = base
                    done = [False] * 4
                    remain, parts, cur_sl, mae, mfe = 1.0, [], sl, 0.0, 0.0
                    ent_type, ent_day = ("RETEST" if retest else "CONT"), day_l[i]
                    day_trades += 1
                    if EV is not None:
                        EV.append((i, "SIGNAL", d, ent_type, sl, candR))
                    last_dir, last_type, last_setup, last_block, last_setup_bar = d, ent_type + " ENTRY", "BUY CONFIRMED" if d == 1 else "SELL CONFIRMED", "NONE", i
                    last_result = "BUY ACTIVE" if d == 1 else "SELL ACTIVE"
                    fill_pending = p.fill == "next_open"
            else:
                st["no_trade_small_R"] += 1
                if EV is not None:
                    EV.append((i, "SIGNAL_NO_TRADE", d))
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
            if active and p.eod_flat and not fill_pending and day_l[i] != ent_day:
                # the last bar(s) of the entry day are absent from the data: flatten at this bar's open (conservative, flagged)
                parts.append((remain, ol[i])); st["eod_missed"] = st.get("eod_missed", 0) + 1
                close_trade(i, "EOD_MISSED", parts, sum(done))
            if active:
                lo, hi, op = ll[i], hl[i], ol[i]
                mae = min(mae, side * ((lo if side == 1 else hi) - entry))
                mfe = max(mfe, side * ((hi if side == 1 else lo) - entry))
                hit_sl = (lo <= cur_sl) if side == 1 else (hi >= cur_sl)
                if hit_sl and p.favorable_first and p.exit_model == "PINE" and ((hi >= tg[3]) if side == 1 else (lo <= tg[3])):
                    hit_sl = False                                   # optimistic ordering (sensitivity test only)
                if hit_sl:
                    if EV is not None:
                        EV.append((i, "SL_HIT", side))
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
                        if EV is not None:
                            EV.append((i, f"T{k + 1}", side))
                        px = tg[k]
                        if p.gap_fills:
                            px = max(op, tg[k]) if side == 1 else min(op, tg[k])
                        m = p.exit_model
                        if m == "PINE":
                            if k == 3:
                                if EV is not None:
                                    EV.append((i, "T4_COMPLETE", side))
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
                    if not ended and p.eod_flat and mod_l[i] + p.chart_tf >= p.session[1]:
                        parts.append((remain, cl_[i])); close_trade(i, "EOD", parts, sum(done))
    if want_state and n:
        i = n - 1
        names = [f"{x}m" for x in p.mtf_tfs]
        dtxt = lambda d: "UP" if d == 1 else "DOWN" if d == -1 else "NEUTRAL"
        mrow = [int(x) for x in mt[i]] if p.require_mtf else [0] * len(names)
        direction = "UPSIDE" if structure == 1 else "DOWNSIDE" if structure == -1 else "NEUTRAL"
        c1, c2 = (cl_[i - 1] if i > 0 else NAN), (cl_[i - 2] if i > 1 else NAN)
        weakening = (structure == 1 and cl_[i] < c1 and c1 < c2) or (structure == -1 and cl_[i] > c1 and c1 > c2)
        rev_conf = bool(bull_ch or bear_ch)
        rev_watch = (not rev_conf) and ((structure == 1 and i > 0 and cl_[i] < ll[i - 1]) or (structure == -1 and i > 0 and cl_[i] > hl[i - 1]))
        trend_status = ("REVERSAL CONFIRMED" if rev_conf else "REVERSAL WATCH" if rev_watch else "TREND WEAKENING" if weakening else
                        "UPSIDE CONTINUATION" if structure == 1 else "DOWNSIDE CONTINUATION" if structure == -1 else "NEUTRAL")
        if not sess_l[i]:
            sig_block = "SESSION FILTER BLOCK"
        elif active:
            sig_block = "TRADE ALREADY ACTIVE"
        elif structure == 0:
            sig_block = "NEED STRUCTURE BREAK"
        elif buy_sig:
            sig_block = "BUY CONFIRMED"
        elif sell_sig:
            sig_block = "SELL CONFIRMED"
        elif buy_rej or sell_rej:
            sig_block = last_block
        elif pend_dir == 1:
            sig_block = "WAITING FOR BUY RETEST"
        elif pend_dir == -1:
            sig_block = "WAITING FOR SELL RETEST"
        elif not gate:
            sig_block = "ENTRY WINDOW CLOSED" if not win_ok else "DAILY LIMIT REACHED"
        else:
            sig_block = (("UPTREND: WAIT RETEST" if not hybrid else "UPTREND: WAIT FRESH BREAKOUT") if structure == 1 else
                         ("DOWNTREND: WAIT RETEST" if not hybrid else "DOWNTREND: WAIT FRESH BREAKOUT"))
        gp = (today_open - prior_close) / prior_close * 100.0 if (not math.isnan(prior_close) and prior_close and not math.isnan(today_open)) else NAN
        gap_text = "GAP N/A" if math.isnan(gp) else (f"GAP UP {gp:.2f}%" if gp > 0.10 else f"GAP DOWN {gp:.2f}%" if gp < -0.10 else "FLAT OPEN")
        tstat = lambda k: ("COMPLETED" if done[k] else "PENDING") if active else "—"
        nxt = next((f"T{k + 1}" for k in range(4) if not done[k]), "COMPLETE") if active else "—"
        qty = (p.capital * p.risk_pct / 100.0) / max(R * p.pointvalue, p.mintick) if active and not math.isnan(R) else NAN
        if qty == qty and p.lot_size:
            qty = (qty // p.lot_size) * p.lot_size
        z = smc["final"] if smc else {}
        st["state"] = dict(
            engine="HYBRID MP v13 (port)" + (" + FIXES" if p.swing_used_flag or p.eod_flat else ""), chart_tf=p.chart_tf,
            bar_time=int(t[i]), close=cl_[i],
            session="IN SESSION" if sess_l[i] else "OUT OF SESSION",
            mtf={names[k]: dtxt(mrow[k]) for k in range(len(names))}, mtf_bull_agree=int(ba[i]), mtf_bear_agree=int(sa[i]),
            mtf_required=bool(p.require_mtf), direction=direction, trend_status=trend_status,
            trade="BUY ACTIVE" if active and side == 1 else "SELL ACTIVE" if active else ("PENDING BUY" if pend_dir == 1 else "PENDING SELL" if pend_dir == -1 else "NO ACTIVE TRADE"),
            opening_gap=gap_text, signal_check=sig_block, next_target=nxt,
            targets={f"T{k + 1}": tstat(k) for k in range(4)},
            entry=entry if active else None, sl=cur_sl if active else None,
            levels=[tg[k] for k in range(4)] if active else None, last_result=last_result,
            planned_qty=None if qty != qty else qty, planned_qty_note="informational; ignores broker margin",
            vwap=None if math.isnan(vw[i]) else vw[i], vwap_is_proxy=bool(p.vwap_proxy and not vol_usable(chart)),
            vwap_filter=bool(p.use_vwap),
            pending=dict(direction="BUY" if pend_dir == 1 else "SELL" if pend_dir == -1 else "NONE",
                         level=None if math.isnan(pend_level) else pend_level,
                         age_bars=(i - pend_bar) if pend_dir != 0 and pend_bar >= 0 else None,
                         reason=("TRADE ACTIVE" if active else "OUT OF SESSION" if not sess_l[i] else "WAITING FOR BUY RETEST" if pend_dir == 1 else
                                 "WAITING FOR SELL RETEST" if pend_dir == -1 else "WAITING FOR NEW SETUP"),
                         last_setup=last_setup, last_block=last_block,
                         last_info=f"{'BUY' if last_dir == 1 else 'SELL' if last_dir == -1 else 'NONE'} | {last_type} | "
                                   f"{(i - last_setup_bar) if last_setup_bar >= 0 else '—'} bars ago"),
            today=dict(trades=day_trades, gross_r=round(day_r, 3), max_trades=p.max_trades_day, loss_limit_r=p.daily_loss_r),
            structure=dict(swing_high=None if math.isnan(swingH) else swingH, swing_low=None if math.isnan(swingL) else swingL,
                           choch_now=rev_conf, breaks_up=st["breaks_up"], breaks_dn=st["breaks_dn"]),
            smc=dict(enabled=bool(p.smc_filter), min_score=p.smc_min,
                     bull_score=int(sbull[i]), bear_score=int(sbear[i]),
                     components={nm: {"bull": bool(smc["comp_bull"][i][k]), "bear": bool(smc["comp_bear"][i][k])}
                                 for k, nm in enumerate(("sweep", "inducement", "displacement", "fvg_or_ob_touch", "discount_premium"))} if smc else {},
                     zone="DISCOUNT" if smc and bool(smc["discount"][i]) else "PREMIUM" if smc and bool(smc["premium"][i]) else "—",
                     equilibrium=None if not smc or math.isnan(smc["eq50"][i]) else float(smc["eq50"][i]),
                     dealing_range=None if not smc or math.isnan(smc["dealing_hi"][i]) else [float(smc["dealing_lo"][i]), float(smc["dealing_hi"][i])],
                     fvg_bull=z.get("fvg_bull", []), fvg_bear=z.get("fvg_bear", []), ob_bull=z.get("ob_bull", []), ob_bear=z.get("ob_bear", []),
                     buy_side_liquidity=z.get("buy_liquidity", []), sell_side_liquidity=z.get("sell_liquidity", [])))
    if active and n:                                          # data ended with a position open
        if fill_pending:
            active = False
        else:
            parts.append((remain, cl_[n - 1])); close_trade(n - 1, "EOD_DATA", parts, sum(done))
    st["trades"] = len(trades)
    st["mtf_incompatible_tfs"] = [N for N in p.mtf_tfs if N < p.chart_tf] if p.require_mtf else []     # requested TF lower than the chart TF: not TradingView-verified
    if EV is not None:
        st["events"] = EV
    return trades, st
