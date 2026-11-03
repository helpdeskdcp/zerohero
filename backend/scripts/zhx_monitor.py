"""Every 5 minutes in market hours: ZHX signal -> Safe-AI decision (SHADOW, append-only log). python scripts/zhx_monitor.py"""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.arena.safe_ai import zhx_monitor as Z
r = Z.tick(); print(json.dumps([{"sym": x.get("symbol"), "decision": x.get("decision"), "err": x.get("error"), "reasons": (x.get("reasons") or [])[:3]} for x in r]))
