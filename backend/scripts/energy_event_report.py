"""Settle the recorded H-EV-NG signals with the arena rules and print every activation (empty ones included). Read-only."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from app.arena import run as AR
from app.energy_event import shadow
from app.forward_test import ledger as L
rows = shadow.read_rows(); ok, n, why = L.verify_chain(shadow.LEDGER) if rows else (True, 0, None)
sigs = [r["signal"] for r in rows if r["status"] == "SIGNAL"]
res = AR.run(sigs) if sigs else {}
print(json.dumps({"chain_ok": ok, "activations": len(rows), "by_status": {s: sum(1 for r in rows if r["status"] == s) for s in sorted({r["status"] for r in rows})},
                  "settled": {p: v["summary"] for p, v in res.items() if p != "_spread_models"}, "note": "n < 30 => INSUFFICIENT_SAMPLE whatever the sign"}, indent=1, default=str))
