"""HYPOTHESIS H-M (from the owner's request; derived EXPLORATORILY from Mohit's 29 Arena-settled calls: index calls in the first hour won, later ones and MCX did not): 'an early-session (<= 10:15) turn away from the session extreme has before-cost
information'. It is tested here on INDEPENDENT data (NIFTY 2017-2026, BANKNIFTY / SENSEX 2021-2026 one-minute index candles), with the existing Safe-AI U-turn rule exactly as already built (thrust 0.50 % = v1, 0.60 % = v2: both pre-existing, nothing tuned),
the same simulator, costs, splits, mirror control and promotion gate as the main study (the gate is NOT relaxed). One trade per day at most. Controls: ALLDAY window (09:35-14:30) vs EARLY (09:35-10:15) shows whether 'early' matters;
the RANDOM-TIME control takes a same-geometry trade at a random minute of the same early window in the direction of the same turn rule's side (to show what the geometry alone earns).
Geometry (fixed): SL = extreme -/+ 0.2 ATR5, T1 / T2 / T3 = 0.8 / 1.6 / 2.4 R (Mohit's median first target is 0.81 R), legs 40/30/30, stop to entry after T1."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.arena.safe_ai import signals as SG
from app.research_engines.hybrid_sr_mtf import data as D, metrics as M, sim as S
from p3_backtest import SPLITS, mirror
import p3_backtest as P

def run(prep, thrust, t_to, rnd=None):
    p = SG.Params(thrust=thrust, t_from=(9, 35), t_to=(t_to // 60, t_to % 60)); mins = prep.t1 % 1440; trades = []; out_days = np.unique(prep.day); idx_by_day = {}
    for i, dk in enumerate(prep.day): idx_by_day.setdefault(int(dk), []).append(i)
    t5 = prep.tf5
    for dk, ids in idx_by_day.items():
        if len(ids) < 360: continue
        ts = [(float(prep.t1[i] * 60 + 60), float(prep.c[i])) for i in ids]          # closing time of each 1-minute bar (seconds on the absolute minute axis), its close
        fired = False
        for k in range(20, len(ids) - 2):
            m_close = int(mins[ids[k]]) + 1
            if m_close < 9 * 60 + 35: continue
            if m_close > t_to: break
            d = SG.detect(ts[:k + 1], ts[k][0], p)
            if d is None: continue
            side = 1 if d["side"] == "CE" else -1; j = ids[k]
            i5 = int(np.searchsorted(t5.end, prep.t1[j] + 1, side="right")) - 1
            atr5 = float(t5.atr[i5]) if i5 >= 0 and np.isfinite(t5.atr[i5]) else None
            if not atr5: break
            ext = d["features"]["extreme"]; sl = ext - side * 0.2 * atr5; entry = float(prep.o[j + 1]); risk = abs(entry - sl)
            if (entry - sl) * side <= 0 or risk > 2.5 * atr5 or risk < 0.4 * atr5: break
            res = S.simulate(prep.o, prep.h, prep.l, prep.c, mins, prep.day, j + 1, side, entry, sl, entry + side * 0.8 * risk, entry + side * 1.6 * risk, entry + side * 2.4 * risk, 15 * 60 + 15)
            trades.append({"cand": "H_M", "day": int(dk), "side": side, "t_entry": int(prep.t1[j + 1]), "entry": entry, "sl": sl, "t1": entry + side * 0.8 * risk, "t2": entry + side * 1.6 * risk, "t3": entry + side * 2.4 * risk, "entry_i": j + 1, **res})
            fired = True; break
    return trades

out = {"note": "see module docstring", "results": {}}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    prep = D.prepare(sym, D.load_index_1m(sym)); reg = M.regimes(prep); out["results"][sym] = {}
    for thrust in (0.005, 0.006):
        for wname, t_to in (("EARLY", 10 * 60 + 15), ("ALLDAY", 14 * 60 + 30)):
            tr = run(prep, thrust, t_to); mir = [mirror(prep, t) for t in tr]
            for t, m in zip(tr, mir): t["regime"] = reg.get(t["day"], "n/a"); m["regime"] = t["regime"]
            res = {"trades": len(tr), "splits": {}, "mirror_splits": {}, "by_regime": {}}
            for sp, (a, b) in SPLITS[sym].items():
                res["splits"][sp] = M.summarize([t for t in tr if a <= t["day"] <= b], sym); res["mirror_splits"][sp] = M.summarize([m for m in mir if a <= m["day"] <= b], sym)
            for rg in ("bull", "bear", "sideways"): res["by_regime"][rg] = M.summarize([t for t in tr if t["regime"] == rg and t["day"] >= SPLITS[sym]["VALIDATION"][0]], sym)
            res["all"] = M.summarize(tr, sym); out["results"][sym][f"thrust{thrust}/{wname}"] = res
            o = res["splits"]["OOS"]; print(sym, f"thrust {thrust:.3f}", wname, "trades", len(tr), "| DEV", res["splits"]["DEV"].get("n"), res["splits"]["DEV"].get("net_per_trade_rs"), "VAL", res["splits"]["VALIDATION"].get("n"), res["splits"]["VALIDATION"].get("net_per_trade_rs"),
                  "| OOS n", o.get("n"), "net", o.get("net_per_trade_rs"), o.get("ci90_per_trade"), "win", o.get("win_rate"), "PF", o.get("profit_factor"), "| mirror OOS", res["mirror_splits"]["OOS"].get("net_per_trade_rs"), flush=True)
json.dump(out, open(os.path.join(HERE, "p5_early_turn_results.json"), "w"), indent=1, default=float)
