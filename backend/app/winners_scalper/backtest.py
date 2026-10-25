"""Back-test of the ported signals (spec: data/research/winners_scalper/SPEC_2026-10-25.md). Entry = open of the bar after the signal bar; flat 15:15; no signal after 15:00."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ..zone_breakout import backtest as ZBB, data as DT
from . import strategy as WS


def simulate(df: pd.DataFrame, variant: str, p: WS.Params | None = None, strong_only: bool = False) -> pd.DataFrame:
    d = WS.signals(df, p); ts = pd.DatetimeIndex(d["timestamp"]).tz_convert("Asia/Kolkata"); day = np.array([x.date() for x in ts]); mins = (ts.hour * 60 + ts.minute).to_numpy()
    o, h, l, c = (d[k].to_numpy(float) for k in ("open", "high", "low", "close")); n = len(d); rows = []; i = 0; END = 15 * 60 + 15; LAST_SIG = 15 * 60
    while i < n - 2:
        side = 1 if d["buy"].iat[i] else -1 if d["sell"].iat[i] else 0
        if side == 0 or mins[i] > LAST_SIG or (strong_only and not d["strong"].iat[i]): i += 1; continue
        j = i + 1
        if day[j] != day[i] or mins[j] >= END: i += 1; continue
        entry = o[j]; sl0 = d["long_stop"].iat[i] if side == 1 else d["short_stop"].iat[i]; tp1 = d["tp1"].iat[i]
        risk = (entry - sl0) if side == 1 else (sl0 - entry)
        if not np.isfinite(risk) or risk <= 0 or not np.isfinite(tp1): i += 1; continue
        exit_px, reason, k = None, "eod", j
        while k < n:
            if day[k] != day[j]: exit_px, reason = c[k - 1], "eod"; k -= 1; break
            if mins[k] >= END: exit_px, reason = o[k], "eod"; break
            sl_k = sl0 if variant == "X1" else (d["long_stop"].iat[k - 1] if side == 1 else d["short_stop"].iat[k - 1]) if k > j else sl0
            if not np.isfinite(sl_k): sl_k = sl0
            hit_sl = (l[k] <= sl_k) if side == 1 else (h[k] >= sl_k)
            if hit_sl:
                exit_px = (min(o[k], sl_k) if side == 1 else max(o[k], sl_k)) if k > j else sl_k; reason = "sl"; break
            if variant == "X1" and ((h[k] >= tp1) if side == 1 else (l[k] <= tp1)): exit_px, reason = tp1, "tp"; break
            if variant == "X2" and d["dir"].iat[k] != side and k + 1 < n and day[k + 1] == day[j]: exit_px, reason = o[k + 1], "flip"; k += 1; break
            k += 1
        if exit_px is None: exit_px, reason = c[min(k, n - 1)], "eod"
        pnl = (exit_px - entry) if side == 1 else (entry - exit_px); rows.append(dict(side="BUY" if side == 1 else "SELL", signal_i=i, entry_ts=d["timestamp"].iat[j], entry=entry, sl=sl0, tp=tp1, exit=exit_px, reason=reason, R=pnl / risk,
                                                                                  risk_bp=risk / entry * 1e4, session=day[j], strong=bool(d["strong"].iat[i]), exit_i=k)); i = max(k, i) + 1
    return pd.DataFrame(rows)


def run(sym: str, tf: int, variant: str, strong_only: bool = False, bars: pd.DataFrame | None = None) -> pd.DataFrame:
    d5 = bars if bars is not None else DT.load_5m(sym); d = d5 if tf == 5 else DT.to_15m(d5); t = simulate(d, variant, None, strong_only)
    if len(t): t["net_R"] = t["R"] - DT.COST_BP[sym] / t["risk_bp"]; t["inst"] = sym; t["year"] = pd.DatetimeIndex(t["entry_ts"]).tz_convert("Asia/Kolkata").year
    return t
