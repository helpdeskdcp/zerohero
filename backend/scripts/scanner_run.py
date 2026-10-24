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
    if group == "STOCKS":
        by = {}
        for h in res["new_hits"]: by.setdefault(h["scan"], []).append(h["inst"])
        lines = [f"{k} ({len(v)}): {', '.join(v[:6])}{' ...' if len(v) > 6 else ''}" for k, v in sorted(by.items(), key=lambda kv: -len(kv[1]))]
        tail = "\nObservation only. 53-stock back-test (2019-26): 0 of 36 scans passed; breakout scans have UNDERperformed the basket (about -20 bp over 5 days). A hit is a candidate, not a trade."
    else:
        lines = [f"{h['inst']}: {h['scan']} ({h['date']})" for h in res["new_hits"]]; tail = "\nObservation only. Historical test (NIFTY/BN/SENSEX): no scan passed the pre-registered gates."
    telegram._send("ZeroHero scanner (" + (group or "ALL") + ", daily close) new hits:\n" + "\n".join(lines[:25]) + tail, os.environ.get("TELEGRAM_CHAT_ID"))
