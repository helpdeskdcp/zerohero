"""Safe AI U-turn rule on MCX FUTURES (NATURALGAS, CRUDEOIL) 1-minute candles since 2026-07 -- RESEARCH ONLY (no order path, Safe AI code untouched).
Pre-registered BEFORE looking at any result: the unchanged Safe-AI detector (app.arena.safe_ai.signals.detect), thrust levels {0.6 %, 1.0 %, 1.5 %} for both symbols (all three reported, none selected), MCX window 09:35-22:30 IST,
one trade a day, entry = open of the bar after the signal, SL = extreme -/+ 0.2 ATR5, T1 / T2 / T3 = 0.8 / 1.6 / 2.4 R (legs 40 / 30 / 30), time exit 23:15, round-trip cost on the FUTURE = FUT_COST_BP of net_edge (NG 4 bp, CRUDE 3 bp);
the option vehicle is not tested (MCX option premium history is too short). Verdict gate (same spirit as before): net per trade lower bound > 0 with >= 100 trades -- impossible with ~4 months, so this can only say 'no signal of an edge' or 'worth collecting more'."""
import json, os, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.arena.safe_ai import signals as SG
from app.research_engines.hybrid_sr_mtf import data as D, sim as S
from app.autoscalp.net_edge import FUT_COST_BP
import sqlite3
DB = os.path.join(HERE, "..", "..", "market_history.db")

def load(sym):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60)
    d = pd.read_sql_query("SELECT bar_start, expiry, o, h, l, c FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' ORDER BY bar_start", c, params=(sym,)); c.close()
    d["ts"] = pd.to_datetime(d["bar_start"], utc=True).dt.tz_convert("Asia/Kolkata"); d["day"] = d.ts.dt.strftime("%Y%m%d").astype(int)
    keep = d.groupby("day")["expiry"].agg(lambda x: x.value_counts().idxmax()); d = d[d.expiry == d.day.map(keep)]                      # per day the contract with most bars = the front/liquid one
    d = d.drop_duplicates("ts").set_index("ts").sort_index().rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
    return d[["open", "high", "low", "close", "day"]].astype(float)

def atr5(h, l, c, i):
    k = max(1, i - 70); hh = h[k:i + 1]; ll = l[k:i + 1]; cc = c[k:i + 1]
    return float(np.mean(hh - ll)) * np.sqrt(5)                                                               # coarse 5-minute range proxy from 1-minute ranges (no resampling needed)

out = {"note": __doc__, "results": {}}
for sym in ("NATURALGAS", "CRUDEOIL"):
    d = load(sym); o, h, l, c = (d[k].to_numpy() for k in ("open", "high", "low", "close")); days = d.day.to_numpy().astype(int); mins = (d.index.hour * 60 + d.index.minute).to_numpy(); t_abs = np.asarray((d.index - pd.Timestamp("1970-01-01", tz="UTC")).total_seconds(), float)
    out["results"][sym] = {"sessions": int(len(np.unique(days))), "first": str(d.index[0]), "last": str(d.index[-1])}
    for thrust in (0.006, 0.010, 0.015):
        p = SG.Params(thrust=thrust, t_from=(9, 35), t_to=(22, 30)); trades = []
        for dk in np.unique(days):
            ids = np.flatnonzero(days == dk)
            if len(ids) < 240: continue
            ticks = [(t_abs[i] + 60.0, float(c[i])) for i in ids]
            for k in range(20, len(ids) - 2):
                if mins[ids[k]] + 1 < 9 * 60 + 35 or mins[ids[k]] + 1 > 22 * 60 + 30: continue
                dd = SG.detect(ticks[:k + 1], ticks[k][0], p)
                if dd is None: continue
                side = 1 if dd["side"] == "CE" else -1; j = ids[k]; ext = dd["features"]["extreme"]; a = atr5(h, l, c, j)
                entry = float(o[j + 1]); sl = ext - side * 0.2 * a; risk = abs(entry - sl)
                if (entry - sl) * side <= 0 or risk <= 0 or risk > 4 * a: break
                r = S.simulate(o, h, l, c, mins, days, j + 1, side, entry, sl, entry + side * 0.8 * risk, entry + side * 1.6 * risk, entry + side * 2.4 * risk, 23 * 60 + 15)
                gross_bp = r["pts"] / entry * 1e4; net_bp = gross_bp - FUT_COST_BP[sym]
                trades.append({"day": int(dk), "side": side, "gross_bp": gross_bp, "net_bp": net_bp, "R": r["R"], "risk_bp": risk / entry * 1e4}); break
        t = pd.DataFrame(trades)
        if len(t) == 0: out["results"][sym][f"thrust{thrust}"] = {"trades": 0}; continue
        mirror = []
        for _ in [0]: pass
        net = t.net_bp.to_numpy(); g = pd.Series(net).groupby(t.day.to_numpy()); sums = g.sum().to_numpy(); cnt = g.count().to_numpy(); ci = [None, None]
        if len(sums) >= 8:
            rng = np.random.default_rng(1); bs = [sums[k].sum() / cnt[k].sum() for k in (rng.integers(0, len(sums), len(sums)) for _ in range(3000))]; ci = [round(float(np.percentile(bs, 5)), 1), round(float(np.percentile(bs, 95)), 1)]
        out["results"][sym][f"thrust{thrust}"] = {"trades": int(len(t)), "win_rate_net": round(float((net > 0).mean()), 3), "gross_bp_per_trade": round(float(t.gross_bp.mean()), 1), "cost_bp": FUT_COST_BP[sym], "net_bp_per_trade": round(float(net.mean()), 1), "ci90_net_bp": ci,
                                                  "R_mean": round(float(t.R.mean()), 3), "median_risk_bp": round(float(t.risk_bp.median()), 1), "long_share": round(float((t.side > 0).mean()), 2)}
        print(sym, thrust, out["results"][sym][f"thrust{thrust}"], flush=True)
json.dump(out, open(os.path.join(HERE, "mcx_uturn_test.json"), "w"), indent=1, default=float)
