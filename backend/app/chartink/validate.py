"""Forward validation of Chartink scans, exactly as pre-registered (data/research/chartink/SPEC_2026-10-24.md). Read-only."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import store

COST_BPS = 8.0; SENS = (5.0, 10.0, 15.0); CAND = ("30", "60", "EOD"); MIN_DAYS, MIN_PAIRS = 30, 300
HZ = ("5", "15", "30", "60", "EOD")


def frame(path: str | None = None) -> pd.DataFrame:
    """One row per valid (alert, symbol, horizon): reference and mark prices with NIFTY, both status OK."""
    with store.conn(path) as c:
        q = """SELECT a.id alert_id, a.scan_name, a.session_date, p.id pair_id, p.symbol, m.horizon, m.price p1, m.nifty n1, r.price p0, r.nifty n0
               FROM marks m JOIN pairs p ON p.id=m.pair_id JOIN alerts a ON a.id=p.alert_id JOIN marks r ON r.pair_id=m.pair_id AND r.horizon='0'
               WHERE m.status='OK' AND r.status='OK' AND m.horizon!='0'"""
        return pd.DataFrame([dict(x) for x in c.execute(q).fetchall()])


def _boot_lo(blocks: list[np.ndarray], alpha: float, B: int = 1000, seed: int = 7) -> float:
    rng = np.random.default_rng(seed); s = np.array([b.sum() for b in blocks]); n = np.array([len(b) for b in blocks]); out = []
    for _ in range(B):
        k = rng.integers(0, len(blocks), len(blocks)); out.append(s[k].sum() / max(n[k].sum(), 1))
    return float(np.percentile(out, 100 * alpha / 2))


def evaluate(df: pd.DataFrame, sides: dict, n_scans: int | None = None, B: int = 1000) -> dict:
    out = {}
    if df.empty: return out
    K = n_scans or df["scan_name"].nunique(); alpha = 0.05 / K
    for scan, g in df.groupby("scan_name"):
        side = 1 if (sides.get(scan, {}).get("side", "LONG") == "LONG") else -1
        days = sorted(g["session_date"].unique()); res = {"side": "LONG" if side == 1 else "SHORT", "alert_days": len(days), "pairs": int(g["pair_id"].nunique()), "horizons": {}}
        for h in HZ:
            x = g[g["horizon"] == h]
            if x.empty: continue
            r = side * (x.p1 / x.p0 - 1) * 1e4; e = r - side * (x.n1 / x.n0 - 1) * 1e4; row = {"n": int(len(x)), "mean_r_bps": float(r.mean()), "mean_excess_bps": float(e.mean()), "hit": float((r > 0).mean()),
                                                                                           "net_excess": {str(c): float(e.mean() - c) for c in (COST_BPS,) + SENS}}
            if h in CAND:
                net = (e - COST_BPS).values; blocks = [net[(x["session_date"] == d).values] for d in days if (x["session_date"] == d).any()]
                half = len(days) // 2; first = set(days[:half]); m1 = net[x["session_date"].isin(first).values].mean() if half else np.nan; m2 = net[~x["session_date"].isin(first).values].mean() if half else np.nan
                row["net_ci_lo"] = _boot_lo(blocks, alpha, B) if len(blocks) >= 5 else None; row["halves"] = [float(m1), float(m2)]
                row["pass"] = bool(row["net_ci_lo"] is not None and row["net_ci_lo"] > 0 and net.mean() > 0 and m1 > 0 and m2 > 0)
            res["horizons"][h] = row
        enough = len(days) >= MIN_DAYS and res["pairs"] >= MIN_PAIRS; npass = sum(1 for h in CAND if res["horizons"].get(h, {}).get("pass"))
        res["npass"] = npass; res["verdict"] = "INSUFFICIENT_SAMPLE" if not enough else ("GO-SHADOW" if npass >= 2 else ("single-horizon pass: not enough" if npass == 1 else "NO-GO"))
        res["need"] = {"alert_days": f"{len(days)}/{MIN_DAYS}", "pairs": f"{res['pairs']}/{MIN_PAIRS}"}; out[scan] = res
    return out


def report(path: str | None = None) -> dict:
    return {"cost_bps": COST_BPS, "scans": evaluate(frame(path), store.load_scan_sides()), "spec": "data/research/chartink/SPEC_2026-10-24.md"}
