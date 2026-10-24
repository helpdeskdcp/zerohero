"""ADDENDUM 2026-10-24 (dated, spec untouched): same walk-forward + same gates, with the 61 canonical TA-Lib patterns added as features. Writes results_addendum_talib.json."""
import json, os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
import numpy as np, pandas as pd
from app.index_signal_research.data import load_index_5m
from app.candle_ai import evaluate as E, features as F
from app.candle_ai.patterns_talib import talib_features

P1, P2 = list(range(2018, 2022)), list(range(2022, 2027)); out = {"cells": {}, "baseline_M2_without_talib": {}}
base = json.load(open(os.path.join(HERE, "data", "research", "candle_ai", "results.json")))["primary"]["cells"]
for sym in ("NIFTY", "BANKNIFTY"):
    df = load_index_5m(sym); f = F.build(df); tg = F.target(df); f = pd.concat([f, talib_features(df)], axis=1); t0 = time.time()
    wf = E.walk_forward(f, tg, P1 + P2); print(sym, len(wf), round(time.time() - t0), "s", flush=True)
    for name, yrs in (("P1", P1), ("P2", P2)):
        m = E.cell_metrics(wf[wf["year"].isin(yrs)]); m["gates"] = E.gates(m); out["cells"][f"{sym}_{name}"] = m
        print(sym, name, "AUC %.4f (without TA-Lib %.4f) B1 %.4f | slope %.2f | signals %d net@1bp %+.2f CI %s" % (m["auc_p2"], base[f"{sym}_{name}"]["auc_p2"], m["auc_pb"], m["slope"], m["n_signals"], m.get("net_bps", float("nan")), m.get("net_ci")), m["gates"], flush=True)
out["verdict"] = E.verdict(out["cells"]); print("ADDENDUM VERDICT", out["verdict"])
json.dump(out, open(os.path.join(HERE, "data", "research", "candle_ai", "results_addendum_talib.json"), "w"), indent=1, default=float)
