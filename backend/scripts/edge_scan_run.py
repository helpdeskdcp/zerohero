"""Run the pre-registered edge scan (app/strategy_lab/edge_scan.py) on NIFTY 5m, with placebo calibration, BANKNIFTY replication and the promotion gate.
READ-ONLY. Costs: 2 bps round trip. Periods fixed in advance (same as smart_bos splits): TRAIN 2017-06..2021-12, VALID 2022-2023, OOS(holdout) 2024+.
Usage: python scripts/edge_scan_run.py"""
import datetime as dt
import json
import sys
import time

import numpy as np

sys.path.insert(0, ".")
from app.hybrid_orderflow.policy import load_policy  # noqa: E402
from app.research_engines.hybrid_mp.engine import resample  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402
from app.strategy_lab import gate as G  # noqa: E402

COST = 2.0
O = dt.date.toordinal
PER = ES.Periods((O(dt.date(2017, 1, 1)), O(dt.date(2021, 12, 31))), (O(dt.date(2022, 1, 1)), O(dt.date(2023, 12, 31))), (O(dt.date(2024, 1, 1)), O(dt.date(2026, 12, 31))))
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


def mats_for(sym, start):
    b1 = load_1m(sym, "INDEX", start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    ch = resample(b1, 5, 555, 930)
    days, mod = ist_minutes(ch["t"])
    return ES.build_mats(ch["t"], ch["o"], ch["h"], ch["l"], ch["c"], days + 0, mod)


t0 = time.time()
M = mats_for("NIFTY", "2017-01-01")
# ist_minutes() returns days as ordinal-since-epoch; build_mats only needs a consistent integer key
say(f"NIFTY 5m: {M.D} sessions, {M.day[0]}..{M.day[-1]} (day keys), cost {COST} bps round trip")
EPOCH = 719163
PER = ES.Periods(*[tuple(x - EPOCH for x in p) for p in (PER.train, PER.valid, PER.oos)])
hyps = ES.generate(M)
fam = {}
for h in hyps:
    fam[h.family] = fam.get(h.family, 0) + 1
say(f"hypotheses tested (pre-registered, nothing tuned): {len(hyps)}  {fam}")
res = ES.pipeline(hyps, PER, COST)
say(f"eligible (>=300 events, >=150 days in TRAIN): {res['eligible']}")
say(f"S1 discovery  (BH q=0.10, net>0): {len(res['s1'])} survivors")
say(f"S2 validation (BH q=0.05 within S1): {len(res['s2'])} survivors")
say(f"S3 holdout    (BH q=0.05 within S2): {len(res['s3'])} survivors")
# top by train evidence, for transparency
rows = []
for h in hyps:
    m = (h.day >= PER.train[0]) & (h.day <= PER.train[1])
    mu, p, n, nd = ES.cluster_t(h.day[m], h.val[m] - COST)
    if n >= 300 and nd >= 150:
        mv, pv, nv, _ = ES._stat(h, PER.valid, COST)
        mo, po, no, _ = ES._stat(h, PER.oos, COST)
        rows.append((p, h.name, mu, n, mv, pv, mo, po))
rows.sort()
say("\nTop 12 by TRAIN evidence (net bps/event, one-sided p) and what happened later:")
say(f"{'hypothesis':34s} {'TRAIN net':>9} {'p':>8} {'n':>6} | {'VALID net':>9} {'p':>6} | {'OOS net':>8} {'p':>6}")
for p, nm, mu, n, mv, pv, mo, po in rows[:12]:
    say(f"{nm:34s} {mu:9.2f} {p:8.4f} {n:6d} | {mv:9.2f} {pv:6.3f} | {mo:8.2f} {po:6.3f}")
if res["s2"]:
    say("\nS2 survivors:")
    for h, mu, p, n in res["s2"]:
        say(f"  {h.name:34s} VALID net {mu:+.2f} bps p={p:.4f} n={n}")
if res["s3"]:
    say("\nS3 (holdout) survivors -> promotion gate on OOS trades:")
    pr = load_policy()["promotion"]
    for h, mu, p, n, nd in res["s3"]:
        m = (h.day >= PER.oos[0]) & (h.day <= PER.oos[1])
        order = np.argsort(h.day[m], kind="stable")
        g = G.evaluate(h.val[m][order], h.day[m][order], COST, pr)
        say(f"  {h.name:34s} OOS net {mu:+.2f} bps p={p:.4f} n={n} -> gate {g['verdict']} {g['failed']}")
# informational replication of the 4 strongest TRAIN hypotheses on BANKNIFTY (only data from 2021-09; VALID/OOS only). NOT a selection step.
try:
    MB = mats_for("BANKNIFTY", "2021-01-01")
    hb = {h.name: h for h in ES.generate(MB)}
    say("\nInformational replication on BANKNIFTY (same rule, no re-tuning), net bps/event [p]:")
    for p0, nm, *_ in rows[:4]:
        hh = hb.get(nm)
        if hh is None:
            continue
        mv, pv, nv, _ = ES._stat(hh, PER.valid, COST)
        mo, po, no, _ = ES._stat(hh, PER.oos, COST)
        say(f"  {nm:34s} VALID {mv:+6.2f} [{pv:.3f}] n={nv:4d} | OOS {mo:+6.2f} [{po:.3f}] n={no:4d}")
except Exception as e:
    say(f"BANKNIFTY replication not run: {type(e).__name__}: {e}")
# placebo
say("\nPlacebo calibration (events replaced by random same-size draws from the same opportunity pool; 30 runs): survivors per stage (S1,S2,S3)")
pl = ES.placebo(hyps, PER, COST, runs=30)
a = np.array(pl)
say(f"  mean {a.mean(0).round(2).tolist()}  max {a.max(0).tolist()}  | real run: ({len(res['s1'])}, {len(res['s2'])}, {len(res['s3'])})")
say(f"\nelapsed {time.time() - t0:.0f}s")
open(f"data/research/strategy_lab/edge_scan_{dt.date.today().isoformat()}.txt", "w").write("\n".join(LINES) + "\n")
json.dump({"tested": len(hyps), "s1": [h.name for h, *_ in res["s1"]], "s2": [h.name for h, *_ in res["s2"]], "s3": [h.name for h, *_ in res["s3"]], "placebo": pl},
          open(f"data/research/strategy_lab/edge_scan_{dt.date.today().isoformat()}.json", "w"))
