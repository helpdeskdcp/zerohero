"""Python mirror of app/smart_uturn/pine/chanakya_smart_trend_pullback_v1.pine (same rules, same bar order) so the dashboard's claims can be replayed on long history. Research only."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import indicators as ind
from .strategy import mtf_values


@dataclass(frozen=True)
class TPParams:
    min_htf: int = 1
    mid_len: int = 21
    slow_len: int = 50
    rsi_len: int = 14
    pull_bars: int = 6
    touch_atr: float = 0.15
    rsi_long_min: int = 45
    rsi_short_max: int = 55
    min_atr_bps: float = 2.0
    cooldown: int = 5
    sess_start_hm: int = 925
    sess_end_hm: int = 1500
    flat_hm: int = 1515
    max_per_day: int = 4
    max_loss_day: int = 2
    atr_len: int = 14
    swing_len: int = 8
    sl_buffer: float = 0.2
    min_risk_atr: float = 0.5
    max_risk_atr: float = 2.5
    rr1: float = 1.0
    rr2: float = 2.0
    rr3: float = 3.0
    mintick: float = 0.05
    mode: str = "PULLBACK"          # PULLBACK | BREAKOUT | BOTH
    bo_len: int = 20
    bo_adx: float = 25.0
    bo_rsi_long: float = 55.0
    bo_rsi_short: float = 45.0


def dmi(h, l, c, n: int = 14):
    """Pine ta.dmi(n, n): returns (DI+, DI-, ADX)."""
    h, l, c = (np.asarray(x, float) for x in (h, l, c)); up = np.zeros(len(h)); dn = np.zeros(len(h)); up[1:] = np.diff(h); dn[1:] = -np.diff(l)
    pdm = np.where((up > dn) & (up > 0), up, 0.0); mdm = np.where((dn > up) & (dn > 0), dn, 0.0); tr = ind.rma(ind.true_range(h, l, c), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        plus = 100 * ind.rma(pdm, n) / tr; minus = 100 * ind.rma(mdm, n) / tr
        tot = plus + minus; dx = np.abs(plus - minus) / np.where(tot == 0, 1, tot)
    ok = ~np.isnan(dx); adx = np.full(len(h), np.nan)
    if ok.any():
        f = int(np.argmax(ok)); adx[f:] = ind.rma(dx[f:], n)
    return plus, minus, 100 * adx


def htf_trend(bars: pd.DataFrame, p: TPParams) -> np.ndarray:
    c = bars.close.to_numpy(float); a, b, r = ind.ema(c, p.mid_len), ind.ema(c, p.slow_len), ind.rsi(c, p.rsi_len)
    with np.errstate(invalid="ignore"):
        return np.where((a > b) & (r > 50), 1, np.where((a < b) & (r < 50), -1, 0))


def run(chart: pd.DataFrame, htf_a: pd.DataFrame, htf_b: pd.DataFrame, p: TPParams = TPParams(), include_open: bool = False) -> pd.DataFrame:
    n = len(chart); o, h, l, c = (chart[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    em, es, rv = ind.ema(c, p.mid_len), ind.ema(c, p.slow_len), ind.rsi(c, p.rsi_len); atr = ind.atr(h, l, c, p.atr_len)
    sw_lo, sw_hi = ind.lowest(l, p.swing_len), ind.highest(h, p.swing_len); pull_lo, pull_hi = ind.lowest(l, p.pull_bars), ind.highest(h, p.pull_bars)
    rv1, h1, l1 = ind.shift(rv), ind.shift(h), ind.shift(l)
    dip, dim, adx = dmi(h, l, c, 14); hh = ind.highest_prev(h, p.bo_len); ll = ind.lowest_prev(l, p.bo_len)
    score = mtf_values(chart.index, htf_a, htf_trend(htf_a, p)) + mtf_values(chart.index, htf_b, htf_trend(htf_b, p))
    loc = chart.index.tz_convert("Asia/Kolkata"); hm = np.asarray(loc.hour * 100 + loc.minute); day = np.asarray(loc.date)
    with np.errstate(invalid="ignore"):
        local_up, local_dn = em > es, em < es
        touched_up, touched_dn = pull_lo <= em + p.touch_atr * atr, pull_hi >= em - p.touch_atr * atr
        trig_long = (c > em) & (c > o) & (c > h1) & (rv > p.rsi_long_min) & (rv > rv1)
        trig_short = (c < em) & (c < o) & (c < l1) & (rv < p.rsi_short_max) & (rv < rv1)
        bo_long = (adx >= p.bo_adx) & (dip > dim) & (em > es) & (c > em) & (h > hh) & (c > o) & (rv >= p.bo_rsi_long)
        bo_short = (adx >= p.bo_adx) & (dim > dip) & (em < es) & (c < em) & (l < ll) & (c < o) & (rv <= p.bo_rsi_short)
        atr_bps = np.where(c > 0, atr / c * 1e4, np.nan); vol_ok = ~np.isnan(atr_bps) & (atr_bps >= p.min_atr_bps)
        min_dist = np.maximum(atr * p.min_risk_atr, p.mintick * 2)
        stop_l = np.minimum(sw_lo - atr * p.sl_buffer, c - min_dist); stop_s = np.maximum(sw_hi + atr * p.sl_buffer, c + min_dist)
        wide_l = (c - stop_l) > atr * p.max_risk_atr; wide_s = (stop_s - c) > atr * p.max_risk_atr
    in_sess = (hm >= p.sess_start_hm) & (hm < p.sess_end_hm); flat_now = hm >= p.flat_hm
    sig_today = loss_today = 0; last_buy = last_sell = None; t_dir = 0; third = 1.0 / 3.0; out = []
    tr = dict(entry=0.0, stop=0.0, risk=0.0, t=(0.0, 0.0, 0.0), bar=0, h=[False, False, False], realized=0.0, remaining=0.0, time=None, end="")
    for i in range(n):
        new_day = i == 0 or day[i] != day[i - 1]
        if new_day: sig_today = loss_today = 0
        can = in_sess[i] and vol_ok[i] and not np.isnan(atr[i]) and sig_today < p.max_per_day and loss_today < p.max_loss_day and t_dir == 0
        buy_cd = last_buy is None or i - last_buy > p.cooldown; sell_cd = last_sell is None or i - last_sell > p.cooldown
        pb_long = score[i] >= p.min_htf and local_up[i] and touched_up[i] and trig_long[i]; pb_short = score[i] <= -p.min_htf and local_dn[i] and touched_dn[i] and trig_short[i]
        use_pb, use_bo = p.mode in ("PULLBACK", "BOTH"), p.mode in ("BREAKOUT", "BOTH")
        long_setup = can and ((use_pb and pb_long) or (use_bo and bo_long[i])) and buy_cd
        short_setup = can and ((use_pb and pb_short) or (use_bo and bo_short[i])) and sell_cd
        buy = long_setup and not wide_l[i]; sell = short_setup and not wide_s[i]
        if t_dir != 0 and i > tr["bar"]:
            d = t_dir; stop_hit = (l[i] <= tr["stop"]) if d == 1 else (h[i] >= tr["stop"]); ended = None
            if stop_hit:
                tr["realized"] -= tr["remaining"]; tr["remaining"] = 0.0; ended = "STOP"
            else:
                for k, rr in enumerate((p.rr1, p.rr2, p.rr3)):
                    if k > 0 and not tr["h"][k - 1]: break
                    if not tr["h"][k] and ((h[i] >= tr["t"][k]) if d == 1 else (l[i] <= tr["t"][k])):
                        tr["h"][k] = True; tr["realized"] += rr * third; tr["remaining"] -= third
                        if k == 2: ended = "T3"
            if tr["remaining"] > 1e-4 and (flat_now[i] or new_day):
                px = c[i - 1] if (new_day and i > 0) else c[i]; tr["realized"] += tr["remaining"] * (d * (px - tr["entry"]) / tr["risk"]); tr["remaining"] = 0.0; ended = "FLAT"
            if tr["remaining"] <= 1e-4:
                if tr["realized"] < 0: loss_today += 1
                out.append({"time": tr["time"], "dir": d, "entry": tr["entry"], "risk": tr["risk"], "gross_R": tr["realized"], "h1": tr["h"][0], "h2": tr["h"][1], "h3": tr["h"][2], "end": ended or tr["end"]})
                t_dir = 0
        if buy or sell:
            d = 1 if buy else -1; st = stop_l[i] if d == 1 else stop_s[i]; risk = abs(c[i] - st)
            t_dir = d; tr = dict(entry=c[i], stop=st, risk=risk, t=tuple(c[i] + d * risk * rr for rr in (p.rr1, p.rr2, p.rr3)), bar=i, h=[False, False, False], realized=0.0, remaining=1.0, time=chart.index[i], end="OPEN")
            sig_today += 1
            if buy: last_buy = i
            else: last_sell = i
    if include_open and t_dir != 0:
        mark = tr['realized'] + tr['remaining'] * (t_dir * (c[-1] - tr['entry']) / tr['risk'])
        out.append({'time': tr['time'], 'dir': t_dir, 'entry': tr['entry'], 'risk': tr['risk'], 'gross_R': mark, 'h1': tr['h'][0], 'h2': tr['h'][1], 'h3': tr['h'][2], 'end': 'OPEN'})
    return pd.DataFrame(out)
