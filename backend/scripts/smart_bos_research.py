#!/usr/bin/env python3
"""Reproduce the Smart-BoS / Renko research results (read-only; never touches live DBs for writing).

  python scripts/smart_bos_research.py baseline   # Pine port as-is, TRAIN+VALID only
  python scripts/smart_bos_research.py signflip   # sign-flip null for the frozen Renko-trend config
  python scripts/smart_bos_research.py oos        # FINAL: frozen config on OOS + other instruments

Frozen config (chosen on TRAIN/VALID only, before any OOS run): k=0.12, N=3, SL=2 bricks,
step=3 bricks, exit=T4. Splits: TRAIN 2017-06..2021-12, VALID 2022..2023, OOS 2024-01..2026-10.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import numpy as np  # noqa: E402

from app.research_engines.smart_bos import data as D  # noqa: E402
from app.research_engines.smart_bos import engine as E  # noqa: E402
from app.research_engines.smart_bos import renko as R  # noqa: E402
from app.research_engines.smart_bos import renko_trend as RT  # noqa: E402
from app.research_engines.smart_bos import sim as M  # noqa: E402
from app.research_engines.smart_bos import splits as S  # noqa: E402

F = dict(k=0.12, lookback=5, N=3, sl_bricks=2, step_bricks=3, exit_model="T4")
CFG = dict(sl_bricks=F["sl_bricks"], step_bricks=F["step_bricks"])


def _print(label, tr, costs=(0, 1, 2, 3)):
    print(f"== {label}: trades={len(tr)}")
    for cb in costs:
        r = M.summarize(tr, cost_bps=cb, boot=500)
        if r["n"]:
            print(f"   cost={cb}bps meanR={r['mean_r']:+.3f} bps={r['mean_bps']:+.2f} PF={r['pf']:.2f} "
                  f"CI95R=({r['ci95_r'][0]:+.3f},{r['ci95_r'][1]:+.3f})")


def baseline():
    d = D.load_1m("NIFTY", "INDEX", "2017-01-01", "2027-01-01")
    sig, st = E.generate_signals(d, E.Params(mintick=0.05))
    print("signals", st)
    for nm, sp in (("TRAIN", S.TRAIN), ("VALID", S.VALID)):          # OOS deliberately not touched here
        ss = S.of_split(sig, sp, key=lambda s: s.day)
        for ex in ("T1", "T2", "T3", "SCALE"):
            tr, _ = M.simulate(d, ss, mode="pine", exit_model=ex)
            _print(f"Pine levels {nm} {ex}", tr, costs=(0, 1))


def signflip():
    d = D.load_1m("NIFTY", "INDEX", "2017-01-01", "2027-01-01")
    days, _ = D.ist_minutes(d["t"])
    n = len(days)
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    starts = np.nonzero(np.r_[True, days[1:] != days[:-1]])[0]
    ends = np.r_[starts[1:], n]
    body, upw, dnw = np.log(c / o), (h - np.maximum(o, c)) / c, (np.minimum(o, c) - l) / c
    for seed in range(3):
        flip = np.random.default_rng(100 + seed).random(n) < 0.5
        b2, u2, d2 = np.where(flip, -body, body), np.where(flip, dnw, upw), np.where(flip, upw, dnw)
        o2, c2, h2, l2 = o.copy(), c.copy(), h.copy(), l.copy()
        for a, b in zip(starts, ends):
            px = o[a]
            for j in range(a, b):
                oo = px
                cc = oo * np.exp(b2[j])
                o2[j], c2[j], h2[j], l2[j] = oo, cc, max(oo, cc) * (1 + u2[j]), min(oo, cc) * (1 - d2[j])
                px = cc
        bars = {"t": d["t"], "o": o2, "h": h2, "l": l2, "c": c2, "v": d["v"]}
        rs = R.build(bars, k=F["k"])
        sg = S.of_split(RT.generate(bars, rs, min_run=F["N"]), S.TRAIN, key=lambda s: s.day)
        tr, _ = M.simulate(bars, sg, mode="renko", exit_model=F["exit_model"], renko_cfg=CFG)
        _print(f"SIGNFLIP null seed{seed} TRAIN (expect gross ~0)", tr, costs=(0,))


def oos():
    jobs = [("NIFTY OOS", "NIFTY", "INDEX", S.OOS), ("NIFTY all years", "NIFTY", "INDEX", None),
            ("BANKNIFTY all", "BANKNIFTY", "INDEX", None), ("SENSEX all", "SENSEX", "INDEX", None)]
    for label, sym, kind, split in jobs:
        d = D.load_1m(sym, kind, "2017-01-01" if sym == "NIFTY" else "2021-01-01", "2027-01-01")
        rs = R.build(d, k=F["k"], lookback=F["lookback"])
        sg = RT.generate(d, rs, min_run=F["N"])
        if split:
            sg = S.of_split(sg, split, key=lambda s: s.day)
        tr, _ = M.simulate(d, sg, mode="renko", exit_model=F["exit_model"], renko_cfg=CFG)
        _print(label, tr, costs=(0, 1, 2, 3))


if __name__ == "__main__":
    {"baseline": baseline, "signflip": signflip, "oos": oos}[sys.argv[1] if len(sys.argv) > 1 else "oos"]()
