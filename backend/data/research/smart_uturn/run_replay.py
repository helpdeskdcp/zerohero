"""Replay per SPEC_REPLAY_2026-10-23.md. usage: python data/research/smart_uturn/run_replay.py <one_minute_csv> <label> [P1|P2|ALL]"""
import json, os, sys
import pandas as pd
sys.path.insert(0, ".")
from app.smart_uturn import cli, reports
from app.smart_uturn.backtest import run_backtest
from app.smart_uturn.config import BacktestParams, SignalParams
from app.smart_uturn.data import load_csv, validate_one_minute
from app.smart_uturn.data.resampler import NSE

path, label = sys.argv[1], sys.argv[2]; per = sys.argv[3] if len(sys.argv) > 3 else "ALL"
PER = {"P1": ("2015-01-01", "2019-12-31"), "P2": ("2020-01-01", "2025-12-31"), "ALL": ("1900-01-01", "2100-01-01")}
raw = load_csv(path); a, b = (pd.Timestamp(x, tz="Asia/Kolkata") for x in PER[per]); raw = raw[(raw.index >= a) & (raw.index < b + pd.Timedelta(days=1))]
clean, q = validate_one_minute(raw, NSE); sp = SignalParams().validate(); res = {"label": label, "period": per, "quality": {k: v for k, v in q.items() if k != "sessions_partial"}}
for tf in ("5", "15"):
    chart, sig = cli.build_signals(clean, tf, sp, NSE); res[tf] = {}
    runs = {"PRIMARY": BacktestParams()}
    for k, bp in {"cost0": BacktestParams(cost_points_roundtrip=0.0), "cost4": BacktestParams(cost_points_roundtrip=4.0), "SIGNAL_CLOSE(optimistic)": BacktestParams(entry_mode="SIGNAL_CLOSE"),
                  "exit_T1": BacktestParams(exit_model="T1"), "exit_T2": BacktestParams(exit_model="T2"), "exit_T3": BacktestParams(exit_model="T3")}.items(): runs[k] = bp
    for k, bp in runs.items():
        s = run_backtest(chart, sig, bp, sp)["summary"]
        res[tf][k] = {x: s.get(x) for x in ("signals", "trades", "rejected", "net_points", "gross_points", "win_rate", "profit_factor", "expectancy_points", "expectancy_R", "expectancy_R_ci95", "max_drawdown_pct", "max_consecutive_losses", "verdict")}
    print(label, per, "chart", tf, "PRIMARY", {x: (round(v, 3) if isinstance(v, float) else v) for x, v in res[tf]["PRIMARY"].items() if x in ("trades", "gross_points", "net_points", "win_rate", "profit_factor", "expectancy_R", "expectancy_R_ci95", "verdict")})
    print("      sens:", {k: (v["trades"], round(v["expectancy_R"], 3) if v["expectancy_R"] is not None else None) for k, v in res[tf].items()})
json.dump(res, open(os.path.join("data/research/smart_uturn", f"results_{label}_{per}.json"), "w"), indent=1, default=str)
