"""Bounded offline sample: wall time, peak RSS, tracemalloc peak and payload size for build_chart over the tick-covered (symbol,date) pairs. Read-only."""
import json, os, resource, sys, time, tracemalloc
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.pa_of_lab.api import available, build_chart

pairs = [(a["symbol"], a["date"]) for a in available() if a["symbol"] in ("NIFTY", "BANKNIFTY", "NATURALGAS")][:8]
print("pair                     tf  days bars  build_s  cpu_s  tracemalloc_MB  json_KB  json_s")
for tf in (300, 60):
    for sym, d in pairs[:4]:
        tracemalloc.start(); c0 = time.process_time(); t0 = time.time()
        p = build_chart(sym, d, tf, "ticks", 1)
        t1 = time.time(); c1 = time.process_time()
        s = json.dumps(p); t2 = time.time(); peak = tracemalloc.get_traced_memory()[1] / 1e6; tracemalloc.stop()
        print(f"{sym:10s} {d} {tf:4d}  1  {len(p['bars']):4d}  {t1 - t0:7.2f} {c1 - c0:6.2f} {peak:14.1f} {len(s) / 1024:8.0f} {t2 - t1:7.3f}")
print("process peak RSS MB:", round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1))
