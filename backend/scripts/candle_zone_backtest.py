"""Candlestick entries at 'turning zones' -- hindsight event study + CAUSAL backtest with multiple-testing control. NIFTY 5m 2017-2026, READ-ONLY.

PART A (hindsight, NOT tradable): which candle patterns / zones are over-represented at the ORIGIN of the largest moves (top 5% of the next 12 bars' excursion in ATR units)?
PART B (causal, tradable): pattern bar closes in a zone -> enter next bar open -> stop = pattern extreme (but at least FLOOR x ATR away) -> target RR x stop, 24-bar / end-of-session
exit, adverse-first, gaps fill at the open, cost 2 bps. 128 pattern hypotheses + 24 zone-only CONTROLS (does the candle pattern add anything beyond the zone?).
Staged BH-FDR: discovery 2017-21 -> validation 2022-23 -> holdout 2024+. Usage: python scripts/candle_zone_backtest.py"""
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import resample  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402
from app.research_engines.smart_bos.engine import _atr  # noqa: E402
from app.strategy_lab import candle_patterns as CP  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402

COST = 2.0
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


b1 = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
o, h, l, c = ch["o"], ch["h"], ch["l"], ch["c"]
day, mod = ist_minutes(ch["t"])
n = len(c)
atr = _atr(h, l, c, 14)
atr = np.where(np.isfinite(atr) & (atr > 0), atr, np.nan)
P = CP.patterns(o, h, l, c, atr)
Z = CP.zones(o, h, l, c, atr, day, None)
valid_bar = np.isfinite(atr) & (np.arange(n) > 70) & (mod >= 555 + 10) & (mod + 5 <= 930 - 30)          # not in the first 10 / last 30 minutes
say(f"NIFTY 5m: {n} bars, patterns per 1000 bars: " + ", ".join(f"{k} {P[k][valid_bar].mean() * 1000:.0f}" for k in list(CP.PATTERNS_LONG) + list(CP.PATTERNS_SHORT)))

# ---------------- PART A: hindsight lift at the origin of the biggest moves
fw_up = np.full(n, np.nan); fw_dn = np.full(n, np.nan)
for i in range(n - 13):
    if day[i + 12] == day[i] and np.isfinite(atr[i]):
        fw_up[i] = (h[i + 1:i + 13].max() - o[i + 1]) / atr[i]
        fw_dn[i] = (o[i + 1] - l[i + 1:i + 13].min()) / atr[i]
qa_up, qa_dn = np.nanpercentile(fw_up[valid_bar], 95), np.nanpercentile(fw_dn[valid_bar], 95)
org_up = valid_bar & (fw_up >= qa_up) & (fw_dn < qa_up * 0.5)             # biggest UP excursion without a deep dip first
org_dn = valid_bar & (fw_dn >= qa_dn) & (fw_up < qa_dn * 0.5)
say(f"\n== PART A (hindsight, not tradable): origins of the biggest 12-bar moves = top 5% excursion (up >= {qa_up:.1f} ATR: {org_up.sum()} bars; down >= {qa_dn:.1f} ATR: {org_dn.sum()} bars)")
say(f"{'feature':26s} {'P(feature)':>10s} {'P(feature|up-origin)':>20s} {'lift':>6s} | {'P(feat|down-origin)':>19s} {'lift':>6s}")
feats = {}
for k in list(CP.PATTERNS_LONG) + list(CP.PATTERNS_SHORT):
    feats[k] = P[k]
for zk in ("exhaust", "prevday", "sweep"):
    feats[f"zone {zk} (long side)"] = Z[zk][0]
    feats[f"zone {zk} (short side)"] = Z[zk][1]
for k, f in feats.items():
    base = f[valid_bar].mean()
    pu, pd_ = f[org_up].mean(), f[org_dn].mean()
    say(f"{k:26s} {base * 100:9.2f}% {pu * 100:19.2f}% {pu / base if base else float('nan'):6.2f} | {pd_ * 100:18.2f}% {pd_ / base if base else float('nan'):6.2f}")
say("lift ~ 1.0 = the feature does not identify where big moves start; precision is also tiny: a 5% base rate of a 'big move' barely moves with any pattern.")

# ---------------- PART B: causal backtest
hyps = []
cache = {}


def outcomes(key, idx, side, ext_dist_fn, floor, rr):
    ck = (key, floor, rr)
    if ck in cache:
        return cache[ck]
    e = idx + 1
    ok = e < n
    idx, e = idx[ok], e[ok]
    sd = np.maximum(ext_dist_fn(idx, e), floor * atr[idx])
    out = CP.bracket(o, h, l, c, day, idx, np.full(len(idx), side), sd, rr)
    cache[ck] = (idx, out)
    return cache[ck]


def ext_long(kind):
    def f(idx, e):
        lo = np.where(kind in ("bull_engulf", "bull_harami"), np.minimum(l[idx], l[idx - 1]), l[idx])
        return o[e] - lo
    return f


def ext_short(kind):
    def f(idx, e):
        hi = np.where(kind in ("bear_engulf", "bear_harami"), np.maximum(h[idx], h[idx - 1]), h[idx])
        return hi - o[e]
    return f


def add(name, fam, idx, side, ext, floor, rr, key):
    ii, out = outcomes(key, idx, side, ext, floor, rr)
    m = np.isfinite(out)
    hyps.append(ES.Hyp(name, fam, day[ii][m], out[m], np.zeros(0), np.zeros(0), side))


for side, plist, extf in ((1, CP.PATTERNS_LONG, ext_long), (-1, CP.PATTERNS_SHORT, ext_short)):
    zi = 0 if side == 1 else 1
    for pk in plist:
        for zk in CP.ZONES:
            idx = np.flatnonzero(P[pk] & Z[zk][zi] & valid_bar)
            for floor in (1.0, 2.0):
                for rr in (1.0, 2.0):
                    add(f"{'L' if side == 1 else 'S'} {pk} @{zk} stop>={floor}ATR rr{rr:g}", "pattern", idx, side, extf(pk), floor, rr, (pk, zk))
    for zk in CP.ZONES:                                                       # CONTROL: zone only, direction of the bar's close, no named pattern
        dirbar = (c > o) if side == 1 else (c < o)
        idx = np.flatnonzero(dirbar & Z[zk][zi] & valid_bar)
        for floor in (1.0, 2.0):
            for rr in (1.0, 2.0):
                add(f"{'L' if side == 1 else 'S'} CONTROL zone-only @{zk} stop>={floor}ATR rr{rr:g}", "control", idx, side,
                    (lambda idx, e, side=side: (o[e] - l[idx]) if side == 1 else (h[idx] - o[e])), floor, rr, ("ctl", zk, side))
say(f"\n== PART B (causal): {len(hyps)} hypotheses ({sum(x.family == 'pattern' for x in hyps)} pattern, {sum(x.family == 'control' for x in hyps)} zone-only controls), cost {COST} bps, entry next bar open")
EP = 719163
O_ = dt.date.toordinal
PER = ES.Periods(*[tuple(x - EP for x in p) for p in ((O_(dt.date(2017, 1, 1)), O_(dt.date(2021, 12, 31))), (O_(dt.date(2022, 1, 1)), O_(dt.date(2023, 12, 31))), (O_(dt.date(2024, 1, 1)), O_(dt.date(2026, 12, 31))))])
res = ES.pipeline(hyps, PER, COST, min_events=150, min_days=100)
say(f"eligible {res['eligible']} | S1 discovery (BH q=0.10) {len(res['s1'])} | S2 validation (q=0.05) {len(res['s2'])} | S3 holdout (q=0.05) {len(res['s3'])}")
rows = []
for hh in hyps:
    m = (hh.day >= PER.train[0]) & (hh.day <= PER.train[1])
    mu, p, nn, nd = ES.cluster_t(hh.day[m], hh.val[m] - COST)
    if nn >= 150 and nd >= 100:
        mv, pv, nv, _ = ES._stat(hh, PER.valid, COST)
        mo, po, no_, _ = ES._stat(hh, PER.oos, COST)
        allv = hh.val - COST
        rows.append((p, hh.name, hh.family, mu, nn, np.mean(hh.val[m] - COST > 0) * 100 if m.sum() else float("nan"), mv, mo, po))
rows.sort()
say(f"\nTop 14 by TRAIN evidence: {'hypothesis':58s} {'TRAIN net':>9} {'win%':>5} {'p':>6} {'n':>5} | {'VALID':>6} | {'2024+':>6} {'p':>6}")
for p, nm, fam, mu, nn, w, mv, mo, po in rows[:14]:
    say(f"   {nm:60s} {mu:9.2f} {w:5.0f} {p:6.3f} {nn:5d} | {mv:6.2f} | {mo:6.2f} {po:6.3f}")
for fam in ("pattern", "control"):
    sub = [r for r in rows if r[2] == fam]
    say(f"{fam:8s}: {len(sub)} eligible hypotheses | mean TRAIN net {np.mean([r[3] for r in sub]):+.2f} bps | mean 2024+ net {np.mean([r[7] for r in sub]):+.2f} bps | share with 2024+ net>0: {np.mean([r[7] > 0 for r in sub]) * 100:.0f}%")
say("\nwin rate of pattern entries by geometry (mean over eligible pattern hypotheses, TRAIN): " + ", ".join(
    f"rr{rr:g}: {np.mean([r[5] for r in rows if r[2] == 'pattern' and f'rr{rr:g}' in r[1]]):.0f}%" for rr in (1.0, 2.0)))
open("data/research/strategy_lab/candle_zone_backtest_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
