"""Backtest harness for the Hybrid Market Profile Engine v13 port. Defaults of the Pine script are NOT tuned.

Pre-declared protocol (fixed before any result was seen):
  * splits TRAIN 2017-06..2021-12, VALID 2022..2023, OOS 2024-01..  (no parameter was fitted, so every split is out-of-sample
    for the as-is script; they are reported separately to expose regime/decay)
  * PRIMARY = literal Pine exits (SL or T4), chart 1m, REALISTIC execution (next-bar-open fill, gap fills, flat at session end),
    round-trip cost 2 bps of price. Everything else is a labelled secondary view.
Usage: python scripts/hybrid_mp_research.py <primary|variants|tf|null|other> [SYMBOL]
"""
import datetime as dt
import sys
import time

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import Params, resample, run  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.sim import summarize  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, TRAIN, VALID, of_split  # noqa: E402

REAL = dict(fill="next_open", gap_fills=True, eod_flat=True)
ASIS = dict(fill="close", gap_fills=False, eod_flat=False)


def fmt(s, label):
    if not s.get("n"):
        return f"  {label:28s} n=0"
    return (f"  {label:28s} n={s['n']:5d} win={s['win']*100:4.1f}% meanR={s['mean_r']:+.3f} CI=({s['ci95_r'][0]:+.3f},{s['ci95_r'][1]:+.3f}) "
            f"PF={s['pf']:.2f} bps={s['mean_bps']:+6.2f} totR={s['total_r']:+7.1f} maxDD_R={s['max_dd_r']:.1f}")


def funnel(tr):
    n = len(tr)
    if not n:
        return ""
    cnt = [sum(1 for t in tr if t.max_t >= k) for k in (1, 2, 3, 4)]
    on = sum(1 for t in tr if t.overnight_days > 0)
    rt = sum(1 for t in tr if t.entry_type == "RETEST")
    lg = sum(1 for t in tr if t.side == 1)
    return (f"    reach T1/T2/T3/T4 = {cnt[0]/n:.0%}/{cnt[1]/n:.0%}/{cnt[2]/n:.0%}/{cnt[3]/n:.0%}  overnight={on/n:.0%} "
            f"retest={rt/n:.0%} long={lg/n:.0%} avg_hold_bars={np.mean([t.exit_i - t.entry_i for t in tr]):.0f}")


def bars_for(sym, kind, tf, mcx=False):
    b1 = load_1m(sym, kind)
    om, cm = (540, 1410) if mcx else (555, 930)
    return b1, (b1 if tf == 1 and not mcx else resample(b1, tf, om, cm)) if True else None


def splits_report(tr, costs=(0, 2), title=""):
    print(title)
    for name, sp in (("TRAIN", TRAIN), ("VALID", VALID), ("OOS", OOS)):
        sub = of_split(tr, sp)
        for c in costs:
            print(fmt(summarize(sub, cost_bps=c, boot=1000), f"{name} cost={c}bps"))
        if costs[0] == 0:
            print(funnel(sub))


def go(sym="NIFTY", kind="INDEX", tf=1, mcx=False, exit_model="PINE", exec_=REAL, **kw):
    om, cm = (540, 1410) if mcx else (555, 930)
    b1 = load_1m(sym, kind)
    chart = resample(b1, tf, om, cm)
    p = Params(chart_tf=tf, exit_model=exit_model, session=(om, cm), mintick=0.05 if not mcx else 0.1, **exec_, **kw)
    t0 = time.time()
    tr, st = run(chart, p, b1=b1 if p.require_mtf else None)
    return tr, st, time.time() - t0, chart


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "primary"
    sym = sys.argv[2] if len(sys.argv) > 2 else "NIFTY"
    if mode == "primary":
        tr, st, dt_, ch = go(sym)
        print(f"PRIMARY {sym} 1m, PINE exits, REALISTIC execution  (bars={len(ch['t'])}, trades={len(tr)}, {dt_:.0f}s)\n  engine counters: {st}")
        splits_report(tr, costs=(0, 1, 2, 3), title="-- by split (net of round-trip bps cost)")
        import datetime as _d
        print("-- by year, cost=2bps")
        by = {}
        for t in tr:
            by.setdefault(_d.date.fromordinal(719163 + t.day).year, []).append(t)
        for y, v in sorted(by.items()):
            print(fmt(summarize(v, cost_bps=2, boot=500), str(y)))
    elif mode == "variants":
        print(f"VARIANTS {sym} (1m, cost=2bps unless noted) -- every variant is declared, none was tuned")
        for label, kw in (
            ("AS-IS plotted (close fill, level exits, overnight)", dict(exec_=ASIS)),
            ("REAL + exit T1", dict(exit_model="T1")), ("REAL + exit T2", dict(exit_model="T2")),
            ("REAL + exit T3", dict(exit_model="T3")),
            ("REAL + SCALE 25% x4", dict(exit_model="SCALE")), ("REAL + SCALE_BE", dict(exit_model="SCALE_BE")),
            ("REAL + retest-only entries", dict(entry_mode="retest")),
            ("REAL + pattern filter", dict(use_pattern_filter=True)),
            ("REAL + MTF agree>=3", dict(require_mtf=True)),
            ("REAL + pivot ties strict", dict(pivot_ties="strict")),
            ("REAL + candle SL off (prev low)", dict(use_candle_sl=False)),
        ):
            tr, st, dt_, _ = go(sym, **kw)
            print(f"[{label}] trades={len(tr)}")
            for name, sp in (("TRAIN", TRAIN), ("VALID", VALID), ("OOS", OOS)):
                print(fmt(summarize(of_split(tr, sp), cost_bps=2 if "AS-IS" not in label else 0, boot=500), name))
    elif mode == "tf":
        for tf in (3, 5):
            tr, st, dt_, ch = go(sym, tf=tf)
            splits_report(tr, costs=(0, 2), title=f"TF={tf}m PINE exits, REALISTIC (bars={len(ch['t'])}, trades={len(tr)})")
    elif mode == "other":
        for s2, k, m in (("BANKNIFTY", "INDEX", False), ("SENSEX", "INDEX", False), ("NATURALGAS", "FUTURE", True), ("CRUDEOIL", "FUTURE", True)):
            tr, st, dt_, ch = go(s2, k, mcx=m)
            print(f"{s2} {k} 1m PINE exits REALISTIC trades={len(tr)}")
            for c in (0, 2):
                print(fmt(summarize(tr, cost_bps=c, boot=1000), f"all periods cost={c}bps"))
            if s2 in ("BANKNIFTY", "SENSEX"):
                for name, sp in (("VALID", VALID), ("OOS", OOS)):
                    print(fmt(summarize(of_split(tr, sp), cost_bps=2, boot=500), f"{name} cost=2bps"))
    elif mode == "null":
        b1 = load_1m(sym, "INDEX")
        days = (b1["t"] + 19800) // 86400
        lo = (dt.date(2024, 1, 1).toordinal() - 719163)
        sel = days >= lo
        b = {k: v[sel] for k, v in b1.items()}
        real_p = Params(chart_tf=1, exit_model="PINE", **REAL)
        rtr, _ = run(resample(b, 1), real_p)
        print(f"REAL OOS subset: {fmt(summarize(rtr, cost_bps=0, boot=500), 'gross')}")
        for seed in range(6):
            rng = np.random.default_rng(seed)
            pc = np.r_[b["c"][0], b["c"][:-1]]
            ln = {k: np.log(b[k] / pc) for k in ("o", "h", "l", "c")}
            flip = rng.random(len(pc)) < 0.5
            lo_, hi_, o_, c_ = ln["l"], ln["h"], ln["o"], ln["c"]
            no = np.where(flip, -o_, o_); nc = np.where(flip, -c_, c_)
            nh = np.where(flip, -lo_, hi_); nl = np.where(flip, -hi_, lo_)
            # chain: new prev-close grows by the (possibly flipped) CLOSE return of each bar
            chain = np.exp(np.cumsum(nc)) * b["c"][0]
            npc = np.r_[b["c"][0], chain[:-1]]
            sb = {"t": b["t"], "o": npc * np.exp(no), "h": npc * np.exp(nh), "l": npc * np.exp(nl), "c": chain, "v": b["v"]}
            tr, _ = run(resample(sb, 1), real_p)
            print(f"  null seed {seed}: {fmt(summarize(tr, cost_bps=0, boot=300), 'gross')}")
    elif mode == "time":
        tr, st, dt_, ch = go(sym)
        print(f"{sym} bars={len(ch['t'])} trades={len(tr)} {dt_:.1f}s", st)
