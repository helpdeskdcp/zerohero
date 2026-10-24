"""Stock-universe back-test of the 36 daily scans as pre-registered (data/research/scanner/SPEC_STOCKS_2026-10-24.md). Writes results_stocks.json."""
import json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.scanner import engine, stocks as ST
from app.scanner.library import SCANS

HZ = (1, 3, 5, 10); CAND = (3, 5, 10); COST, SENS = 15.0, (5.0, 25.0); MIN_N = 500; K = len(SCANS)


def main():
    t0 = time.time(); ks = ST.keys(); frames = {}
    for s in ks:
        try:
            d = ST.load_stock(s, ikey=ks[s])
            if len(d) >= 130: frames[s] = d
        except Exception as e: print("load failed", s, repr(e)[:80], flush=True)
        time.sleep(0.05)
    print(f"{len(frames)} stocks loaded in {round(time.time() - t0)}s; bars {min(len(f) for f in frames.values())}..{max(len(f) for f in frames.values())}", flush=True)
    cal = sorted(set().union(*[set(pd.to_datetime(f["date"])) for f in frames.values()])); ci = {d: i for i, d in enumerate(cal)}; nC = len(cal); names = list(frames)
    O = np.full((nC, len(names)), np.nan); C = np.full((nC, len(names)), np.nan)
    for j, s in enumerate(names):
        idx = [ci[d] for d in pd.to_datetime(frames[s]["date"])]; O[idx, j] = frames[s]["o"].values; C[idx, j] = frames[s]["c"].values
    R = {}; B = {}
    for h in HZ:
        r = np.full((nC, len(names)), np.nan); r[:nC - h] = C[h:nC] / O[1:nC - h + 1] - 1; R[h] = r * 1e4; B[h] = np.nanmean(R[h], axis=1)
    sig = {s: engine.evaluate(frames[s]) for s in names}; print("scans evaluated", round(time.time() - t0), "s", flush=True)
    dates = pd.DatetimeIndex(cal); out = {"stocks": len(names), "calendar_days": nC, "from": str(cal[0].date()), "to": str(cal[-1].date()), "scans": {}}
    for name, (side, expr) in SCANS.items():
        sg = 1.0 if side == "LONG" else -1.0; res = {"side": side, "horizons": {}}; npass = 0
        for h in HZ:
            rows = []
            for j, s in enumerate(names):
                f = sig[s].get(name)
                if f is None: continue
                di = [ci[d] for d in pd.to_datetime(frames[s]["date"])]; last = -10 ** 9
                for i in np.where(f.values)[0]:
                    t = di[i]
                    if t <= last + h or not np.isfinite(R[h][t, j]) or not np.isfinite(B[h][t]): continue
                    last = t; rows.append((s, dates[t], sg * R[h][t, j], sg * (R[h][t, j] - B[h][t])))
            p = pd.DataFrame(rows, columns=["stock", "date", "raw", "exc"])
            row = {"n": int(len(p))}
            if len(p):
                row.update(mean_raw_net=float((p["raw"] - COST).mean()), mean_excess_gross=float(p["exc"].mean()), mean_excess_net=float((p["exc"] - COST).mean()), hit_excess=float(((p["exc"] - COST) > 0).mean()), **{f"mean_excess_net_cost{int(c)}": float((p["exc"] - c).mean()) for c in SENS})
                if h in CAND and len(p) >= MIN_N:
                    net = (p["exc"] - COST).values; mon = p["date"].dt.to_period("M").values; g = pd.Series(net).groupby(mon); sm = g.sum().values; nn = g.count().values; rng = np.random.default_rng(7)
                    bs = [sm[k].sum() / nn[k].sum() for k in (rng.integers(0, len(sm), len(sm)) for _ in range(2000))]; lo = float(np.percentile(bs, 100 * (0.05 / K) / 2))
                    mid = p["date"].sort_values().iloc[len(p) // 2]; h1, h2 = float(net[(p["date"] <= mid).values].mean()), float(net[(p["date"] > mid).values].mean())
                    yr = pd.Series(net).groupby(p["date"].dt.year.values).mean(); ypos = int((yr > 0).sum()); contrib = pd.Series(net).groupby(p["stock"].values).sum().sort_values(ascending=False); keep = ~p["stock"].isin(contrib.index[:5]).values
                    drop5 = float(net[keep].mean()) if keep.sum() else float("nan")
                    row.update(ci_lo=lo, halves=[h1, h2], years_positive=ypos, years=int(len(yr)), drop_top5_mean=drop5)
                    row["pass"] = bool(row["mean_excess_net"] > 0 and lo > 0 and h1 > 0 and h2 > 0 and ypos >= 5 and drop5 > 0); npass += row["pass"]
            res["horizons"][h] = row
        n5 = res["horizons"][5]["n"]; res["npass"] = npass; res["verdict"] = "INSUFFICIENT_SAMPLE" if n5 < MIN_N else ("GO-SHADOW" if npass >= 2 else "NO-GO"); out["scans"][name] = res
    json.dump(out, open(os.path.join(HERE, "data", "research", "scanner", "results_stocks.json"), "w"), indent=1, default=float)
    print(f"{'scan':30}{'side':6}{'verdict':22}{'n5':>7}{'exc gross 5d':>13}{'exc net 5d':>11}{'raw net 5d':>11}{'exc net 10d':>12}")
    for k, v in out["scans"].items():
        h5, h10 = v["horizons"][5], v["horizons"][10]
        print(f"{k:30}{v['side']:6}{v['verdict']:22}{h5['n']:7d}{h5.get('mean_excess_gross', float('nan')):13.1f}{h5.get('mean_excess_net', float('nan')):11.1f}{h5.get('mean_raw_net', float('nan')):11.1f}{h10.get('mean_excess_net', float('nan')):12.1f}")
    print("GO-SHADOW:", [k for k, v in out["scans"].items() if v["verdict"] == "GO-SHADOW"])


if __name__ == "__main__":
    main()
