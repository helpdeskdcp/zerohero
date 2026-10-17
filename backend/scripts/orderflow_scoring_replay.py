"""READ-ONLY, PAPER-ONLY causal replay of app.hybrid_orderflow.scoring on the real Dhan ticks (5m bars built from ticks).

Pre-registered: policy thresholds are used AS SHIPPED (no tuning). At each closed 5m bar only data up to that bar is used. Entry = NEXT bar
open, exits at +3/+6 bars close; a missing bar is never forward-filled (that trade is skipped). Costs 2 bps round trip.
This is a plumbing/behaviour check and a descriptive look at SIGNAL bars -- the sample is a handful of sessions, so it can NOT validate an edge.
Usage: python scripts/orderflow_scoring_replay.py"""
import collections
import datetime as dt
import random
import sqlite3
import sys

import numpy as np

sys.path.insert(0, ".")
from app.hybrid_orderflow.bar_eval import InstrumentEvaluator  # noqa: E402
from app.hybrid_orderflow import scoring as S  # noqa: E402
from app.hybrid_orderflow.policy import load_policy  # noqa: E402

P = load_policy()
SYMS = {570750: "NATURALGAS", 569900: "CRUDEOIL", 48704: "NIFTY", 48699: "BANKNIFTY"}      # SENSEX fut: too few ticks
BAR = 300
LEN = P["smc"]["pivot_len"]
MINB = P["data"]["min_session_bars_for_features"]
COST_BPS = 2.0
HORIZ = (3, 6)
IST = 19800

db = sqlite3.connect("file:data/dhan_orderflow_ticks.db?mode=ro", uri=True)
rows = db.execute("select security_id,ltt,ltp,ltq,side,classification_method,best_bid,best_ask from classified_ticks "
                  f"where ltt>1500000000 and security_id in ({','.join(map(str, SYMS))}) order by security_id,ltt,id").fetchall()
ticks = collections.defaultdict(list)
dropped = 0
for sid, ltt, ltp, ltq, side, meth, bb, ba in rows:
    if bb and ba and bb >= ba:
        dropped += 1
        continue
    ticks[sid].append((ltt, ltp, ltq, side, meth))


# exchange tick (rupees) = Dhan master SEM_TICK_SIZE / 100 (paise), cross-checked against the observed price grid on 2026-10-17
TICKS = {570750: 0.1, 569900: 1.0, 48704: 0.1, 48699: 0.2}




def replay(sid):
    sym = SYMS[sid]
    ev = InstrumentEvaluator(sym, TICKS[sid], P)
    results = []
    for ltt, ltp, ltq, side, meth in ticks[sid]:
        results += ev.add_tick(ltt, ltp, ltq, side, meth)
    results += ev.flush(1e12)
    bars = {r["k"]: r["bar"] for r in results}
    out = []
    for r in results:
        if not r["scored"]:
            continue
        k = r["k"]
        fw = {}
        for h in HORIZ:
            a, b = bars.get(k + 1), bars.get(k + h)
            if a and b and (k + h) // (86400 // BAR) == k // (86400 // BAR):          # exact bars only, same session, never forward-filled
                fw[h] = (b["c"] / a["o"] - 1) * 1e4
        out.append({"sym": sym, "k": k, "status": r["status"], "dir": r["dir"], "total": r["total"], "reasons": r["reasons"], "fw": fw,
                    "comp": {c["component"]: c["points"] for c in r["components"]}, "creasons": {c["component"]: c["reasons"] for c in r["components"]}})
    return TICKS[sid], out


allr = []
print(f"ticks used={sum(len(v) for v in ticks.values())} crossed/locked dropped={dropped}")
for sid in SYMS:
    tick, out = replay(sid)
    allr += out
    nd = len({o['k'] * BAR // 86400 for o in out})
    print(f"{SYMS[sid]:11s} tick_size={tick} scored_bars={len(out)} days={nd}")

st = collections.Counter(o["status"] for o in allr)
print(f"\nSTATUS counts: {dict(st)}")
print("top WAIT/NO_TRADE reasons:", collections.Counter(r for o in allr if o["status"] != "SIGNAL" for r in o["reasons"]).most_common(6))
print("\ncomponent availability (mean points | share of bars with points>0 | most common reason when 0):")
for c in P["scoring"]["components_max"]:
    pts = [o["comp"].get(c, 0) for o in allr]
    rs = collections.Counter(r for o in allr if not o["comp"].get(c, 0) for r in o["creasons"].get(c, []))
    print(f"  {c:21s} mean={np.mean(pts):.2f}  active={np.mean([p > 0 for p in pts])*100:5.1f}%  {rs.most_common(1)}")

net = lambda x, d: (x if d == S.BULL else -x) - COST_BPS
gross = lambda x, d: x if d == S.BULL else -x
print("\nforward result in signal direction (bps, entry next-bar open, exit +H bars close; missing bars skipped):")
rng = random.Random(7)
pool = {h: [(o["fw"][h], o["dir"]) for o in allr if o["dir"] and h in o["fw"]] for h in HORIZ}      # every bar that had a lead direction (SIGNAL or WAIT)
for h in HORIZ:
    sig = [(o["fw"][h], o["dir"]) for o in allr if o["status"] == "SIGNAL" and h in o["fw"]]
    wl = [(o["fw"][h], o["dir"]) for o in allr if o["status"] == "WAIT" and o["dir"] and h in o["fw"]]
    if not sig:
        print(f"  H={h}: SIGNAL n=0")
        continue
    g = np.array([gross(x, d) for x, d in sig])
    null = [np.mean([gross(*rng.choice(pool[h])) for _ in range(len(sig))]) for _ in range(2000)]
    p = float(np.mean([m >= g.mean() for m in null]))
    wg = np.mean([gross(x, d) for x, d in wl]) if wl else float("nan")
    print(f"  H={h}: SIGNAL n={len(sig)} gross mean={g.mean():+.1f}bps net(2bps)={g.mean()-COST_BPS:+.1f} hit={np.mean(g>0)*100:.0f}% | "
          f"WAIT-with-lead n={len(wl)} gross mean={wg:+.1f} | random-lead-bar null p(mean>=signal)={p:.2f}")
print("\nSIGNAL bars (IST bar-open | symbol | direction | total | fwd bps H3/H6 in signal direction):")
for o in allr:
    if o["status"] == "SIGNAL":
        t = dt.datetime.fromtimestamp(o["k"] * BAR - IST, dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%m-%d %H:%M")
        f = lambda h: f"{gross(o['fw'][h], o['dir']):+.0f}" if h in o["fw"] else "n/a"
        print(f"  {t} {o['sym']:10s} {o['dir']:8s} {o['total']:.0f}  {f(3)}/{f(6)}")
print("\nREAD: tiny sample, overlapping horizons, thresholds untuned. Behaviour/plumbing check only; NOT a validation of any edge.")
