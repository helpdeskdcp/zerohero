"""Replay the unified policy over the paper ledger (read-only). usage: rules_engine_replay.py [STRATEGY|ALL=ALL] [capital=200000]"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.rules_engine.engine import Engine
from app.rules_engine.replay import load_ledger, replay

strat = sys.argv[1] if len(sys.argv) > 1 else "ALL"; cap = float(sys.argv[2]) if len(sys.argv) > 2 else 200000.0
eng = Engine.load(); rows = load_ledger("data/chanakya.db", strat); r = replay(rows, eng, capital=cap); s = r["summary"]
os.makedirs("data/research/rules_engine", exist_ok=True)
json.dump(r, open(f"data/research/rules_engine/replay_{strat}.json", "w"), indent=1, default=str)
f = lambda g: f"n={g['n']} sum={g['sum']:+.0f} mean={g['mean']} win={g['win_rate']}"
md = [f"# Rules-engine replay — policy {eng.p['policy_version']} ({eng.p['validation_status']}) · strategy {strat} · capital {cap:,.0f}", "",
      f"- ledger: {s['trades']} closed trades over {s['days']} days", f"- recorded (all):   {f(s['all'])}", f"- policy ALLOWS:     {f(s['allowed'])}", f"- policy BLOCKS:     {f(s['blocked'])}",
      f"- blocks by rule: {s['blocked_by_rule']}", f"- NOT evaluable on this ledger (inputs absent, never guessed): {r['not_evaluable']}", "",
      "Diagnostic only: sequential replay ignores path dependence; allowed trades' P&L is as recorded; this is not a forecast and not evidence of edge."]
open(f"data/research/rules_engine/replay_{strat}.md", "w").write("\n".join(md)); print("\n".join(md))
