"""Write the research log (one JSON line per PA / OF / combined signal) for one chart selection. Read-only on the tick DB.
usage: pa_of_lab_export.py SYMBOL DATE [tf_sec=300] [source=ticks] [out=data/research/pa_of_lab/signals_<symbol>_<date>_<tf>.jsonl]"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.pa_of_lab.api import build_chart
from app.pa_of_lab.log import write_jsonl

sym, date = sys.argv[1], sys.argv[2]
tf = int(sys.argv[3]) if len(sys.argv) > 3 else 300
src = sys.argv[4] if len(sys.argv) > 4 else "ticks"
out = sys.argv[5] if len(sys.argv) > 5 else f"data/research/pa_of_lab/signals_{sym}_{date}_{tf}_{src}.jsonl"
p = build_chart(sym, date, tf, src, 1)
n = write_jsonl(out, p)
print(f"{out}: {n} records | state {p['status']['state']} | bars {len(p['bars'])} | footprint {'yes' if p['footprint']['available'] else 'unavailable'}")
