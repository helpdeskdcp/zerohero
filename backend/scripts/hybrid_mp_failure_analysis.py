"""WHERE does Hybrid MP v13 lose? (1) decompose the corrected engine's trades (cost vs gross, exits, setups, hours, stop size) and (2) falsify the strategy's
PREMISE directly: after a close-confirmed break of a confirmed swing, does NIFTY 5m continue (follow) or reverse (fade)? Tested with the pre-registered
multiple-testing pipeline (BH, TRAIN/VALID/holdout, placebo). READ-ONLY.  Usage: python scripts/hybrid_mp_failure_analysis.py"""
import collections
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import ist_minutes, load_1m  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, of_split  # noqa: E402
from app.strategy_lab import edge_scan as ES  # noqa: E402

COST = 2.0
LINES = []


def say(s=""):
    print(s)
    LINES.append(s)


b1 = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
ch = resample(b1, 5, 555, 930)
tr, _ = run(ch, fixed_params(chart_tf=5), b1=b1)
days, mod = ist_minutes(ch["t"])
say(f"== PART 1: decomposition of the corrected engine (NIFTY 5m), {len(tr)} trades 2017-2026; cost {COST} bps round trip")
for name, sub in (("TRAIN 2017-21", of_split(tr, TRAIN)), ("OOS 2024+", of_split(tr, OOS))):
    g = np.array([t.bps for t in sub]); rk = np.array([t.risk / t.entry * 1e4 for t in sub]); r = np.array([t.r for t in sub])
    say(f"\n[{name}] n={len(sub)} win(gross)={np.mean(g > 0) * 100:.0f}% | mean GROSS {g.mean():+.2f} bps | mean stop distance {rk.mean():.1f} bps -> cost = {COST / rk.mean():.2f} R | mean gross R {r.mean():+.3f} | break-even gross needed {COST:.1f} bps")
    ex = collections.defaultdict(list)
    for t in sub:
        ex[t.reason].append(t.bps)
    say("  exit reason:      " + "  ".join(f"{k} n={len(v)} mean {np.mean(v):+.1f}bps" for k, v in sorted(ex.items(), key=lambda kv: -len(kv[1]))[:6]))
    mt = collections.defaultdict(list)
    for t in sub:
        mt[t.max_t].append(t.bps)
    say("  highest target reached: " + "  ".join(f"T{k}:{len(v)} ({np.mean(v):+.1f})" for k, v in sorted(mt.items())))
    for lab, key in (("entry type", lambda t: t.entry_type), ("side", lambda t: "long" if t.side > 0 else "short"),
                     ("hour", lambda t: f"{int(mod[t.entry_i]) // 60:02d}h")):
        gr = collections.defaultdict(list)
        for t in sub:
            gr[key(t)].append(t.bps)
        say(f"  by {lab:10s}: " + "  ".join(f"{k} n={len(v)} {np.mean(v):+.1f}" for k, v in sorted(gr.items())))
    q = np.quantile(rk, [0.33, 0.66])
    for lab, m in (("small stop", rk <= q[0]), ("mid stop", (rk > q[0]) & (rk <= q[1])), ("large stop", rk > q[1])):
        say(f"  {lab:10s} (stop {rk[m].mean():.0f} bps): n={m.sum()} gross {g[m].mean():+.2f} bps, cost {COST / rk[m].mean():.2f}R, net {g[m].mean() - COST:+.2f} bps")

# ---- PART 2: premise test on a day x slot matrix
days_u, mod_u = days, mod
M = ES.build_mats(ch["t"], ch["o"], ch["h"], ch["l"], ch["c"], days_u, mod_u)
D = M.D
out = []
dayc = np.repeat(M.day[:, None], ES.NSLOT, 1)
for L in (3, 5, 8):
    brk_up = np.zeros((D, ES.NSLOT), bool); brk_dn = np.zeros((D, ES.NSLOT), bool)
    for d in range(D):
        sh = sl = np.nan
        for s in range(ES.NSLOT):
            j = s - L
            if j - L >= 0 and np.isfinite(M.H[d, j - L:j + L + 1]).all():
                hj, lj = M.H[d, j], M.L[d, j]
                if (hj > M.H[d, j - L:j]).all() and (hj >= M.H[d, j + 1:j + L + 1]).all():
                    sh = hj
                if (lj < M.L[d, j - L:j]).all() and (lj <= M.L[d, j + 1:j + L + 1]).all():
                    sl = lj
            if s and np.isfinite(sh) and M.C[d, s] > sh >= M.C[d, s - 1]:
                brk_up[d, s] = True; sh = np.nan                     # one break per swing
            if s and np.isfinite(sl) and M.C[d, s] < sl <= M.C[d, s - 1]:
                brk_dn[d, s] = True; sl = np.nan
    for h in (3, 6, 12):
        ret = np.full((D, ES.NSLOT), np.nan)
        for s in range(0, ES.NSLOT - h - 1):
            ret[:, s] = (M.C[:, s + h] / M.O[:, s + 1] - 1) * 1e4
        for bd, bm in (("up", brk_up), ("dn", brk_dn)):
            for act, sign in (("follow", 1 if bd == "up" else -1), ("fade", -1 if bd == "up" else 1)):
                out.append(ES._mk(f"BoS L{L} {bd}-break {act} h{h}", "bos", bm.ravel(), ret.ravel(), dayc.ravel(), sign))
O_ = dt.date.toordinal
EP = 719163
PER = ES.Periods(*[tuple(x - EP for x in p) for p in ((O_(dt.date(2017, 1, 1)), O_(dt.date(2021, 12, 31))), (O_(dt.date(2022, 1, 1)), O_(dt.date(2023, 12, 31))), (O_(dt.date(2024, 1, 1)), O_(dt.date(2026, 12, 31))))])
say(f"\n\n== PART 2: premise test -- does price continue after a close-confirmed swing break? {len(out)} pre-registered hypotheses (pivot 3/5/8 x up/down x follow/fade x 3/6/12 bars), cost {COST} bps")
rows = []
for hh in out:
    ev = hh.day
    m = (ev >= PER.train[0]) & (ev <= PER.train[1])
    mu, p, n, nd = ES.cluster_t(hh.day[m], hh.val[m] - COST)
    mg = hh.val[m].mean() if m.sum() else float("nan")
    mv = ES._stat(hh, PER.valid, COST)
    mo = ES._stat(hh, PER.oos, COST)
    rows.append((p, hh.name, mg, mu, n, mv[0], mo[0], mo[1]))
rows.sort()
say(f"{'hypothesis':32s} {'TRAIN gross':>11} {'TRAIN net':>9} {'p':>7} {'n':>6} | {'VALID net':>9} | {'OOS net':>8} {'p':>6}")
for p, nm, mg, mu, n, mv, mo, po in rows[:10]:
    say(f"{nm:32s} {mg:11.2f} {mu:9.2f} {p:7.4f} {n:6d} | {mv:9.2f} | {mo:8.2f} {po:6.3f}")
res = ES.pipeline(out, PER, COST, min_events=300, min_days=150)
say(f"\nBH pipeline: eligible {res['eligible']} | S1 {len(res['s1'])} | S2 {len(res['s2'])} | S3 {len(res['s3'])}")
fol = [r for r in rows if "follow" in r[1]]; fad = [r for r in rows if "fade" in r[1]]
say(f"mean TRAIN GROSS bps: follow {np.nanmean([r[2] for r in fol]):+.2f}, fade {np.nanmean([r[2] for r in fad]):+.2f}  (a continuation premise needs follow > cost {COST}; these are the raw drifts after a break)")
open("data/research/strategy_lab/hybrid_failure_analysis_2026-10-18.txt", "w").write("\n".join(LINES) + "\n")
