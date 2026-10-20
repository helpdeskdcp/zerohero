"""Replay the AI side's recent AUTOSCALP paper signals under the arena settlement rules (read-only; writes only the result files). Spec: data/research/arena_backtest/SPEC_2026-10-21.md
  python scripts/arena_ai_backtest.py 2026-10-11 2026-10-20"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
for line in open(os.path.join(HERE, ".env")):
    if "=" in line and not line.startswith("#"): k, v = line.strip().split("=", 1); os.environ.setdefault(k, v)
from app.arena import run as R
a, b = sys.argv[1], sys.argv[2]
db = os.environ.get("CHANAKYA_DB_PATH") or os.path.join(HERE, "data", "chanakya.db")
sigs = R.ai_signals(db, a, b)
res = R.run(sigs)
out = {"window": [a, b], "n_signals": len(sigs), "result": {p: v for p, v in res.items() if p != "_spread_models"}, "spread_models": res.get("_spread_models")}
d = os.path.join(HERE, "data", "research", "arena_backtest"); os.makedirs(d, exist_ok=True)
json.dump(out, open(os.path.join(d, f"RESULT_{a}_{b}.json"), "w"), indent=1, default=str)
print(json.dumps({"n_signals": len(sigs), "summary": {p: v["summary"] for p, v in out["result"].items()}}, indent=1, default=str))
