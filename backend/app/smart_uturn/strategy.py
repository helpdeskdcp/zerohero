"""Faithful port of 'Smart U-Turn Decision Engine v2.1 | Calibrated' (Pine v6). Section numbers refer to the Pine file (pine/smart_u_turn_v2_1.pine).
Every comparison with NaN is False (Pine: na -> false). HTF values follow `request.security(tf, f()[1], lookahead_on)`: the value of the PREVIOUS COMPLETED higher-timeframe bar,
constant over the chart bars of the current HTF bar (see mtf_values). Truncation-invariance (no look-ahead) is asserted in the tests."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from .config import PINE_TF_MAP, ConfigError, SignalParams


def turn_components(df: pd.DataFrame, p: SignalParams) -> dict:
    """Section 03 f_turn(): returns the components and the pulse series (1 bull / -1 bear / 0)."""
    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    v = df["volume"].to_numpy(float) if "volume" in df.columns else np.full(len(df), np.nan)
    if p.use_volume and (np.isnan(v).all() or float(np.nan_to_num(v).max()) == 0.0):
        raise ConfigError("use_volume=True but the source supplies no volume (not treated as zero)")
    ef, es, rv = ind.ema(c, p.fast_len), ind.ema(c, p.slow_len), ind.rsi(c, p.rsi_len)
    bd = np.maximum(np.abs(c - o), p.mintick); rg = np.maximum(h - l, p.mintick)
    lw = np.minimum(o, c) - l; uw = h - np.maximum(o, c)
    prior_low, prior_high = ind.lowest_prev(l, p.swing_len), ind.highest_prev(h, p.swing_len)
    vol_avg = ind.sma(v, p.volume_len)
    rv1, h1, l1, ef1 = ind.shift(rv), ind.shift(h), ind.shift(l), ind.shift(ef)
    with np.errstate(invalid="ignore"):
        bull_wick = (lw / bd >= p.wick_ratio) & (c > l + rg * 0.55); bear_wick = (uw / bd >= p.wick_ratio) & (c < h - rg * 0.55)
        bull_sweep = (l < prior_low) & (c > prior_low); bear_sweep = (h > prior_high) & (c < prior_high)
        bull_mom = (rv > rv1) & (rv1 < 50); bear_mom = (rv < rv1) & (rv1 > 50)
        bull_break = (c > h1) & (c > o); bear_break = (c < l1) & (c < o)
        bull_trend = (ef > es) & (ef > ef1); bear_trend = (ef < es) & (ef < ef1)
        volume_ok = np.ones(len(c), bool) if not p.use_volume else (np.isnan(vol_avg) | (v > vol_avg))
    bs = bull_wick.astype(int) + bull_sweep + bull_mom + bull_break + bull_trend
    ss = bear_wick.astype(int) + bear_sweep + bear_mom + bear_break + bear_trend
    bull_setup = (bs >= p.min_score) & (bull_wick | bull_sweep | bull_break) & volume_ok
    bear_setup = (ss >= p.min_score) & (bear_wick | bear_sweep | bear_break) & volume_ok
    prev_bull = np.concatenate([[False], bull_setup[:-1]]); prev_bear = np.concatenate([[False], bear_setup[:-1]])      # Pine: na[1] -> `not na` is true, so a first-bar setup pulses
    bull_pulse = bull_setup & ~prev_bull; bear_pulse = bear_setup & ~prev_bear
    turn = np.where(bull_pulse, 1, np.where(bear_pulse, -1, 0))
    return {"turn": turn, "bull_score": bs, "bear_score": ss, "bull_setup": bull_setup, "bear_setup": bear_setup, "ef": ef, "es": es, "rsi": rv,
            "bull_wick": bull_wick, "bear_wick": bear_wick, "bull_sweep": bull_sweep, "bear_sweep": bear_sweep, "bull_mom": bull_mom, "bear_mom": bear_mom,
            "bull_break": bull_break, "bear_break": bear_break, "bull_trend": bull_trend, "bear_trend": bear_trend}


def trend_series(df: pd.DataFrame, p: SignalParams) -> np.ndarray:
    """Section 03 f_trend(): +1 / -1 / 0."""
    c = df["close"].to_numpy(float); ef, es, rv = ind.ema(c, p.fast_len), ind.ema(c, p.slow_len), ind.rsi(c, p.rsi_len); ef1 = ind.shift(ef)
    with np.errstate(invalid="ignore"):
        return np.where((ef > es) & (ef > ef1) & (rv > 50), 1, np.where((ef < es) & (ef < ef1) & (rv < 50), -1, 0))


def mtf_values(chart_open_times, htf_bars: pd.DataFrame, series: np.ndarray) -> np.ndarray:
    """Value of `series` (aligned to htf_bars) at the PREVIOUS completed HTF bar, for each chart bar.
    The HTF bar containing chart time T is the last one that OPENED at or before T; the value used is the one before it, whose close precedes that bar's open, hence precedes T (no look-ahead).
    No previous bar -> 0 (neutral)."""
    ho = htf_bars.index.view("int64") if hasattr(htf_bars.index, "view") else htf_bars.index.asi8
    t = pd.DatetimeIndex(chart_open_times).asi8
    k = np.searchsorted(ho, t, side="right") - 1; prev = k - 1
    out = np.zeros(len(t), dtype=int); ok = prev >= 0
    out[ok] = np.asarray(series)[prev[ok]]
    return out


def htf_labels(chart_tf: str) -> tuple[str, str, str]:
    return PINE_TF_MAP.get(chart_tf, ("", "", ""))


def gate_signals(turn, mtf_dir, p: SignalParams):
    """Section 06: strict-trend gate + independent directional cooldowns. lastBuyBar/lastSellBar move ONLY on an emitted signal (not on a blocked or cooled-down candidate)."""
    n = len(turn); buy = np.zeros(n, bool); sell = np.zeros(n, bool); bb = np.zeros(n, bool); bs = np.zeros(n, bool); last_buy = last_sell = None
    for i in range(n):
        bc, sc = turn[i] == 1, turn[i] == -1
        b_ok = (not p.strict_trend) or mtf_dir[i] != -1; s_ok = (not p.strict_trend) or mtf_dir[i] != 1
        b_cd = last_buy is None or i - last_buy > p.cooldown_bars; s_cd = last_sell is None or i - last_sell > p.cooldown_bars
        buy[i] = bc and b_ok and b_cd; sell[i] = sc and s_ok and s_cd
        bb[i] = bc and p.strict_trend and mtf_dir[i] == -1; bs[i] = sc and p.strict_trend and mtf_dir[i] == 1
        if buy[i]: last_buy = i
        if sell[i]: last_sell = i
    return buy, sell, bb, bs


def run(chart: pd.DataFrame, chart_tf: str, htf_bars: dict[str, pd.DataFrame], p: SignalParams) -> pd.DataFrame:
    """Sections 03-09. `chart`: bars of the chart timeframe; `htf_bars`: {tf: bars} for tfA/tfB/tfC (built by the CandleStore, never separately fetched). Returns one row per chart bar."""
    p.validate(); n = len(chart); idx = chart.index
    tfa, tfb, tfc = htf_labels(chart_tf)
    comp = turn_components(chart, p)
    sig, trd = {}, {}
    for name, tf in (("A", tfa), ("B", tfb), ("C", tfc)):
        if tf == "": sig[name] = np.zeros(n, int); trd[name] = np.zeros(n, int); continue
        hb = htf_bars[tf]
        sig[name] = mtf_values(idx, hb, turn_components(hb, p)["turn"]); trd[name] = mtf_values(idx, hb, trend_series(hb, p))
    ev = {k: (v != 0) & (v != np.concatenate([[0], v[:-1]])) for k, v in sig.items()}
    up = sum((trd[k] == 1).astype(int) for k in "ABC"); dn = sum((trd[k] == -1).astype(int) for k in "ABC")
    mtf_dir = np.where((up >= p.min_trend_votes) & (up > dn), 1, np.where((dn >= p.min_trend_votes) & (dn > up), -1, 0))
    h, l, c = (chart[k].to_numpy(float) for k in ("high", "low", "close"))
    atr = ind.atr(h, l, c, p.atr_len); swing_low, swing_high = ind.lowest(l, p.swing_len), ind.highest(h, p.swing_len)
    turn = comp["turn"]
    buy, sell, blocked_buy, blocked_sell = gate_signals(turn, mtf_dir, p)
    active_dir = np.zeros(n, int); entry = np.full(n, np.nan); stop = entry.copy(); risk = entry.copy(); t1 = entry.copy(); t2 = entry.copy(); t3 = entry.copy(); sup = entry.copy(); res = entry.copy()
    call_l: list = []; call_h: list = []
    cur = dict(d=0, e=np.nan, s=np.nan, r=np.nan, a=np.nan, b=np.nan, c=np.nan, sup=np.nan, res=np.nan); new_levels = np.zeros(n, bool)
    for i in range(n):
        if (buy[i] or sell[i]) and not np.isnan(atr[i]):
            d = 1 if buy[i] else -1; e = c[i]
            if p.swing_window == "BARS": sl_, sh_ = swing_low[i], swing_high[i]
            else:                                                       # Pine: ta.lowest/highest sit INSIDE this `if`, so their window is the last swingLen EXECUTIONS (signal bars), not the last swingLen candles
                call_l.append(l[i]); call_h.append(h[i])
                sl_ = min(call_l[-p.swing_len:]) if len(call_l) >= p.swing_len else np.nan; sh_ = max(call_h[-p.swing_len:]) if len(call_h) >= p.swing_len else np.nan
            min_dist = max(atr[i] * p.min_risk_atr, p.mintick * 2)
            raw = (sl_ - atr[i] * p.sl_buffer) if d == 1 else (sh_ + atr[i] * p.sl_buffer)
            st = np.nan if np.isnan(raw) else (min(raw, e - min_dist) if d == 1 else max(raw, e + min_dist)); rk = abs(e - st)         # Pine math.min/max with na -> na (only before swingLen signals exist)
            cur = dict(d=d, e=e, s=st, r=rk, a=e + d * rk * p.rr1, b=e + d * rk * p.rr2, c=e + d * rk * p.rr3, sup=sl_, res=sh_); new_levels[i] = True
        active_dir[i] = cur["d"]; entry[i] = cur["e"]; stop[i] = cur["s"]; risk[i] = cur["r"]; t1[i], t2[i], t3[i] = cur["a"], cur["b"], cur["c"]; sup[i] = cur["sup"]; res[i] = cur["res"]
    # section 08 structure (support/resistance are the levels frozen at the last signal)
    c1 = ind.shift(c)
    with np.errstate(invalid="ignore"):
        bull_breakout = ~np.isnan(res) & (c > res) & (c1 <= res); bear_breakout = ~np.isnan(sup) & (c < sup) & (c1 >= sup)
        bull_retest = ~np.isnan(res) & (l <= res) & (c > res); bear_retest = ~np.isnan(sup) & (h >= sup) & (c < sup)
    state = np.where(bull_retest, "BULL RETEST", np.where(bear_retest, "BEAR RETEST", np.where(bull_breakout, "BULL BREAKOUT", np.where(bear_breakout, "BEAR BREAKOUT", "WAIT / RANGE"))))
    # section 09 potential liquidity (confirmed pivots only)
    ph, pl = ind.pivot_high(h, p.swing_len, p.swing_len), ind.pivot_low(l, p.swing_len, p.swing_len)
    ph0, ph1, ph2 = (ind.valuewhen_recent(ph, k) for k in (0, 1, 2)); pl0, pl1, pl2 = (ind.valuewhen_recent(pl, k) for k in (0, 1, 2))
    long_ = active_dir == 1
    out = pd.DataFrame({"turn": turn, "bull_score": comp["bull_score"], "bear_score": comp["bear_score"], "sigA": sig["A"], "sigB": sig["B"], "sigC": sig["C"], "trA": trd["A"], "trB": trd["B"], "trC": trd["C"],
                        "evA": ev["A"] & p.show_a, "evB": ev["B"] & p.show_b, "evC": ev["C"] & p.show_c, "up_votes": up, "dn_votes": dn, "mtf_dir": mtf_dir,
                        "buy_signal": buy, "sell_signal": sell, "blocked_buy": blocked_buy, "blocked_sell": blocked_sell, "new_levels": new_levels, "active_dir": active_dir,
                        "entry": entry, "stop": stop, "risk": risk, "t1": t1, "t2": t2, "t3": t3, "support": sup, "resistance": res, "atr": atr,
                        "bull_breakout": bull_breakout, "bear_breakout": bear_breakout, "bull_retest": bull_retest, "bear_retest": bear_retest, "structure": state,
                        "hunt1": np.where(long_, pl0, ph0), "hunt2": np.where(long_, pl1, ph1), "hunt3": np.where(long_, pl2, ph2)}, index=idx)
    out.attrs.update(tfs=(tfa, tfb, tfc), chart_tf=chart_tf)
    return out


def decision_text(mtf_dir: int) -> str:
    return "BULL TREND" if mtf_dir == 1 else "BEAR TREND" if mtf_dir == -1 else "MIXED / WAIT"


def dashboard(sig: pd.DataFrame) -> dict:
    """Section 10 dashboard data for the last bar (data only, no drawing)."""
    r = sig.iloc[-1]; names = {1: "UP TURN", -1: "DOWN TURN", 0: "NO NEW TURN"}; tr = {1: "BULLISH", -1: "BEARISH", 0: "NEUTRAL"}
    return {"chart_tf": sig.attrs.get("chart_tf"), "signal": "BUY ACTIVE" if r.active_dir == 1 else "SELL ACTIVE" if r.active_dir == -1 else "WAIT", "trend": tr[int(r.mtf_dir)], "decision": decision_text(int(r.mtf_dir)),
            "tf": dict(zip(("A", "B", "C"), sig.attrs.get("tfs", ()))), "turns": {k: names[int(r["sig" + k])] for k in "ABC"}, "trends": {k: tr[int(r["tr" + k])] for k in "ABC"},
            "votes": {"up": int(r.up_votes), "down": int(r.dn_votes)}, "entry": r.entry, "stop": r.stop, "risk": r.risk, "t1": r.t1, "t2": r.t2, "t3": r.t3, "structure": r.structure,
            "hunts": (r.hunt1, r.hunt2, r.hunt3)}
