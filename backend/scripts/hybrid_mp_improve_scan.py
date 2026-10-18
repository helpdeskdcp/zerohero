"""Disciplined improvement scan for the corrected Hybrid MP: ONE-FACTOR-AT-A-TIME variants fixed in advance (mechanism in the comment), judged by a MECHANICAL
accept rule that is printed before any number is looked at. Selection on NIFTY TRAIN (2017-21) and VALID (2022-23); replication on SENSEX / BANKNIFTY; the 2024+
period is used once, at the end, for the accepted combination only. READ-ONLY.

Accept a variant v iff (all):  NIFTY TRAIN net/trade >= base + 0.25 bps  AND  TRAIN net per YEAR >= 0.9 x base  AND  NIFTY VALID net/trade >= base
AND VALID net per year >= 0.9 x base  AND  on SENSEX and BANKNIFTY (2022-23) net/trade >= base and net per year >= 0.9 x base.
(net per year protects against 'improving' by simply trading less.)"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import _atr, corrected_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402

COST = 2.0
YEARS = {"TRAIN": 4.6, "VALID": 2.0, "OOS": 2.8}
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def summ(tr):
    if not tr:
        return {"n": 0, "net": float("nan"), "yr": 0.0, "p": 1.0}
    g = np.array([t.bps for t in tr]); d = np.array([t.day for t in tr])
    mu, p, n, nd = ES.cluster_t(d, g - COST)
    return {"n": n, "net": mu, "yr": 0.0, "p": p, "tot": float((g - COST).sum())}


DATA = {}
for sym, start in (("NIFTY", "2017-01-01"), ("SENSEX", "2021-01-01"), ("BANKNIFTY", "2021-01-01")):
    b1 = load_1m(sym, "INDEX", start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    ch = resample(b1, 5, 555, 930)
    DATA[sym] = (b1, ch, _atr(ch["h"], ch["l"], ch["c"], 14))

VARIANTS = {  # name: (param overrides, post-filter or None)
    "base (corrected)": ({}, None),
    # A: even less cost per R -- the dose-response had not flattened at 2.0
    "A1 min_risk_atr 2.5": ({"min_risk_atr": 2.5, "max_risk_atr": 4.0}, None),
    "A2 min_risk_atr 3.0": ({"min_risk_atr": 3.0, "max_risk_atr": 5.0}, None),
    # B: with 2-3 ATR stops the quarter-scale exits may leave too much on the table / too little reachable
    "B1 exit T2 (all out)": ({"exit_model": "T2"}, None),
    "B2 exit T3 (all out)": ({"exit_model": "T3"}, None),
    "B3 exit PINE (SL or T4)": ({"exit_model": "PINE"}, None),
    # C: continuation should be stronger when ALL timeframes agree
    "C1 min_agree 4": ({"min_agree": 4}, None),
    # D: late entries have no time to run (hour table showed the 15h bucket weakest)
    "D1 no entry last 45m": ({"no_entry_last_min": 45}, None),
    "D2 no entry last 75m": ({"no_entry_last_min": 75}, None),
    # E: continuation needs movement -- trade only when ATR is expanding vs its own 20-session median (known at entry)
    "E1 ATR ratio > 1.0": ({}, 1.0),
    "E2 ATR ratio > 1.25": ({}, 1.25),
}
say(__doc__.split("\n\n")[1] if "\n\n" in __doc__ else "")
R = {}
for sym, (b1, ch, atr) in DATA.items():
    med = np.full(len(atr), np.nan)
    for i in range(1500, len(atr), 25):
        med[i:i + 25] = np.nanmedian(atr[i - 1500:i])
    for name, (ov, rat) in VARIANTS.items():
        tr, _ = run(ch, corrected_params(chart_tf=5, **ov), b1=b1)
        if rat is not None:
            tr = [t for t in tr if np.isfinite(med[t.sig_i]) and atr[t.sig_i] / med[t.sig_i] > rat]
        R[(sym, name)] = tr


def row(sym, name, split):
    tr = of_split(R[(sym, name)], {"TRAIN": TRAIN, "VALID": VALID, "OOS": OOS}[split])
    s = summ(tr)
    s["yr"] = s.get("tot", 0.0) / YEARS[split]
    return s


say(f"{'variant':26s} | {'NIFTY TRAIN':32s} | {'NIFTY VALID':32s} | SENSEX VALID      | BANKNIFTY VALID  | accept")
say(f"{'':26s} | {'n    net/tr  net/yr   p':32s} | {'n    net/tr  net/yr   p':32s} | n   net/tr net/yr | n   net/tr net/yr")
base = {k: row("NIFTY", "base (corrected)", k) for k in ("TRAIN", "VALID")}
bs = {s: row(s, "base (corrected)", "VALID") for s in ("SENSEX", "BANKNIFTY")}
accepted = []
for name in VARIANTS:
    nt, nv = row("NIFTY", name, "TRAIN"), row("NIFTY", name, "VALID")
    se, bn = row("SENSEX", name, "VALID"), row("BANKNIFTY", name, "VALID")
    ok = (nt["net"] >= base["TRAIN"]["net"] + 0.25 and nt["yr"] >= 0.9 * base["TRAIN"]["yr"] and nv["net"] >= base["VALID"]["net"] and nv["yr"] >= 0.9 * base["VALID"]["yr"]
          and se["net"] >= bs["SENSEX"]["net"] and se["yr"] >= 0.9 * bs["SENSEX"]["yr"] and bn["net"] >= bs["BANKNIFTY"]["net"] and bn["yr"] >= 0.9 * bs["BANKNIFTY"]["yr"])
    if name.startswith("base"):
        ok = False
    f = lambda s: f"{s['n']:4d} {s['net']:+6.2f} {s['yr']:+7.0f} {s['p']:6.3f}"
    g = lambda s: f"{s['n']:3d} {s['net']:+6.2f} {s['yr']:+6.0f}"
    say(f"{name:26s} | {f(nt):32s} | {f(nv):32s} | {g(se):17s} | {g(bn):16s} | {'ACCEPT' if ok else ('-' if not name.startswith('base') else 'base')}")
    if ok:
        accepted.append(name)
say(f"\naccepted by the mechanical rule: {accepted if accepted else 'NONE'}")
open("data/research/strategy_lab/hybrid_improve_scan_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
import pickle
pickle.dump({k: [(t.day, t.bps, t.entry_i, t.sig_i) for t in v] for k, v in R.items()}, open("/root/.claude/jobs/ba6f2282/tmp/improve_R.pkl", "wb"))
