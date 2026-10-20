"""Settle and compare BOTH sides with the identical engine (read-only). usage: arena_run.py [--start YYYY-MM-DD] [--policy T1|LAST] [--capital 50000]"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.arena import run as R
from app.forward_test import ledger as L
from app.arena import submit as SU

ap = argparse.ArgumentParser(); ap.add_argument("--start", default="2026-10-26"); ap.add_argument("--policy", default="T1"); ap.add_argument("--capital", type=float, default=50000.0); a = ap.parse_args()
ok, n, err = (L.verify_chain(SU.PATH) if os.path.exists(SU.PATH) else (True, 0, None))
sigs = R.ai_signals("data/chanakya.db", a.start) + [s for s in R.human_signals() if s["t"] >= 0]
out = R.run(sigs, policy=a.policy, capital=a.capital); os.makedirs("data/arena", exist_ok=True)
json.dump(out, open(f"data/arena/result_{a.start}.json", "w"), indent=1, default=str)
f = lambda x, d=2: "n/a" if x is None else f"{x:.{d}f}"
md = [f"# Arena result (start {a.start}, exit policy {a.policy}, capital {a.capital:,.0f}, 1 lot per signal)", f"human ledger chain: {'OK' if ok else 'BROKEN ' + str(err)} ({n} signals)", ""]
for p, v in out.items():
    if p.startswith("_"): continue
    s = v["summary"]; md.append(f"## {p}"); md.append(f"- signals {s['signals']} · settled {s['settled']} · {s['by_status']}")
    if s["settled"]:
        md.append(f"- NET Rs {s['net_rs']:+.0f} ({s['return_pct']:+.2f}% of capital) · gross {s['gross_rs']:+.0f} · costs {s['cost_rs']:.0f} · win rate {s['win_rate']:.1%} · PF {f(s['profit_factor'])} · max DD Rs {s['max_drawdown_rs']:.0f} ({s['max_drawdown_pct']:.1f}%) · fills on real bid/ask {s['real_quote_fill_share']:.0%}")
md += ["", "spread models used: " + json.dumps({k: v["source"] for k, v in out["_spread_models"].items()}), "",
       "Win rate alone never decides; primary = net return after costs. A difference inside the sampling noise is a DRAW. Snapshot resolution ~1-3 min; SENSEX/MCX use modelled spreads (both sides). Not a profitability claim."]
open(f"data/arena/RESULT_{a.start}.md", "w").write("\n".join(md)); print("\n".join(md))
