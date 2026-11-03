"""Nightly, read-only: replay finished MCX NATURALGAS sessions (from 2026-11-03) through the unchanged Safe-AI U-turn rule and append the result to the immutable forward log. Records only; no orders. python scripts/mcx_uturn_forward.py"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.arena import mcx_forward as M
r = M.process(); print(json.dumps({"added": [(a["day"], a["thrust"], a["side"], a["net_bp"]) for a in r["added"]], "sessions": r["summary"]["sessions_observed"], "levels": {k: {x: v.get(x) for x in ("trades", "net_bp_per_trade", "status")} for k, v in r["summary"]["levels"].items()}}))
