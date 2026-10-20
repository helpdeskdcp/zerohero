"""Residual-edge check of the PA baseline (711 trades) against NIFTY buy&hold, using the vetted residual-edge-analyzer skill (~/.claude/skills).
Daily strategy return = sum of that day's per-trade returns (1x notional per trade, bps/1e4); non-trade days = 0. Market = NIFTY index close-to-close.
Regime = trailing-20d market vol vs its EXPANDING median (causal, shifted a day); first 60 days 'warmup'. analysis_scope=in_sample (no independent holdout)."""
import csv, json, os, subprocess, sys, datetime as dt
from collections import defaultdict
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.research_engines.smart_bos.data import load_1m

IST = dt.timedelta(hours=5, minutes=30)
b1 = load_1m("NIFTY", "INDEX", start="2021-01-01", end="2027-01-01", prefer="ANGELONE_CANDLES")
last = {}
for t, c in zip(b1["t"], b1["c"]):
    d = (dt.datetime.utcfromtimestamp(int(t)) + IST).strftime("%Y-%m-%d"); last[d] = float(c)
days = sorted(last); closes = np.array([last[d] for d in days]); mret = closes[1:] / closes[:-1] - 1; days = days[1:]
g, n = defaultdict(float), defaultdict(float)
for r in csv.DictReader(open("data/research/pa_of_lab/pa_baseline_trades.csv")):
    g[r["date"]] += float(r["gross_bps"]) / 1e4; n[r["date"]] += float(r["net_bps"]) / 1e4
vol = np.array([np.std(mret[max(0, i - 20):i], ddof=1) if i >= 20 else np.nan for i in range(len(mret))])
reg = []
for i in range(len(mret)):
    hist = vol[20:i][~np.isnan(vol[20:i])] if i > 20 else np.array([])
    reg.append("warmup" if i < 80 or len(hist) < 60 else ("high" if vol[i - 1] > np.median(hist) else "low"))
out = "data/research/pa_of_lab/pa_residual_edge"; os.makedirs(out, exist_ok=True)
with open(f"{out}/daily.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "strategy_gross", "strategy_net", "market_return", "vol_regime"])
    for d, m, rg in zip(days, mret, reg): w.writerow([d, f"{g.get(d, 0):.8f}", f"{n.get(d, 0):.8f}", f"{m:.8f}", rg])
print("days", len(days), "trade-days", len(set(g)), "market n", len(mret))
skill = os.path.expanduser("~/.claude/skills/residual-edge-analyzer/scripts/analyze_residual_edge.py")
for col, basis in (("strategy_gross", "gross"), ("strategy_net", "net")):
    cfg = {"schema_version": "1.0", "date_column": "date", "strategy_column": col, "return_unit": "decimal", "frequency": "daily",
           "primary_model": {"name": "market", "baseline_columns": ["market_return"]}, "regime_columns": ["vol_regime"], "rolling_window": 63,
           "minimum_observations": 60, "minimum_regime_observations": 20, "minimum_rolling_windows": 12, "hac_lags": "auto", "include_series": False,
           "data_declarations": {"baseline_selection": "predeclared", "strategy_return_basis": basis, "baseline_return_basis": basis, "analysis_scope": "in_sample", "universe_data": "not_applicable"}}
    json.dump(cfg, open(f"{out}/cfg_{basis}.json", "w"), indent=1)
    r = subprocess.run([sys.executable, skill, "--input", f"{out}/daily.csv", "--config", f"{out}/cfg_{basis}.json", "--output-json", f"{out}/report_{basis}.json", "--output-markdown", f"{out}/report_{basis}.md"], capture_output=True, text=True)
    print(basis, "rc", r.returncode, r.stderr[-300:] if r.returncode else "")
