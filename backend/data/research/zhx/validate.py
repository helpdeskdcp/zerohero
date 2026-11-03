"""ZHX v1 validation on 5-minute INDEX bars built from the stored 1-minute candles (read-only). The Pine script itself manages the trades (SL first on a shared candle; EOD square-off; 40/30/30 scale-out at T1/T2/T3; costs NOT inside the script).
Per trade: entry bar, risk points, realised R -> index points (R x risk) -> one lot of an ATM weekly option (delta 0.5, monitored cost model + 2 extra brokerage orders) via hybrid_sr_mtf.metrics.net_rs.
Splits fixed in advance (same as the earlier studies): NIFTY DEV <=2021, VALIDATION 2022-2023, OOS 2024+; BANKNIFTY / SENSEX DEV 2021-09..2022, VALIDATION 2023, OOS 2024+. Chunks of 12,000 bars with a 600-bar warm-up whose trades are dropped. Nothing was tuned on any of this: defaults = the script's first values."""
import json, os, sys, time
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.research_engines.hybrid_sr_mtf import data as D, metrics as M
from app.smart_chart import runner
SRC = open(os.path.join(HERE, sys.argv[1] if len(sys.argv) > 1 else "zhx_v1.pine")).read(); TAG = sys.argv[2] if len(sys.argv) > 2 else "v1"
SPL = {"NIFTY": {"DEV": (20170601, 20211231), "VALIDATION": (20220101, 20231231), "OOS": (20240101, 20261031)}, "BANKNIFTY": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)},
       "SENSEX": {"DEV": (20210929, 20221231), "VALIDATION": (20230101, 20231231), "OOS": (20240101, 20261031)}}
CH, WARM = 12000, 600; out = {"tag": TAG, "results": {}}
for sym in ("NIFTY", "BANKNIFTY", "SENSEX"):
    d = D.load_index_1m(sym); b = d.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna(); b = b[b.index.hour * 60 + b.index.minute >= 9 * 60 + 15]
    ts = b.index; trades = []; t0 = time.time(); i = 0
    while i < len(b):
        lo = max(0, i - WARM); hi = min(len(b), i + CH); seg = b.iloc[lo:hi]
        cs = [{"time": int(t.timestamp() * 1000), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": 0.0} for t, r in seg.iterrows()]
        r = runner.execute({"op": "run", "source": SRC, "candles": cs, "tf": "5", "symbol": sym}, count=False, timeout=290)
        if not r.get("ok"): print(sym, "FAILED", r.get("diagnostics")); break
        P = {p["title"]: p["values"] for p in r["plots"]}
        sig = P["ZHX Signal"]; ent = P["ZHX Entry"]; scr = P["ZHX Score"]; rr = P["ZHX Realised R"]; rk = P["ZHX Closed Risk Pts"]; dr = P["ZHX Open Trade Dir"]
        for k in range(len(seg)):
            if k < (i - lo): continue                                                             # warm-up bars of this chunk belong to the previous chunk
            if rr[k] is not None: trades.append({"exit_t": seg.index[k], "R": float(rr[k]), "risk": float(rk[k])})
            if sig[k]: trades.append({"entry_t": seg.index[k], "side": int(sig[k]), "entry": float(ent[k]), "score": float(scr[k]) if scr[k] is not None else None})
        i = hi
    ent_ = [t for t in trades if "entry_t" in t]; ex_ = [t for t in trades if "exit_t" in t]
    rows = []
    for e, x in zip(ent_, ex_):                                                                      # one position at a time: the k-th exit closes the k-th entry
        rows.append({"score": e["score"], "day": int(e["entry_t"].strftime("%Y%m%d")), "side": e["side"], "R": x["R"], "pts": x["R"] * x["risk"], "risk": x["risk"], "t_entry": int(e["entry_t"].timestamp()), "reason": "", "ambiguous": False, "mae": 0.0, "mfe": 0.0, "hit_t1": False, "hit_t2": False, "hit_t3": False})
    res = {"bars": len(b), "signals": len(ent_), "closed": len(ex_), "seconds": int(time.time() - t0), "splits": {}}
    for sp, (a, z) in SPL[sym].items():
        sel = [t for t in rows if a <= t["day"] <= z]; s = M.summarize(sel, sym) if sel else {"n": 0}
        res["splits"][sp] = {"n": s.get("n"), "gross_R_mean": round(float(np.mean([t["R"] for t in sel])), 3) if sel else None, "gross_pts_mean": round(float(np.mean([t["pts"] for t in sel])), 1) if sel else None, "win_rate_net": s.get("win_rate"), "net_per_trade_rs": s.get("net_per_trade_rs"),
                             "ci90_net": s.get("ci90_per_trade"), "profit_factor_net": s.get("profit_factor"), "max_dd_rs": s.get("max_drawdown_rs"), "trades_per_day": round(len(sel) / max(1, len({t["day"] for t in sel})), 2) if sel else None,
                             "long_share": round(float(np.mean([t["side"] > 0 for t in sel])), 2) if sel else None}
    res["trade_rows"] = [{k: t[k] for k in ("day", "side", "score", "R", "risk", "pts")} for t in rows]
    out["results"][sym] = res; print(sym, json.dumps({k: v for k, v in res.items() if k != "splits"}), flush=True)
    for sp, v in res["splits"].items(): print("   ", sp, v, flush=True)
    json.dump(out, open(os.path.join(HERE, f"validate_{TAG}.json"), "w"), indent=1, default=str)
