#!/usr/bin/env python3
"""Offline report for the Renko shadow log: do autoscalp trades whose entry was ALIGNED with a
3+ brick Renko run do better than the rest? Read-only (chanakya.db opened mode=ro).

Verdict rule (pre-declared, SMART_BOS_RESEARCH_REPORT.md): no conclusion below 300 closed
trades per group; a difference counts only if the bootstrap 95% CI of (aligned - other)
mean R excludes 0. Output is information, never a gate.
"""
import os
import random
import sqlite3
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHADOW = os.path.join(HERE, "..", "data", "renko_shadow.db")
MAIN = os.path.join(HERE, "..", "data", "chanakya.db")
MIN_N = 300


def load():
    if not os.path.exists(SHADOW):
        return {}, []
    s = sqlite3.connect(f"file:{os.path.abspath(SHADOW)}?mode=ro", uri=True)
    s.row_factory = sqlite3.Row
    shadow = {r["trade_id"]: dict(r) for r in s.execute("SELECT * FROM renko_shadow_entries")}
    m = sqlite3.connect(f"file:{os.path.abspath(MAIN)}?mode=ro", uri=True)
    m.row_factory = sqlite3.Row
    out = []
    for r in m.execute("SELECT o.trade_id, o.r_multiple, o.outcome, p.net_pnl FROM trade_exit_outcomes o "
                       "LEFT JOIN ai_paper_trades p ON p.trade_id=o.trade_id"):
        sh = shadow.get(r["trade_id"])
        if sh and sh["status"] == "OK" and r["r_multiple"] is not None:
            out.append({**sh, "r": r["r_multiple"], "net": r["net_pnl"], "outcome": r["outcome"]})
    return shadow, out


def boot_diff(a, b, n=2000, seed=3):
    rnd = random.Random(seed)
    d = []
    for _ in range(n):
        d.append(statistics.fmean(rnd.choices(a, k=len(a))) - statistics.fmean(rnd.choices(b, k=len(b))))
    d.sort()
    return d[int(.025 * n)], d[int(.975 * n) - 1]


def main():
    shadow, rows = load()
    if not shadow:
        print("no shadow log yet (data/renko_shadow.db missing or empty): the service must run with the hook active first")
        return 0
    st = {}
    for r in shadow.values():
        st[r["status"]] = st.get(r["status"], 0) + 1
    print(f"shadow rows: {len(shadow)} by status {st}; joined with a closed outcome: {len(rows)}")
    groups = {"aligned_run3": [x for x in rows if x["aligned_run3"]], "other": [x for x in rows if not x["aligned_run3"]]}
    for name, g in groups.items():
        if g:
            rs = [x["r"] for x in g]
            print(f"  {name}: n={len(g)} meanR={statistics.fmean(rs):+.3f} win={sum(x['r'] > 0 for x in g) / len(g):.2f}")
    a, b = [x["r"] for x in groups["aligned_run3"]], [x["r"] for x in groups["other"]]
    if min(len(a), len(b)) < MIN_N:
        print(f"INSUFFICIENT SAMPLE: need >= {MIN_N} closed trades per group (have {len(a)} / {len(b)}). No conclusion.")
        return 0
    lo, hi = boot_diff(a, b)
    print(f"mean R difference (aligned - other): CI95 = ({lo:+.3f}, {hi:+.3f}) ->",
          "aligned better" if lo > 0 else "aligned worse" if hi < 0 else "no demonstrated difference")
    return 0


if __name__ == "__main__":
    sys.exit(main())
