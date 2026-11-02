"""PHASE 3: chronological replay of the pre-registered candidates on NIFTY / BANKNIFTY / SENSEX 1-minute index candles (read-only).
Splits (fixed before any result was seen): NIFTY DEV 2017-06..2021-12, VALIDATION 2022-2023, OUT-OF-SAMPLE 2024-01..2026-10; BANKNIFTY / SENSEX DEV 2021-09..2022-12, VALIDATION 2023, OOS 2024-01..2026-10.
The strategy has no fitted parameter, so 'walk-forward' = the same frozen rules evaluated year by year. Control: the MIRROR trade (same entry bar, opposite side, mirrored stop / targets).
Writes p3_results.json. python p3_backtest.py [SYMBOL ...]"""
import json, os, sys, time
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D, engine as E, metrics as M, sim as S

SPLITS = {"NIFTY": {"DEV": (20170601, 20211231), "VALIDATION": (20220101, 20231231), "OOS": (20240101, 20261031)},
          "BANKNIFTY": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)},
          "SENSEX": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}}
VARIANTS = {"ROOM_ALL": E.Params(room_levels="all"), "ROOM_MAJOR": E.Params(room_levels="major")}


def mirror(prep, t):
    side = -t["side"]; e = t["entry"]; mir = lambda x: 2 * e - x; mins = prep.t1 % 1440
    r = S.simulate(prep.o, prep.h, prep.l, prep.c, mins, prep.day, t["entry_i"], side, e, mir(t["sl"]), mir(t["t1"]), mir(t["t2"]), mir(t["t3"]), 15 * 60 + 15)
    return {**t, **r, "side": side}


def main(symbols):
    out = {"splits": SPLITS, "variants": {k: v.__dict__ for k, v in VARIANTS.items()}, "note": "gross index points -> 1 lot of an ATM weekly option, delta 0.5, monitored cost model; theta/gamma not modelled", "results": {}}
    for sym in symbols:
        t0 = time.time(); d = D.load_index_1m(sym); prep = D.prepare(sym, d); reg = M.regimes(prep); out["results"][sym] = {}
        print(sym, "bars", len(d), "5m", len(prep.tf5.c), "load+prep %.0fs" % (time.time() - t0), flush=True)
        for vname, params in VARIANTS.items():
            for cand in E.CANDIDATES:
                t1 = time.time(); trades, rej, stats = E.scan(prep, cand, params)
                for t in trades: t["regime"] = reg.get(t["day"], "n/a"); t["year"] = t["day"] // 10000
                mirr = [mirror(prep, t) for t in trades]
                for t, m in zip(trades, mirr): m["regime"] = t["regime"]; m["year"] = t["year"]
                res = {"setups": stats.get("setups", 0), "trades": len(trades), "rejected": dict(rej), "structure_stats": {k: v for k, v in stats.items() if k != "setups"}, "splits": {}, "mirror_splits": {}, "by_year": {}, "by_regime": {}}
                for sp, (a, b) in SPLITS[sym].items():
                    sel = [t for t in trades if a <= t["day"] <= b]; res["splits"][sp] = M.summarize(sel, sym); res["mirror_splits"][sp] = M.summarize([m for m in mirr if a <= m["day"] <= b], sym)
                for y in sorted({t["year"] for t in trades}): res["by_year"][str(y)] = M.summarize([t for t in trades if t["year"] == y], sym)
                for rg in ("bull", "bear", "sideways"): res["by_regime"][rg] = M.summarize([t for t in trades if t["regime"] == rg and SPLITS[sym]["VALIDATION"][0] <= t["day"]], sym)
                res["all_trades"] = M.summarize(trades, sym); res["by_side"] = {("long" if s > 0 else "short"): M.summarize([t for t in trades if t["side"] == s], sym) for s in (1, -1)}
                out["results"][sym][f"{vname}/{cand}"] = res
                oo = res["splits"]["OOS"]; print(f"  {vname:10s} {cand:18s} trades {len(trades):4d} | OOS n={oo.get('n')} net/trade={oo.get('net_per_trade_rs')} ci={oo.get('ci90_per_trade')} PF={oo.get('profit_factor')} win={oo.get('win_rate')} | %.1fs" % (time.time() - t1), flush=True)
    json.dump(out, open(os.path.join(HERE, "p3_results.json" if len(symbols) == 3 else f"p3_results_{'_'.join(symbols)}.json"), "w"), indent=1, default=float)


if __name__ == "__main__":
    main(sys.argv[1:] or ["NIFTY", "BANKNIFTY", "SENSEX"])
