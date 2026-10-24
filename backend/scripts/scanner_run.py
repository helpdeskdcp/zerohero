"""Daily scanner run: refresh daily bars, evaluate the library on the last completed bar, store hits, optional private Telegram summary.
   python scripts/scanner_run.py --group NSE|MCX [--send]      cron: NSE 15:50 IST, MCX 23:45 IST (Mon-Fri). Observation only."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
for l in open(os.path.join(HERE, ".env")):
    if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
from app.scanner import engine
group = sys.argv[sys.argv.index("--group") + 1] if "--group" in sys.argv else None
res = engine.run(group=group); print(json.dumps(res, default=str)[:3000])
if "--send" in sys.argv and res["new_hits"]:
    from app.connectors import telegram
    lines = [f"{h['inst']}: {h['scan']} ({h['date']})" for h in res["new_hits"]]
    telegram._send("ZeroHero scanner (" + (group or "ALL") + ", daily close) new hits:\n" + "\n".join(lines[:25]) + "\nObservation only. Historical test (NIFTY/BN/SENSEX): no scan passed the pre-registered gates.", os.environ.get("TELEGRAM_CHAT_ID"))
