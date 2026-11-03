"""BANKNIFTY for Safe AI -- RESEARCH ONLY (owner's request 2026-11-03: today BANKNIFTY is skipped because only MONTHLY options exist, 56 days to expiry, and the rule trades only the last 3 days before an expiry).
Question 1 (index level, 2021-09..2026-10, 1-minute): does the unchanged U-turn rule (thrust 0.5 % = v1, 0.6 % = v2, window 09:35-14:30 = the Safe-AI window) have any GROSS information on BANKNIFTY? Splits DEV 2021-09..2022-12, VALIDATION 2023, OOS 2024-01..2026-10; mirror control; geometry SL = extreme -/+ 0.2 ATR5, T1/T2/T3 0.8/1.6/2.4 R, EOD 15:15 (same as the H-M study).
Question 2 (vehicle): what would a MONTHLY ATM option cost? Real far-dated BANKNIFTY option bars are NOT stored (the capture has only the 29-Sep contract, <= 2 days to expiry, ATM premium 0.45 % of spot), so the premium is ESTIMATED from Black-Scholes ATM (0.4 x sigma x sqrt(T)) with sigma 15 %: 30 days -> 1.7 % of spot, 56 days -> 2.4 %. delta 0.5, theta / gamma not modelled, costs = monitored cost model."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", "..")); sys.path.insert(0, os.path.join(HERE, "..", "hybrid_sr_mtf"))
from app.arena.safe_ai import signals as SG
from app.research_engines.hybrid_sr_mtf import data as D, metrics as M, sim as S
from p3_backtest import SPLITS, mirror

def trades_for(prep, thrust):
    p = SG.Params(thrust=thrust, t_from=(9, 35), t_to=(14, 30)); mins = prep.t1 % 1440; out = []; idx = {}
    for i, dk in enumerate(prep.day): idx.setdefault(int(dk), []).append(i)
    t5 = prep.tf5
    for dk, ids in idx.items():
        if len(ids) < 360: continue
        ticks = [(float(prep.t1[i] * 60 + 60), float(prep.c[i])) for i in ids]
        for k in range(20, len(ids) - 2):
            m_close = int(mins[ids[k]]) + 1
            if m_close < 9 * 60 + 35: continue
            if m_close > 14 * 60 + 30: break
            d = SG.detect(ticks[:k + 1], ticks[k][0], p)
            if d is None: continue
            side = 1 if d["side"] == "CE" else -1; j = ids[k]; i5 = int(np.searchsorted(t5.end, prep.t1[j] + 1, side="right")) - 1
            a = float(t5.atr[i5]) if i5 >= 0 and np.isfinite(t5.atr[i5]) else None
            if not a: break
            ext = d["features"]["extreme"]; sl = ext - side * 0.2 * a; entry = float(prep.o[j + 1]); risk = abs(entry - sl)
            if (entry - sl) * side <= 0 or risk > 2.5 * a or risk < 0.4 * a: break
            r = S.simulate(prep.o, prep.h, prep.l, prep.c, mins, prep.day, j + 1, side, entry, sl, entry + side * 0.8 * risk, entry + side * 1.6 * risk, entry + side * 2.4 * risk, 15 * 60 + 15)
            out.append({"cand": "SAFE", "day": int(dk), "side": side, "t_entry": int(prep.t1[j + 1]), "entry": entry, "sl": sl, "t1": entry + side * 0.8 * risk, "t2": entry + side * 1.6 * risk, "t3": entry + side * 2.4 * risk, "entry_i": j + 1, "spot": entry, **r}); break
    return out

prep = D.prepare("BANKNIFTY", D.load_index_1m("BANKNIFTY")); res = {"note": __doc__, "results": {}}
for thrust in (0.005, 0.006):
    tr = trades_for(prep, thrust); mir = [mirror(prep, t) for t in tr]; r = {"trades_all": len(tr), "splits": {}}
    for sp, (a, b) in SPLITS["BANKNIFTY"].items():
        sel = [t for t in tr if a <= t["day"] <= b]; msel = [m for m in mir if a <= m["day"] <= b]
        row = {"n": len(sel), "gross_pts_per_trade": round(float(np.mean([t["pts"] for t in sel])), 1) if sel else None, "R_mean": round(float(np.mean([t["R"] for t in sel])), 3) if sel else None, "mirror_gross_pts": round(float(np.mean([t["pts"] for t in msel])), 1) if msel else None, "vehicle": {}}
        for name, pct in (("weekly_assumption_350", None), ("monthly_30d_est_1.7pct", 1.7), ("monthly_56d_est_2.4pct", 2.4)):
            old = M.PREMIUM["BANKNIFTY"]
            if pct: M.PREMIUM["BANKNIFTY"] = round(pct / 100 * float(np.mean([t["spot"] for t in sel])), 0) if sel else old
            s = M.summarize(sel, "BANKNIFTY") if sel else {"n": 0}
            be = None
            if sel: net0, fr = M.net_rs("BANKNIFTY", 0.0); be = round(fr / (M.DELTA * M.LOT["BANKNIFTY"]), 1)
            row["vehicle"][name] = {"premium": M.PREMIUM["BANKNIFTY"], "net_per_trade_rs": s.get("net_per_trade_rs"), "ci90": s.get("ci90_per_trade"), "win": s.get("win_rate"), "breakeven_index_pts": be}
            M.PREMIUM["BANKNIFTY"] = old
        r["splits"][sp] = row
    res["results"][f"thrust_{thrust}"] = r
    for sp, row in r["splits"].items(): print(f"thrust {thrust} {sp}: n={row['n']} gross pts/trade={row['gross_pts_per_trade']} R={row['R_mean']} mirror={row['mirror_gross_pts']} | " + " | ".join(f"{k}: net {v['net_per_trade_rs']} ci {v['ci90']} be {v['breakeven_index_pts']}pts" for k, v in row["vehicle"].items()), flush=True)
json.dump(res, open(os.path.join(HERE, "banknifty_monthly_test.json"), "w"), indent=1, default=float)
