"""Input-data parity ONLY (NOT indicator/signal parity): compare hand-copied TradingView 5m OHLC against ZeroHero's
1m->5m resample (same session-anchored bucketing as engine.resample). Read-only.
Usage: python scripts/hybrid_mp_tv_manual_ohlc.py data/research/hybrid_mp/audit/tv/tv_manual.csv [--tol 0.05]
CSV header: time_ist,open,high,low,close   (time_ist = bar OPEN, e.g. 2026-10-14 09:15, exchange tz = IST)"""
import csv
import datetime as dt
import sys

import numpy as np

sys.path.insert(0, ".")
from app.research_engines.hybrid_mp.engine import resample  # noqa: E402
from app.research_engines.smart_bos.data import load_1m  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def main(path, tol):
    rows = list(csv.DictReader(open(path)))
    days = sorted({r["time_ist"][:10] for r in rows})
    b1 = load_1m("NIFTY", "INDEX", start=days[0], end=(dt.date.fromisoformat(days[-1]) + dt.timedelta(days=2)).isoformat(), use_cache=False)
    ch = resample(b1, 5)
    idx = {int(t): i for i, t in enumerate(ch["t"])}
    bad = miss = ok = 0
    for r in rows:
        e = int(dt.datetime.strptime(r["time_ist"], "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())
        i = idx.get(e)
        if i is None:
            miss += 1
            print(f"MISSING in ZeroHero: {r['time_ist']}")
            continue
        d = {k: float(r[k]) - float(ch[k[0]][i]) for k in ("open", "high", "low", "close")}
        if max(abs(x) for x in d.values()) > tol:
            bad += 1
            print(f"DIFF {r['time_ist']}: " + " ".join(f"{k}={v:+.2f}" for k, v in d.items()))
        else:
            ok += 1
    print(f"bars={len(rows)} match(tol {tol})={ok} diff={bad} missing={miss}")
    print("VERDICT: INPUT DATA " + ("MATCHES on this sample" if bad == miss == 0 else "DIFFERS") +
          " -- sample only; says nothing about indicator/signal parity (UNVERIFIED).")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[sys.argv.index("--tol") + 1]) if "--tol" in sys.argv else 0.05)
