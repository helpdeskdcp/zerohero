"""Cache the 1-minute option candles of every shown INSTITUTION_TRADER signal (finished days only) so the chart page keeps working after the capture database is trimmed. Read-only on the capture DB.
python scripts/mohit_cache_build.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.arena import mohit_view as MV

rows = MV.list_signals(); ok = empty = 0
for r in rows:
    out = MV.chart_for(r["id"], 1, fresh=True)
    n = len((out or {}).get("candles") or [])
    print(r["date"], r["time"], r["id"], r["underlying"], int(r["strike"]), r["option_type"], "entry", r["entry"], "sl", r["sl"], "T", r["t1"], r["t2"], r["t3"], "| candles", n)
    ok += 1 if n else 0; empty += 0 if n else 1
print({"signals": len(rows), "with_candles": ok, "empty": empty})
