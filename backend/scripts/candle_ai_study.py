"""Candle-AI study, exactly as pre-registered in data/research/candle_ai/SPEC_2026-10-24.md. Writes data/research/candle_ai/results.json.   python scripts/candle_ai_study.py [--quick]"""
import json, os, sqlite3, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.index_signal_research.data import load_index_5m
from app.candle_ai import evaluate as E, features as F

OUT = os.path.join(HERE, "data", "research", "candle_ai", "results.json")
P1, P2 = list(range(2018, 2022)), list(range(2022, 2027))


def futures_5m(sym):
    c = sqlite3.connect(f"file:{os.path.join(HERE, 'data', 'market_history.db')}?mode=ro", uri=True)
    d = pd.read_sql_query("SELECT bar_start, session_date_ist, expiry, o, h, l, c, v FROM market_candles WHERE kind='FUTURE' AND symbol=? AND tf='1m'", c, params=(sym,)); c.close()
    d["t"] = pd.to_datetime(d["bar_start"], utc=True)
    front = d.groupby(["session_date_ist", "expiry"])["v"].sum().reset_index().sort_values("v").groupby("session_date_ist").tail(1)[["session_date_ist", "expiry"]]
    d = d.merge(front, on=["session_date_ist", "expiry"]).drop_duplicates("t").sort_values("t")
    d["bin"] = d["t"].dt.floor("5min")
    g = d.groupby("bin").agg(o=("o", "first"), h=("h", "max"), l=("l", "min"), c=("c", "last"), v=("v", "sum")).reset_index().rename(columns={"bin": "t"})
    return g.reset_index(drop=True)


def main():
    quick = "--quick" in sys.argv; res = {"spec": "SPEC_2026-10-24.md", "generated": time.strftime("%F %T"), "primary": {}, "tables": {}, "volume": {}}
    cells = {}
    for sym in ("NIFTY", "BANKNIFTY"):
        df = load_index_5m(sym, limit_years=3 if quick else None); f = F.build(df); tg = F.target(df); t0 = time.time()
        wf = E.walk_forward(f, tg, P1 + P2); print(sym, "walk-forward rows", len(wf), round(time.time() - t0), "s", flush=True)
        for name, yrs in (("P1", P1), ("P2", P2)):
            r = wf[wf["year"].isin(yrs)]
            if len(r) < 1000: continue
            m = E.cell_metrics(r); m["gates"] = E.gates(m); cells[f"{sym}_{name}"] = m; print(sym, name, {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items() if k not in ("gates",)}, m["gates"], flush=True)
        by_year = {int(y): E.cell_metrics(g)["auc_p2"] for y, g in wf.groupby("year")}; res["primary"][f"{sym}_auc_by_year"] = by_year
        res["tables"][sym] = E.cell_table(f, tg, ("2015-01-01", "2020-01-01"), ("2020-01-01", "2027-01-01")); print(sym, "replicated cells:", len(res["tables"][sym]), flush=True)
    res["primary"]["cells"] = cells; res["primary"]["verdict"] = E.verdict(cells) if cells else "INCONCLUSIVE"; print("VERDICT", res["primary"]["verdict"], flush=True)
    # ---- volume sub-study (descriptive, ~3-4 months of futures 5m with real volume)
    for sym in ("NIFTY", "BANKNIFTY", "NATURALGAS", "CRUDEOIL"):
        try:
            df = futures_5m(sym); f = F.build(df); tg = F.target(df); days = sorted(f["date"].unique()); k = int(len(days) * 0.7); cut = days[k]
            y = tg["y"].values; tr = (f["date"] < cut).values & ~np.isnan(y); te = (f["date"] >= cut).values & ~np.isnan(y)
            from app.candle_ai.models import GBM
            out = {"bars": int(len(df)), "days": len(days), "test_bars": int(te.sum())}
            for tag, vol in (("price_only", False), ("with_volume", True)):
                m = GBM(F.model_columns(f, vol)).fit(f[tr], y[tr].astype(int)); p = m.predict(f[te]); out[tag] = {"auc": E.auc(y[te], p), "auc_ci": E.boot_auc(y[te].astype(int), p, f["date"].values[te], B=300)}
            out["flag"] = "INSUFFICIENT_SAMPLE (descriptive only; no GO possible)"; res["volume"][sym] = out; print("VOLUME", sym, out, flush=True)
        except Exception as e:
            res["volume"][sym] = {"error": str(e)}; print("VOLUME", sym, "error", e, flush=True)
    json.dump(res, open(OUT, "w"), indent=1, default=float); print("wrote", OUT)


if __name__ == "__main__":
    main()
