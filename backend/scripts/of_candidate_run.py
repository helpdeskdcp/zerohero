"""Run OF-1 over every tick-covered (symbol, day). READ-ONLY. DESCRIPTIVE only until the pre-registered forward criteria are met."""
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.of_candidate import run as R

out = R.run_all(); D = "data/research/of_candidate"; os.makedirs(D, exist_ok=True)
json.dump(out, open(f"{D}/of1_result.json", "w"), indent=1, default=str)
with open(f"{D}/of1_trades.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["symbol", "day", "kind", "side", "t_signal", "entry", "exit", "exit_reason", "gross_bps", "net_bps", "hold_bars"])
    for t in out["trades"]: w.writerow([t["symbol"], t["day"], t["kind"], t["side"], t["t_signal"], t["entry"], t["exit"], t["exit_reason"], round(t["gross_bps"], 2), round(t["net_bps"], 2), t["hold_bars"]])
s = out["summary"]; f2 = lambda x, d=2: "n/a" if x is None else f"{x:.{d}f}"
L = ["# OF-1 result (spec OF_CANDIDATE_SPEC_2026-10-20.md @57973ae) — DESCRIPTIVE ONLY", "",
     f"- symbol-days with ticks: {s['symbol_days_with_ticks']} · bars with OK data quality: {f2((s['ok_bar_share'] or 0) * 100, 0)}% · signals: {s.get('signals', 0)} · trades: {s['n_trades']} · verdict: **{s['verdict']}**"]
if s["n_trades"]:
    L += [f"- win rate {s['win_rate']:.1%} (Wilson95 {s['wilson95'][0]:.1%}-{s['wilson95'][1]:.1%}) vs break-even {f2(s['breakeven_win_rate'], 3)} · mean gross {f2(s['mean_gross_bps'])} bps · mean NET {f2(s['mean_net_bps'])} bps · median net {f2(s['median_net_bps'])} · days {s['days']} · exits {s['exits']}"]
L += ["", "| symbol | day | bars | OK bars | signals | trades | gaps |", "|---|---|---|---|---|---|---|"]
for d in out["days"]: L.append(f"| {d['symbol']} | {d['day']} | {d['bars']} | {d['ok_bars']} | {d['signals']} | {d['n_trades']} | {d['gaps']} |")
L += ["", "No inference is made at this sample size. Aggressor side is inferred (quote/tick rule). Cost 6.3 bps is a placeholder for MCX."]
open(f"{D}/OF1_RESULT.md", "w").write("\n".join(L)); print("\n".join(L))
