"""Back-test harness for the Zone Breakout strategy (spec: data/research/zone_breakout/SPEC_2026-10-25.md). Costs are charged in R: cost_bp / risk_bp per trade."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as DT, strategy as ZB

PERIODS = {"P1": ("2015-01-01", "2019-12-31"), "P2": ("2020-01-01", "2023-12-31"), "P3": ("2024-01-01", "2026-12-31")}


def run(sym: str, tf: int = 5, cfg: ZB.Config | None = None, entry: str = "next_open", intraday: bool = True, bars: pd.DataFrame | None = None) -> pd.DataFrame:
    d5 = bars if bars is not None else DT.load_5m(sym); d = d5 if tf == 5 else DT.to_15m(d5)
    t, _ = ZB.simulate(d, cfg or ZB.Config(), entry, intraday, interval_min=tf)
    if len(t):
        t["net_R"] = t["R"] - DT.COST_BP[sym] / t["risk_bp"]; t["inst"] = sym; t["year"] = pd.DatetimeIndex(t["entry_ts"]).tz_convert("Asia/Kolkata").year
    return t


def period(t: pd.DataFrame, name: str) -> pd.DataFrame:
    a, b = PERIODS[name]; y = t["year"]; return t[(y >= int(a[:4])) & (y <= int(b[:4]))] if len(t) else t


def boot_lo(t: pd.DataFrame, alpha: float, col: str = "net_R", B: int = 3000, seed: int = 7) -> float:
    if len(t) < 30: return float("nan")
    g = t.groupby("session")[col]; s = g.sum().values; n = g.count().values; rng = np.random.default_rng(seed)
    out = [s[k].sum() / n[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(B))]; return float(np.percentile(out, 100 * alpha / 2))


def summary(t: pd.DataFrame, alpha: float = 0.025) -> dict:
    if t is None or len(t) == 0: return {"n": 0}
    w = t["net_R"]; gw = w[w > 0].sum(); gl = -w[w < 0].sum()
    return {"n": int(len(t)), "win_rate": float((t["R"] > 0).mean()), "mean_R_gross": float(t["R"].mean()), "mean_R_net": float(w.mean()), "ci_lo": boot_lo(t, alpha), "profit_factor_net": float(gw / gl) if gl > 0 else float("inf"),
            "median_risk_bp": float(t["risk_bp"].median()), "share_risk_lt5bp": float((t["risk_bp"] < 5).mean()), "tp": int((t["reason"] == "tp").sum()), "sl": int((t["reason"] == "sl").sum()), "eod": int((t["reason"] == "eod").sum())}
