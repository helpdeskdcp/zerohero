"""Zone Breakout calibration exactly as pre-registered (SPEC_2026-10-25.md): 72-config grid on P1 (NIFTY+BANKNIFTY, 5m, variant A), the single best (>= 300 P1 trades) is tested ONCE on P2, then described on P3. Writes results_calibration.json."""
import itertools, json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.zone_breakout import backtest as BT, data as DT, strategy as ZB


def main():
    t0 = time.time(); bars = {s: DT.load_5m(s) for s in ("NIFTY", "BANKNIFTY")}; p1 = {s: b[b["timestamp"] < pd.Timestamp("2020-01-01", tz="Asia/Kolkata")].reset_index(drop=True) for s, b in bars.items()}
    grid = list(itertools.product((1.0, 1.5, 2.0, 3.0), (2, 3, 5), (True, False), (1.0, 1.5, 2.5))); rows = []
    for k, (rr, cb, ema, mc) in enumerate(grid):
        cfg = ZB.Config(rr=rr, cons_bars=cb, use_ema_filter=ema, max_cons_atr=mc); t = pd.concat([BT.run(s, 5, cfg, "next_open", True, bars=p1[s]) for s in p1], ignore_index=True)
        sm = BT.summary(t) if len(t) else {"n": 0}; rows.append({"rr": rr, "cons_bars": cb, "ema": ema, "max_cons_atr": mc, "n": sm["n"], "net_R": sm.get("mean_R_net"), "gross_R": sm.get("mean_R_gross"), "win": sm.get("win_rate")})
        if k % 8 == 0: print(k + 1, "/", len(grid), round(time.time() - t0), "s", flush=True)
    g = pd.DataFrame(rows); ok = g[g["n"] >= 300].sort_values("net_R", ascending=False); res = {"trials": len(grid), "p1_grid_top5": ok.head(5).to_dict("records"), "p1_median_net_R": float(ok["net_R"].median()) if len(ok) else None, "p1_share_net_positive": float((ok["net_R"] > 0).mean()) if len(ok) else None}
    if len(ok) == 0: res["selected"] = None; res["verdict"] = "NO CONFIG WITH >=300 P1 TRADES"
    else:
        b = ok.iloc[0]; cfg = ZB.Config(rr=float(b.rr), cons_bars=int(b.cons_bars), use_ema_filter=bool(b.ema), max_cons_atr=float(b.max_cons_atr)); res["selected"] = cfg.as_dict(); res["selected_p1_net_R_in_sample"] = float(b.net_R)
        tt = pd.concat([BT.run(s, 5, cfg, "next_open", True, bars=bars[s]) for s in bars], ignore_index=True)
        for per in ("P1", "P2", "P3"):
            res[per] = {"POOLED": BT.summary(BT.period(tt, per), alpha=0.05), **{s: BT.summary(BT.period(tt[tt["inst"] == s], per), alpha=0.05) for s in bars}}
        p2 = res["P2"]; res["verdict"] = "PASS_P2" if (p2["POOLED"]["n"] >= 150 and p2["POOLED"].get("mean_R_net", -1) > 0 and p2["POOLED"].get("ci_lo", -1) > 0 and all(p2[s].get("mean_R_net", -1) > 0 for s in bars)) else "FAIL_P2"
    json.dump(res, open(os.path.join(HERE, "data", "research", "zone_breakout", "results_calibration.json"), "w"), indent=1, default=float)
    print("CALIBRATION", res["verdict"], "| selected", res.get("selected"), "| in-sample P1 net R", res.get("selected_p1_net_R_in_sample"))
    for per in ("P1", "P2", "P3"):
        if per in res: c = res[per]["POOLED"]; print(per, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in c.items() if k in ("n", "win_rate", "mean_R_gross", "mean_R_net", "ci_lo", "profit_factor_net")})


if __name__ == "__main__":
    main()
