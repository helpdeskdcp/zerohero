"""Cron entry for the H-EV-NG shadow module (Thursdays 20:03 and 21:03 IST; the module itself decides which slot is the real one and records exactly one row per Thursday).
Kill switch: ENERGY_EVENT=0.  Paper / log-only: no order path."""
import os, sys, time
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env")):
    if "=" in line and not line.startswith("#"): k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
if os.environ.get("ENERGY_EVENT", "1").strip().lower() in ("0", "false", "off", "no"): sys.exit(0)
from app.energy_event import shadow
time.sleep(12)                                              # let the R+2 candle close and be written
r = shadow.run_day()
print(time.strftime("%F %T"), r.get("status"), r.get("reason", ""), r.get("hash", "")[:12])
