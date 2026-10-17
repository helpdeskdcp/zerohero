"""Run every available candidate through the promotion gate (net of cost, out-of-sample only). READ-ONLY research; paper is the most any
strategy can ever earn. Costs are explicit assumptions: index futures/spot-derived 2 bps round trip, MCX futures 3 bps (stress 1.5x in the gate).
Usage: python scripts/strategy_gate_run.py   -> prints + data/research/strategy_lab/gate_report_<date>.txt"""
import datetime as dt
import os
import sys

import numpy as np

sys.path.insert(0, ".")
from app.hybrid_orderflow.policy import load_policy  # noqa: E402
from app.research_engines.hybrid_mp.engine import fixed_params, resample, run as hmp_run  # noqa: E402
from app.research_engines.smart_bos import engine as SBE  # noqa: E402
from app.research_engines.smart_bos import sim as SIM  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402
from app.research_engines.smart_bos.splits import OOS, of_split  # noqa: E402
from app.strategy_lab import gate as G  # noqa: E402

PR = load_policy()["promotion"]
IDX_COST, MCX_COST = 2.0, 3.0
rows = []


def judge(name, trades, cost, oos_only=True, informational=False):
    tr = of_split(trades, OOS) if oos_only else trades
    tr = sorted(tr, key=lambda t: (t.day, t.entry_i))
    r = G.evaluate([t.bps for t in tr], [t.day for t in tr], cost, PR)
    if informational:                                     # in-sample view: shown for context, can NEVER promote
        r = {**r, "verdict": "IN_SAMPLE_INFO_ONLY(" + r["verdict"] + ")"}
    rows.append((name, r))
    ci = r.get("net_ci95_bps")
    print(f"{name:42s} n={r['n']:5d} days={r.get('distinct_days', 0):4d} net={r.get('net_mean_bps', float('nan')):+7.2f}bps "
          f"CI={'(%+.1f,%+.1f)' % ci if ci else 'n/a':16s} -> {r['verdict']} {r['failed']}")


def hmp(sym, kind, label, cost, start="2017-01-01"):
    b1 = load_1m(sym, kind, start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    tr, _ = hmp_run(resample(b1, 5, 555 if kind == "INDEX" else 540, 930 if kind == "INDEX" else 1410),
                    fixed_params(chart_tf=5, **({} if kind == "INDEX" else {"session": (540, 1410)})), b1=b1)
    judge(f"hybrid_mp v13 fixed 5m {label}", tr, cost, oos_only=(kind == "INDEX"))


for sym, label, start in (("NIFTY", "NIFTY", "2017-01-01"), ("BANKNIFTY", "BANKNIFTY", "2021-01-01"), ("SENSEX", "SENSEX", "2021-01-01")):
    try:
        hmp(sym, "INDEX", label, IDX_COST, start)
    except Exception as e:
        print(f"{label}: not evaluated ({type(e).__name__}: {e})")
for sym in ("CRUDEOIL", "NATURALGAS"):
    try:
        hmp(sym, "FUTURE", f"{sym} fut", MCX_COST, "2026-01-01")
    except Exception as e:
        print(f"{sym}: not evaluated ({type(e).__name__}: {e})")

try:                                                    # smart_bos (Pine port) on NIFTY 1m bars, default params, T2 exit
    b = load_1m("NIFTY", "INDEX", start="2017-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
    sigs, _ = SBE.generate_signals(b)
    tr, _ = SIM.simulate(b, sigs, mode="pine", exit_model="T2")
    judge("smart_bos pine NIFTY 1m (T2)", tr, IDX_COST)
except Exception as e:
    print(f"smart_bos: not evaluated ({type(e).__name__}: {e})")

class _T:                                                 # minimal trade record for non-simulated candidates
    def __init__(self, day, bps, i):
        self.day, self.bps, self.entry_i = day, bps, i


def overnight(sym, label, start):
    """Pre-registered, untuned: buy the last 1m close of a session, sell the next session's first open (index spot-derived proxy for futures)."""
    from app.research_engines.smart_bos.data import ist_minutes
    b = load_1m(sym, "INDEX", start=start, end="2027-01-01", prefer="ANGELONE_CANDLES")
    days, mod = ist_minutes(b["t"])
    ok = (mod >= 555) & (mod < 930)
    d, o, c = days[ok], b["o"][ok], b["c"][ok]
    first = {}
    last = {}
    for i, x in enumerate(d):
        first.setdefault(x, o[i])
        last[x] = c[i]
    ds = sorted(first)
    trades = [_T(int(a), (first[bn] / last[a] - 1) * 1e4, k) for k, (a, bn) in enumerate(zip(ds, ds[1:])) if bn - a <= 4 and last[a] > 0]
    judge(f"overnight long {label} (close->next open)", trades, IDX_COST)
    judge(f"overnight long {label} ALL YEARS (in-sample)", trades, IDX_COST, oos_only=False, informational=True)


for sym, start in (("NIFTY", "2017-01-01"), ("BANKNIFTY", "2021-01-01")):
    try:
        overnight(sym, sym, start)
    except Exception as e:
        print(f"overnight {sym}: not evaluated ({type(e).__name__}: {e})")

out = ["# Promotion gate report " + dt.date.today().isoformat(), "Costs assumed: index 2 bps, MCX futures 3 bps round trip; OOS = 2024+ for indices, all 2026 data for MCX (short).", ""]
for name, r in rows:
    out.append(f"{name}: {r['verdict']}  n={r['n']} days={r.get('distinct_days')} net={r.get('net_mean_bps')} CI={r.get('net_ci95_bps')} folds={r.get('fold_net_means_bps')} failed={r['failed']}")
promoted = [n for n, r in rows if r["verdict"] == G.PROMOTE]            # informational rows carry a different verdict string and are excluded
out += ["", f"PROMOTED TO PAPER: {promoted if promoted else 'NONE'}"]
os.makedirs("data/research/strategy_lab", exist_ok=True)
open(f"data/research/strategy_lab/gate_report_{dt.date.today().isoformat()}.txt", "w").write("\n".join(out) + "\n")
print("\n" + out[-1])
