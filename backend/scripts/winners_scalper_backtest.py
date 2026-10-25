"""Pre-registered: data/research/winners_scalper/SPEC_2026-10-25.md"""
import json, os, sys
import pandas as pd
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.zone_breakout import backtest as ZBB, data as DT
from app.winners_scalper import backtest as WB
ALPHA = 0.05 / 4; OUT = os.path.join(HERE, "data", "research", "winners_scalper")

if __name__ == "__main__":
    bars = {s: DT.load_5m(s) for s in DT.INDICES}; res = {}
    for tf in (5, 15):
        for var in ("X1", "X2"):
            t = pd.concat([WB.run(s, tf, var, bars=bars[s]) for s in DT.INDICES], ignore_index=True); key = f"{var}_{tf}m"
            per = {pn: ZBB.summary(ZBB.period(t, pn), ALPHA) for pn in ZBB.PERIODS}; allr = ZBB.summary(t, ALPHA)
            ok = allr["n"] >= 300 and allr["mean_R_net"] > 0 and allr["ci_lo"] > 0 and sum(per[p].get("n", 0) > 0 and per[p]["mean_R_net"] > 0 for p in per) >= 2 and per["P3"].get("mean_R_net", -1) > 0
            by_inst = {s: {k: v for k, v in ZBB.summary(t[t.inst == s], ALPHA).items() if k in ("n", "win_rate", "mean_R_gross", "mean_R_net")} for s in DT.INDICES}
            strong = ZBB.summary(t[t.strong], ALPHA); res[key] = dict(all=allr, periods=per, by_inst=by_inst, strong_only={k: v for k, v in strong.items() if k in ("n", "win_rate", "mean_R_gross", "mean_R_net", "ci_lo")}, verdict="GO-SHADOW" if ok else "NO-GO")
            print(key, res[key]["verdict"], json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in allr.items() if k in ("n", "win_rate", "mean_R_gross", "mean_R_net", "ci_lo", "median_risk_bp")}), "| P:", {p: (per[p].get("n"), round(per[p].get("mean_R_net", 0), 3)) for p in per}, "| strong", res[key]["strong_only"].get("n"), round(res[key]["strong_only"].get("mean_R_net", 0), 3), flush=True)
    json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1, default=str)
