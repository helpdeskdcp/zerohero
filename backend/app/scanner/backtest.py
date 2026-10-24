"""Historical test exactly as pre-registered (data/research/scanner/SPEC_2026-10-24.md)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data as D, engine
from .library import SCANS

COST = {"NIFTY": 3.0, "BANKNIFTY": 3.0, "SENSEX": 3.0, "GOLDM": 5.0, "CRUDEOIL": 8.0, "NATURALGAS": 10.0}
HIST_UNIVERSE = ("NIFTY", "BANKNIFTY", "SENSEX")
HORIZONS = (1, 3, 5, 10); CAND = (3, 5, 10); MIN_N = 100


def trades(df: pd.DataFrame, sig: pd.Series, side: str, cost: float, h: int) -> pd.DataFrame:
    o = df["o"].values.astype(float); c = df["c"].values.astype(float); n = len(df); sgn = 1.0 if side == "LONG" else -1.0; out = []; last = -10 ** 9
    for i in np.where(sig.values)[0]:
        if i + h > n - 1 or i + 1 > n - 1: continue
        if i <= last + h: continue                                          # overlapping signal of the same scan on the same instrument: skipped
        last = i; out.append((pd.Timestamp(df["date"].iloc[i]), sgn * (c[i + h] / o[i + 1] - 1.0) * 1e4 - cost))
    return pd.DataFrame(out, columns=["date", "net"])


def _boot_lo(df: pd.DataFrame, alpha: float, B: int = 2000, seed: int = 7) -> float:
    g = df.groupby(df["date"].dt.to_period("M"))["net"]; s = g.sum().values; n = g.count().values
    if len(s) < 6: return float("nan")
    rng = np.random.default_rng(seed); out = [s[k].sum() / n[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(B))]; return float(np.percentile(out, 100 * alpha / 2))


def evaluate_scan(per_inst: dict, K: int, B: int = 2000) -> dict:
    """per_inst: inst -> {h: DataFrame(date, net)}. Pools instruments, applies the spec's gates."""
    res = {"horizons": {}, "n_by_inst": {i: int(len(v.get(5, []))) for i, v in per_inst.items()}}; npass = 0
    for h in HORIZONS:
        parts = [v[h].assign(inst=i) for i, v in per_inst.items() if h in v and len(v[h])]
        if not parts: continue
        p = pd.concat(parts, ignore_index=True); row = {"n": int(len(p)), "mean_net_bps": float(p["net"].mean()), "hit": float((p["net"] > 0).mean())}
        for mult in (0.5, 2.0): row[f"mean_net_cost_x{mult}"] = float((p["net"] + (1 - mult) * p["inst"].map(COST)).mean())
        if h in CAND and len(p) >= MIN_N:
            lo = _boot_lo(p, 0.05 / K, B); mid = p["date"].sort_values().iloc[len(p) // 2]; h1, h2 = p[p["date"] <= mid]["net"].mean(), p[p["date"] > mid]["net"].mean()
            drop = {i: float(p[p["inst"] != i]["net"].mean()) for i in p["inst"].unique()} if p["inst"].nunique() > 1 else {}
            row.update(ci_lo=lo, halves=[float(h1), float(h2)], drop_one=drop)
            row["pass"] = bool(row["mean_net_bps"] > 0 and lo > 0 and h1 > 0 and h2 > 0 and (not drop or min(drop.values()) > 0)); npass += row["pass"]
        res["horizons"][h] = row
    n5 = res["horizons"].get(5, {}).get("n", 0); res["npass"] = npass
    res["verdict"] = "INSUFFICIENT_SAMPLE" if n5 < MIN_N else ("GO-SHADOW" if npass >= 2 else ("single-horizon pass: not enough" if npass == 1 else "NO-GO")); return res


def run_all(loader=None, scans: dict | None = None, B: int = 2000) -> dict:
    scans = scans or SCANS; loader = loader or D.load_daily; frames = {}
    for inst in D.INSTRUMENTS:
        if inst not in HIST_UNIVERSE: continue                              # spec clarification: MCX history is unadjusted across rolls -> forward-only
        try: df = loader(inst)
        except Exception as e: print(inst, "load failed", e); continue
        if len(df) >= engine.MIN_BARS: frames[inst] = df
    out = {"instruments": {i: {"bars": int(len(f)), "from": str(f["date"].iloc[0])[:10], "to": str(f["date"].iloc[-1])[:10]} for i, f in frames.items()}, "scans": {}}; sig = {i: engine.evaluate(f, scans) for i, f in frames.items()}
    for name, (side, expr) in scans.items():
        per = {i: {h: trades(frames[i], sig[i][name], side, COST[i], h) for h in HORIZONS} for i in frames if name in sig[i]}
        out["scans"][name] = {"side": side, "expr": expr, **evaluate_scan(per, len(scans), B)}
    return out
