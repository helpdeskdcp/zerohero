"""Back-test of the owner's 5-minute bullish / bearish scans as pre-registered (data/research/scanner/SPEC_5M_BULLISH_2026-10-24.md). Writes results_5m.json."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.scanner import intraday as I

COST = {"NIFTY": 1.0, "BANKNIFTY": 1.0, "SENSEX": 1.5, "CRUDEOIL": 3.0, "NATURALGAS": 4.0, "GOLDM": 3.0}; HZ = (3, 6, 12); ALPHA = 0.05 / 6; MIN_N = 300


def trades(df, flag, side, h, cost):
    o = df["o"].values.astype(float); c = df["c"].values.astype(float); ist = df["t"].dt.tz_convert("Asia/Kolkata"); day = ist.dt.date.values; n = len(df); out = []; last = -10 ** 9
    for i in np.where(flag)[0]:
        if i + 1 >= n or i + h >= n or day[i + h] != day[i] or i <= last + h: continue
        last = i; out.append((str(day[i]), side * (c[i + h] / o[i + 1] - 1) * 1e4 - cost))
    return pd.DataFrame(out, columns=["day", "net"])


def boot_lo(t, alpha, B=3000, seed=7):
    g = t.groupby("day")["net"]; s = g.sum().values; n = g.count().values
    if len(s) < 8: return float("nan")
    rng = np.random.default_rng(seed); o = [s[k].sum() / n[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(B))]; return float(np.percentile(o, 100 * alpha / 2))


def main():
    frames = {s: I.futures_5m(s) for s in I.INSTRUMENTS}; flags = {s: I.scan_flags(f) for s, f in frames.items() if len(f) > 60}
    out = {"instruments": {s: {"bars": int(len(frames[s])), "from": str(frames[s]["t"].iloc[0])[:10], "to": str(frames[s]["t"].iloc[-1])[:10], "sessions": int(frames[s]["t"].dt.tz_convert("Asia/Kolkata").dt.date.nunique())} for s in flags}, "variants": {}}
    print(out["instruments"], flush=True)
    for var in list(I.VARIANTS) + ["ALL_BARS_LONG", "ALL_BARS_SHORT"]:
        side = -1 if ("BEAR" in var or var.endswith("SHORT")) else 1; res = {"horizons": {}}; npass = 0
        for h in HZ:
            parts = []
            for s, f in flags.items():
                fl = f[var].values if var in f else np.ones(len(f), bool)
                t = trades(frames[s], fl, side, h, COST[s]); t["inst"] = s; parts.append(t)
            p = pd.concat(parts, ignore_index=True); row = {"n": int(len(p)), "n_by_inst": p.groupby("inst").size().to_dict()}
            if len(p):
                row.update(mean_net=float(p["net"].mean()), hit=float((p["net"] > 0).mean()), mean_gross=float((p["net"] + p["inst"].map(COST)).mean()))
                if len(p) >= MIN_N and var in I.VARIANTS:
                    d = sorted(p["day"].unique()); mid = d[len(d) // 2]; h1, h2 = p[p["day"] <= mid]["net"].mean(), p[p["day"] > mid]["net"].mean(); drop = {i: float(p[p["inst"] != i]["net"].mean()) for i in p["inst"].unique()} if p["inst"].nunique() > 1 else {}
                    lo = boot_lo(p, ALPHA); row.update(ci_lo=lo, halves=[float(h1), float(h2)], drop_one=drop); row["pass"] = bool(row["mean_net"] > 0 and lo > 0 and h1 > 0 and h2 > 0 and (not drop or min(drop.values()) > 0)); npass += row["pass"]
            res["horizons"][h] = row
        res["npass"] = npass
        if var in I.VARIANTS: res["verdict"] = "INSUFFICIENT_SAMPLE" if res["horizons"][6]["n"] < MIN_N else ("GO-SHADOW" if npass >= 2 else "NO-GO")
        out["variants"][var] = res
    json.dump(out, open(os.path.join(HERE, "data", "research", "scanner", "results_5m.json"), "w"), indent=1, default=float)
    print(f"{'variant':15}{'verdict':22}" + "".join(f"{'h'+str(h)+' n / mean net / hit':>28}" for h in HZ))
    for var, r in out["variants"].items():
        print(f"{var:15}{r.get('verdict', '-'):22}" + "".join((f"{r['horizons'][h]['n']:>6} {r['horizons'][h].get('mean_net', float('nan')):+8.2f} bp {100 * r['horizons'][h].get('hit', 0):4.0f}%").rjust(28) for h in HZ))


if __name__ == "__main__":
    main()
