"""INDEPENDENT recomputation of the candlestick-at-zones claims from the event-level file (pandas/numpy only; does NOT import app.strategy_lab.edge_scan,
candle_patterns or the engine): search space, eligibility, per-period cluster-robust statistics, the three BH stages, family means and detectable-effect sizes."""
import datetime as dt
import gzip
import hashlib
import math
import re
import sys

import numpy as np
import pandas as pd

FILE = "data/research/strategy_lab/candle_zone_events_2026-10-18.csv.gz"
COST = 2.0
df = pd.read_csv(FILE)
print(f"events file {FILE}: {len(df)} rows, {df.hypothesis.nunique()} hypotheses, sha256 {hashlib.sha256(open(FILE, 'rb').read()).hexdigest()[:16]}")
EP = lambda y, m, d: (dt.date(y, m, d) - dt.date(1970, 1, 1)).days
TRAIN, VALID, OOS = (EP(2017, 1, 1), EP(2021, 12, 31)), (EP(2022, 1, 1), EP(2023, 12, 31)), (EP(2024, 1, 1), EP(2026, 12, 31))
df["net"] = df.gross_bps - COST
names = df.hypothesis.unique()
pat = df[df.family == "pattern"].hypothesis.unique(); ctl = df[df.family == "control"].hypothesis.unique()
parse = lambda s: re.match(r"([LS]) (?:CONTROL zone-only |)(\w+)? ?@(\w+) stop>=([\d.]+)ATR rr(\d)", s.replace("CONTROL zone-only @", "CONTROL zone-only none @"))
print(f"SEARCH SPACE: {len(pat)} pattern + {len(ctl)} control = {len(names)}  [pattern: 8 patterns x 4 zones x 2 stop floors x 2 targets = 128; control: 2 sides x 4 zones x 2 x 2 = 32]")
print("  zones:", sorted({re.search(r'@(\w+)', n).group(1) for n in names}), "| stop floors:", sorted({re.search(r'stop>=([\d.]+)', n).group(1) for n in names}), "| targets (R):", sorted({re.search(r'rr(\d)', n).group(1) for n in names}))


def stat(x, d):
    n = len(x)
    if n < 2:
        return float("nan"), 1.0, n, 0, float("nan")
    mu = x.mean(); ud, inv = np.unique(d, return_inverse=True)
    if len(ud) < 3:
        return mu, 1.0, n, len(ud), float("nan")
    s = np.bincount(inv, weights=x - mu); D = len(ud)
    var = (s ** 2).sum() / n ** 2 * D / (D - 1)
    se = math.sqrt(var)
    return mu, 0.5 * math.erfc((mu / se) / math.sqrt(2)) if se > 0 else (0.0 if mu > 0 else 1.0), n, D, se


def bh(p, q):
    m = len(p); order = np.argsort(p); k = -1
    for r, i in enumerate(order):
        if p[i] <= (r + 1) / m * q:
            k = r
    rej = np.zeros(m, bool)
    rej[order[:k + 1]] = True
    return rej


per = lambda g, lo_hi: g[(g.epoch_day >= lo_hi[0]) & (g.epoch_day <= lo_hi[1])]
rows = []
for nm, g in df.groupby("hypothesis", sort=False):
    t = per(g, TRAIN)
    mu, p, n, D, se = stat(t.net.values, t.epoch_day.values)
    if n >= 150 and D >= 100:
        v = per(g, VALID); o = per(g, OOS)
        rows.append(dict(name=nm, fam=g.family.iloc[0], tr_mu=mu, tr_p=p, tr_n=n, tr_se=se, tr_win=(t.net > 0).mean() * 100,
                         va=stat(v.net.values, v.epoch_day.values), oo=stat(o.net.values, o.epoch_day.values)))
print(f"\nELIGIBLE (TRAIN n>=150 events and >=100 distinct days): {len(rows)} of {len(names)}   <- the '144' in the earlier report is this number; 160 hypotheses were TESTED")
p1 = np.array([r["tr_p"] for r in rows]); r1 = bh(p1, 0.10) & np.array([r["tr_mu"] > 0 for r in rows])
S1 = [r for r, k in zip(rows, r1) if k]
print(f"S1 discovery (BH q=0.10 on TRAIN p-values, mean>0): {len(S1)} survivors")
S2 = []
if S1:
    p2 = np.array([r["va"][1] for r in S1]); r2 = bh(p2, 0.05) & np.array([r["va"][0] > 0 for r in S1]); S2 = [r for r, k in zip(S1, r2) if k]
print(f"S2 validation (BH q=0.05 on 2022-23 p-values among S1): {len(S2)}")
S3 = []
if S2:
    p3 = np.array([r["oo"][1] for r in S2]); r3 = bh(p3, 0.05) & np.array([r["oo"][0] > 0 for r in S2]); S3 = [r for r, k in zip(S2, r3) if k]
print(f"S3 holdout 2024+ (BH q=0.05 among S2): {len(S3)}")
print(f"smallest TRAIN p-value overall: {p1.min():.4f}; BH threshold for rank 1 at q=0.10 with m={len(p1)}: {0.10 / len(p1):.5f} -> {'would pass' if p1.min() <= 0.10 / len(p1) else 'does not pass'}")
print(f"number of hypotheses with TRAIN p<0.05 (uncorrected): {(p1 < 0.05).sum()} of {len(p1)}  (about {0.05 * len(p1):.1f} expected by chance alone)")
for fam in ("pattern", "control"):
    s = [r for r in rows if r["fam"] == fam]
    print(f"{fam:8s}: n_hyp {len(s)}, mean TRAIN net {np.mean([r['tr_mu'] for r in s]):+.2f} bps, mean VALID {np.mean([r['va'][0] for r in s]):+.2f}, mean 2024+ {np.mean([r['oo'][0] for r in s]):+.2f}; share with 2024+ net>0: {np.mean([r['oo'][0] > 0 for r in s]) * 100:.0f}%")
ses = np.array([r["tr_se"] for r in rows])
print(f"\nDETECTABLE EFFECT (TRAIN): median standard error of a hypothesis' mean = {np.median(ses):.2f} bps; 80%-power one-sided minimum detectable net edge at alpha=0.05 ~ {2.49 * np.median(ses):.1f} bps (10th-90th pct of SE: {np.percentile(ses, 10):.2f}-{np.percentile(ses, 90):.2f}); with BH-style alpha=0.001 ~ {(3.09 + 0.84) * np.median(ses):.1f} bps")
w1 = [r["tr_win"] for r in rows if r["fam"] == "pattern" and "rr1" in r["name"]]; w2 = [r["tr_win"] for r in rows if r["fam"] == "pattern" and "rr2" in r["name"]]
print(f"mean TRAIN win rate (net>0 after cost) of pattern hypotheses: rr1 {np.mean(w1):.1f}% (the '48%' quoted in the report), rr2 {np.mean(w2):.1f}%")
