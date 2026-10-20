"""Write the rules inventory report (markdown + json). READ-ONLY.  usage: rules_inventory_report.py [out_dir=.]"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.rules_registry import report, scan

out = sys.argv[1] if len(sys.argv) > 1 else "."
s = scan.scan(); os.makedirs(out, exist_ok=True)
open(os.path.join(out, "RULES_INVENTORY_2026-10-20.md"), "w").write(report.render(s))
json.dump(s, open(os.path.join(out, "rules_inventory_2026-10-20.json"), "w"), indent=1, default=str)
print(f"rules {len(s['rules'])} · problems {len(s['problems'])} · unclassified env {s['unclassified_env']} · conflicts {[c['concept'] for c in s['conflicts']]}")
for p in s["problems"]: print(" -", p)
