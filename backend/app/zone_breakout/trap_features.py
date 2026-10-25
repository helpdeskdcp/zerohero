"""Causal 'trap / stop-hunt' proxy features for Zone Breakout trades (spec: data/research/trap_filter/SPEC_2026-10-25.md).
Every feature of a trade uses bars[: signal_i + 1] only (tests/test_trap_filter.py rewrites the future and checks)."""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURES = ["adv_wick", "body", "close_loc", "range_atr", "zone_h_atr", "edge_dist_atr", "sweep", "prior_fail", "pd_dist_atr", "pd_beyond", "day_pos", "vwap_dist", "mins_open",
            "gap_pct", "prior12", "atr_pct", "ema_slope", "dirn", "risk_bp_f", "weekday"]


def _atr(h, l, c, n=14):
    pc = np.r_[c[0], c[:-1]]; tr = np.maximum(h - l, np.maximum(abs(h - pc), abs(l - pc))); a = np.full(len(c), np.nan)
    if len(c) > n:
        a[n - 1] = tr[:n].mean()
        for i in range(n, len(c)): a[i] = (a[i - 1] * (n - 1) + tr[i]) / n
    return a


def _ema(x, n):
    k = 2 / (n + 1); e = np.empty(len(x)); e[0] = x[0]
    for i in range(1, len(x)): e[i] = e[i - 1] + k * (x[i] - e[i - 1])
    return e


def build(bars: pd.DataFrame, trades: pd.DataFrame, zone_len: int = 20, only: int | None = None) -> pd.DataFrame:
    """bars: columns timestamp/open/high/low/close with a RangeIndex (the frame the trades were simulated on); trades: output of backtest.run."""
    if len(trades) == 0: return pd.DataFrame(columns=FEATURES)
    o, h, l, c = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close")); ts = pd.DatetimeIndex(bars["timestamp"]).tz_convert("Asia/Kolkata")
    day = np.array([x.date() for x in ts]); atr = _atr(h, l, c); ema = _ema(c, 50); mins = (ts.hour * 60 + ts.minute - (9 * 60 + 15)).to_numpy()
    first = {}; lastc = {}; dH = {}; dL = {}
    for i, d in enumerate(day):
        if d not in first: first[d] = i; dH[d] = h[i]; dL[d] = l[i]
        else: dH[d] = max(dH[d], h[i]); dL[d] = min(dL[d], l[i])
        lastc[d] = c[i]
    days = sorted(first); prev = {d: days[k - 1] for k, d in enumerate(days) if k}
    t = trades.sort_values("entry_ts").reset_index(drop=True); rows = []
    for k, r in t.iterrows():
        if only is not None and k != only: continue
        i = int(r["signal_i"]); a = atr[i]; dr = 1.0 if r["side"] == "BUY" else -1.0; rng = max(h[i] - l[i], 1e-9); d = day[i]; f0 = first[d]
        adv = (h[i] - max(o[i], c[i])) if dr > 0 else (min(o[i], c[i]) - l[i]); cl = (c[i] - l[i]) / rng if dr > 0 else (h[i] - c[i]) / rng
        z0 = max(0, i - zone_len); zh, zl = h[z0:i].max() if i > z0 else h[i], l[z0:i].min() if i > z0 else l[i]
        edge = (r["entry"] - zh) / a if dr > 0 else (zl - r["entry"]) / a
        p12 = slice(max(0, i - 12), i); sweep = float((dr > 0 and h[i] > h[p12].max() and c[i] < h[p12].max()) or (dr < 0 and l[i] < l[p12].min() and c[i] > l[p12].min())) if i > 0 else 0.0
        pf = int(((t["side"] == r["side"]) & (t["session"] == r["session"]) & (t["reason"] == "sl") & (t["exit_ts"] < r["entry_ts"]) & (t.index != k)).sum())   # strictly earlier trades only: a stop hit on the trade's own entry bar has exit_ts == entry_ts and must not count itself
        pdd = prev.get(d); pdH = dH[pdd] if pdd else np.nan; pdL = dL[pdd] if pdd else np.nan
        if pdd: pdist = (pdH - r["entry"]) / a if dr > 0 else (r["entry"] - pdL) / a
        else: pdist = np.nan
        dh, dl = h[f0:i + 1].max(), l[f0:i + 1].min(); vw = ((h[f0:i + 1] + l[f0:i + 1] + c[f0:i + 1]) / 3).mean()
        rows.append(dict(adv_wick=adv / rng, body=abs(c[i] - o[i]) / rng, close_loc=cl, range_atr=rng / a, zone_h_atr=(zh - zl) / a, edge_dist_atr=edge, sweep=sweep, prior_fail=pf,
                         pd_dist_atr=pdist, pd_beyond=float(pdist < 0) if pdd else np.nan, day_pos=(r["entry"] - dl) / max(dh - dl, 1e-9), vwap_dist=dr * (r["entry"] - vw) / a, mins_open=float(mins[i]),
                         gap_pct=(o[f0] / lastc[pdd] - 1) * 100 if pdd else np.nan, prior12=dr * (c[i] - c[max(0, i - 12)]) / a, atr_pct=float((atr[max(0, i - 100):i + 1] <= a).mean()),
                         ema_slope=dr * (ema[i] - ema[max(0, i - 5)]) / a, dirn=dr, risk_bp_f=float(r["risk_bp"]), weekday=float(ts[i].weekday())))
    X = pd.DataFrame(rows, columns=FEATURES); X.index = t.index[t.index == only] if only is not None else t.index; t = t.loc[X.index]; return pd.concat([t, X], axis=1)
