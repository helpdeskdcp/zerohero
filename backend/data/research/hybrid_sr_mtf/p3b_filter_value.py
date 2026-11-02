"""What do the NO-TRADE filters buy? Same candidates with the HTF-bias, room-to-opposing-level, chop and time-window filters switched OFF (risk bounds, the 1-minute trigger and data checks stay), versus the filtered
ROOM_ALL / ROOM_MAJOR runs of p3_backtest.py, on the out-of-sample period. A filter is useful only if it raises expectancy per trade or the win rate without just deleting trades at random.
Random-deletion control: the filtered set is compared with 200 random subsets of the unfiltered trades of the same size (same days)."""
import json, os, sys
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D, engine as E, metrics as M
from p3_backtest import SPLITS, VARIANTS
UNF = E.Params(htf_filter=False, min_rr=0.0, chop_range_atr=0.0, win_from=9 * 60 + 20, win_to=14 * 60 + 45, room_levels="all")
out = {}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    prep = D.prepare(sym, D.load_index_1m(sym)); a, b = SPLITS[sym]["OOS"]; out[sym] = {}
    for cand in E.CANDIDATES:
        tu, ru, su = E.scan(prep, cand, UNF); tu = [t for t in tu if a <= t["day"] <= b]
        row = {"unfiltered": M.summarize(tu, sym), "unfiltered_rejects": dict(ru)}
        nets_u = np.array([M.net_rs(sym, t["pts"])[0] for t in tu])
        for vname, prm in VARIANTS.items():
            tf, rf, sf = E.scan(prep, cand, prm); tf = [t for t in tf if a <= t["day"] <= b]; row[vname] = M.summarize(tf, sym)
            if len(tf) >= 5 and len(tu) > len(tf):
                rng = np.random.default_rng(3); k = len(tf); means = [nets_u[rng.choice(len(nets_u), k, replace=False)].mean() for _ in range(200)]
                fm = float(np.mean([M.net_rs(sym, t["pts"])[0] for t in tf])); row[vname]["random_subset_mean_net_p5_p95"] = [round(float(np.percentile(means, 5)), 1), round(float(np.percentile(means, 95)), 1)]
                row[vname]["filtered_mean_net"] = round(fm, 1); row[vname]["beats_random_deletion_p95"] = bool(fm > np.percentile(means, 95))
        out[sym][cand] = row
        print(sym, cand, "unfiltered n", len(tu), "net/trade", row["unfiltered"].get("net_per_trade_rs"), "win", row["unfiltered"].get("win_rate"), "| ROOM_ALL n", row["ROOM_ALL"].get("n"), row["ROOM_ALL"].get("net_per_trade_rs"), "| ROOM_MAJOR n", row["ROOM_MAJOR"].get("n"), row["ROOM_MAJOR"].get("net_per_trade_rs"), flush=True)
json.dump(out, open(os.path.join(HERE, "p3b_filter_value.json"), "w"), indent=1, default=float)
