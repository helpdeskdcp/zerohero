"""Option-premium replay per SPEC_OPTION_REPLAY_2026-10-23.md. usage: python data/research/smart_uturn/run_option_replay.py <index_1m_csv> <upstox_research.db> <label>"""
import json, os, sys
import pandas as pd
sys.path.insert(0, ".")
from app.smart_uturn import cli
from app.smart_uturn.config import SignalParams
from app.smart_uturn.data import load_csv, validate_one_minute
from app.smart_uturn.data.resampler import NSE
from app.smart_uturn.option_replay import OptionReplayConfig, SqliteOptionStore, replay_options

csv, db, label = sys.argv[1:4]
clean, q = validate_one_minute(load_csv(csv), NSE); sp = SignalParams().validate(); store = SqliteOptionStore(db); res = {"label": label, "index_rows": int(len(clean)), "expiries_in_data": len(store.expiries())}
for tf in ("5", "15"):
    chart, sig = cli.build_signals(clean, tf, sp, NSE); res[tf] = {}
    for name, s in (("PRIMARY_s1.5", 1.5), ("s0.5", 0.5), ("s3.0", 3.0)):
        r = replay_options(clean, chart, sig, store, sp, OptionReplayConfig(slippage_pct=s)); res[tf][name] = r["summary"]
        if name == "PRIMARY_s1.5":
            r["trades"].to_csv(os.path.join(os.environ["CLAUDE_JOB_DIR"], "tmp", f"option_trades_{label}_{tf}.csv"), index=False)
            r["rejections"].to_csv(os.path.join(os.environ["CLAUDE_JOB_DIR"], "tmp", f"option_rej_{label}_{tf}.csv"), index=False)
    p = res[tf]["PRIMARY_s1.5"]
    print(label, "chart", tf, {k: (round(v, 2) if isinstance(v, float) else v) for k, v in p.items() if k in ("signals", "trades", "rejected", "rejection_reasons", "net_rs_total", "mean_net_rs", "mean_net_rs_ci95", "win_rate", "profit_factor", "max_drawdown_rs", "mean_net_pct_of_premium", "long_calls", "short_puts", "net_rs_calls", "net_rs_puts", "verdict")})
    print("   sens mean net Rs/trade:", {k: (round(v.get("mean_net_rs", float("nan")), 1), v["trades"]) for k, v in res[tf].items()})
json.dump(res, open(f"data/research/smart_uturn/results_option_{label}.json", "w"), indent=1, default=str)
