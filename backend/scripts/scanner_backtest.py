"""Historical test as pre-registered in data/research/scanner/SPEC_2026-10-24.md. Writes data/research/scanner/results_backtest.json."""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.scanner import backtest as BT
r = BT.run_all()
json.dump(r, open(os.path.join(HERE, "data", "research", "scanner", "results_backtest.json"), "w"), indent=1, default=float)
print("instruments", r["instruments"])
print(f"{'scan':12}{'side':6}{'n(5d)':>6} {'verdict':34}" + "".join(f"{'h'+str(h)+' mean/hit':>18}" for h in BT.HORIZONS))
for k, v in r["scans"].items():
    print(f"{k:12}{v['side']:6}{v['horizons'].get(5, {}).get('n', 0):6d} {v['verdict']:34}" + "".join(f"{(str(round(v['horizons'][h]['mean_net_bps'], 1)) + '/' + str(round(100 * v['horizons'][h]['hit'])) + '%') if h in v['horizons'] else '-':>18}" for h in BT.HORIZONS))
