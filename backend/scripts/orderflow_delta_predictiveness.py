"""READ-ONLY. Does INFERRED (quote-rule) delta predict the next move? Uses the real classified ticks captured by the Dhan worker
(data/dhan_orderflow_ticks.db), 1-minute bars. Crossed/locked-book rows and rows with bogus ltt are EXCLUDED (the old classifier mislabelled
crossed-book ticks as QUOTE_RULE). Descriptive only: ~5 sessions, so no go/no-go claim. Usage: python scripts/orderflow_delta_predictiveness.py"""
import collections
import sqlite3
import sys

import numpy as np

DB = "data/dhan_orderflow_ticks.db"
db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
rows = db.execute("select security_id,ltt,ltp,ltq,side,classification_method,best_bid,best_ask from classified_ticks "
                  "where ltt>1500000000 order by security_id,ltt,id").fetchall()
by = collections.defaultdict(list)
dropped = collections.Counter()
for sid, ltt, ltp, ltq, side, meth, bb, ba in rows:
    if bb and ba and bb >= ba:
        dropped["crossed_or_locked_book"] += 1
        continue
    by[sid].append((ltt, ltp, ltq, side))
print(f"ticks used={sum(len(v) for v in by.values())} dropped={dict(dropped)}  (instruments={len(by)})")


def spearman(a, b):
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1]) if len(a) > 5 and np.std(ra) and np.std(rb) else float("nan")


H = 5                                   # minutes ahead
print(f"\n{'security_id':>11} {'days':>4} {'bars':>6} {'spearman(delta_pct, next-%dm ret)':>34} {'hit(sign agrees | |dpct|>=0.3)':>32} {'n_hit':>6}" % H)
for sid, ts in sorted(by.items(), key=lambda kv: -len(kv[1])):
    if len(ts) < 3000:
        continue
    bars = {}
    for ltt, ltp, ltq, side in ts:
        b = bars.setdefault(ltt // 60, {"c": ltp, "buy": 0, "sell": 0, "t": ltt})
        b["c"] = ltp if ltt >= b["t"] else b["c"]
        b["t"] = max(b["t"], ltt)
        if side == "BUY_AGGRESSOR":
            b["buy"] += ltq
        elif side == "SELL_AGGRESSOR":
            b["sell"] += ltq
    keys = sorted(bars)
    x, y, days = [], [], set()
    for k in keys:
        j = k + H
        if j not in bars:               # only exact +H minute bar: no forward-fill of missing minutes
            continue
        tot = bars[k]["buy"] + bars[k]["sell"]
        if tot <= 0:
            continue
        x.append((bars[k]["buy"] - bars[k]["sell"]) / tot)
        y.append(bars[j]["c"] / bars[k]["c"] - 1)
        days.add((k * 60 + 19800) // 86400)
    x, y = np.array(x), np.array(y)
    if len(x) < 50:
        print(f"{sid:>11} {len(days):>4} {len(x):>6}  insufficient")
        continue
    m = np.abs(x) >= 0.3
    hit = float(np.mean(np.sign(x[m]) == np.sign(y[m]))) if m.sum() else float("nan")
    print(f"{sid:>11} {len(days):>4} {len(x):>6} {spearman(x, y):>34.3f} {hit:>32.3f} {int(m.sum()):>6}")
print("\nREAD: spearman ~0 / hit ~0.5 => no predictive content in this sample. Delta is INFERRED (quote rule), samples overlap (consecutive bars) so effective n is far smaller than 'bars'. Not a validation.")
