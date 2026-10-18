"""Edge scan (multiple-testing control) on MCX NATURALGAS and CRUDEOIL continuous futures, DAILY resolution (intraday history is only ~3 months).
Dhan daily bars: dates are UTC stamps of 18:30 (=IST next day) -> converted to the IST session date; contract rolls leave unadjusted overnight gaps, so only
open->close quantities are used (see edge_scan.generate_daily). One BH family over both instruments. READ-ONLY.  Usage: python scripts/edge_scan_mcx_daily.py"""
import datetime as dt
import json
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.hybrid_orderflow.policy import load_policy  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402
from app.strategy_lab import gate as G  # noqa: E402

COST = 3.0
IST = dt.timedelta(hours=5, minutes=30)
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def load(sym):
    c = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True)
    rows = c.execute("SELECT bar_start,o,h,l,c FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1d' AND source='DHAN_HISTORICAL' ORDER BY bar_start", (sym,)).fetchall()
    d, O, H, L, C = [], [], [], [], []
    for ts, o, h, l, cl in rows:
        if None in (o, h, l, cl) or o <= 0:
            continue
        t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(tzinfo=None) + IST
        d.append(t.date().toordinal()); O.append(o); H.append(h); L.append(l); C.append(cl)
    d = np.array(d)
    keep = np.r_[True, np.diff(d) > 0]
    wd = np.array([dt.date.fromordinal(int(x)).weekday() for x in d])
    return d[keep], wd[keep], np.array(O)[keep], np.array(H)[keep], np.array(L)[keep], np.array(C)[keep]


O_ = dt.date.toordinal
PER = ES.Periods((O_(dt.date(2021, 9, 1)), O_(dt.date(2023, 12, 31))), (O_(dt.date(2024, 1, 1)), O_(dt.date(2025, 6, 30))), (O_(dt.date(2025, 7, 1)), O_(dt.date(2026, 12, 31))))
hyps = []
for sym in ("NATURALGAS", "CRUDEOIL"):
    d, wd, o, h, l, c = load(sym)
    say(f"{sym}: {len(d)} sessions {dt.date.fromordinal(int(d[0]))}..{dt.date.fromordinal(int(d[-1]))}, weekend-dated sessions after IST conversion: {int((wd >= 5).sum())}")
    keep = wd < 5                                                            # any residual weekend-dated bar would be a labelling error: excluded and reported
    hyps += ES.generate_daily(d[keep], wd[keep], o[keep], h[keep], l[keep], c[keep], tag=f"{sym[:3]} ")
fam = {}
for hh in hyps:
    fam[hh.family] = fam.get(hh.family, 0) + 1
say(f"hypotheses tested (pre-registered, nothing tuned, one BH family): {len(hyps)} {fam}; cost {COST} bps round trip; outcome = next session open->close")
res = ES.pipeline(hyps, PER, COST, q=(0.10, 0.05, 0.05), min_events=60, min_days=50)
say(f"eligible: {res['eligible']} | S1 discovery (BH q=0.10): {len(res['s1'])} | S2 validation (q=0.05): {len(res['s2'])} | S3 holdout (q=0.05): {len(res['s3'])}")
rows = []
for hh in hyps:
    m = (hh.day >= PER.train[0]) & (hh.day <= PER.train[1])
    mu, p, n, nd = ES.cluster_t(hh.day[m], hh.val[m] - COST)
    if n >= 60 and nd >= 50:
        mv, pv, nv, _ = ES._stat(hh, PER.valid, COST)
        mo, po, no, _ = ES._stat(hh, PER.oos, COST)
        rows.append((p, hh.name, mu, n, mv, pv, mo, po))
rows.sort()
say("\nTop 12 by TRAIN evidence (net bps/event, one-sided p) and what happened later:")
say(f"{'hypothesis':34s} {'TRAIN net':>9} {'p':>7} {'n':>5} | {'VALID net':>9} {'p':>6} | {'OOS net':>8} {'p':>6}")
for p, nm, mu, n, mv, pv, mo, po in rows[:12]:
    say(f"{nm:34s} {mu:9.1f} {p:7.4f} {n:5d} | {mv:9.1f} {pv:6.3f} | {mo:8.1f} {po:6.3f}")
for lab, key in (("S2 survivors", "s2"), ("S3 (holdout) survivors", "s3")):
    if res[key]:
        say(f"\n{lab}:")
        for item in res[key]:
            say("  " + item[0].name + "  " + " ".join(f"{x:.3g}" if isinstance(x, float) else str(x) for x in item[1:]))
if res["s3"]:
    pr = load_policy()["promotion"]
    for h_, mu, p, n, nd in res["s3"]:
        m = (h_.day >= PER.oos[0]) & (h_.day <= PER.oos[1])
        order = np.argsort(h_.day[m], kind="stable")
        g = G.evaluate(h_.val[m][order], h_.day[m][order], COST, pr)
        say(f"  gate {h_.name}: {g['verdict']} {g['failed']}")
pl = np.array(ES.placebo(hyps, PER, COST, runs=30, q=(0.10, 0.05, 0.05), min_events=60, min_days=50))
say(f"\nPlacebo (30 runs, random same-size draws): survivors per stage mean {pl.mean(0).round(2).tolist()} max {pl.max(0).tolist()} | real {[len(res['s1']), len(res['s2']), len(res['s3'])]}")
open(f"data/research/strategy_lab/edge_scan_mcx_daily_{dt.date.today().isoformat()}.txt", "w").write("\n".join(LINES) + "\n")
json.dump({"tested": len(hyps), "s1": [x[0].name for x in res["s1"]], "s2": [x[0].name for x in res["s2"]], "s3": [x[0].name for x in res["s3"]], "placebo": pl.tolist()},
          open(f"data/research/strategy_lab/edge_scan_mcx_daily_{dt.date.today().isoformat()}.json", "w"))
