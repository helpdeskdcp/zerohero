"""python -m app.smart_uturn.cli {validate,resample,signals,backtest,features} --input one_minute.csv [options]   (run from backend/ with the venv)"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import reports, strategy
from .backtest import run_backtest
from .config import PINE_TF_MAP, BacktestParams, SignalParams
from .data import CandleStore, load_csv, validate_one_minute
from .data.resampler import MCX_1130, MCX_1155, NSE
from .data.schema import feature_availability

SESSIONS = {"nse": NSE, "mcx1130": MCX_1130, "mcx1155": MCX_1155}


def _load(a):
    raw = load_csv(a.input, ts_is=a.ts_is, interval_minutes=1)
    clean, q = validate_one_minute(raw, SESSIONS[a.session]); return raw, clean, q


def _params(a):
    sp = SignalParams(mintick=a.mintick, use_volume=a.use_volume).validate()
    bp = BacktestParams(entry_mode=a.entry_mode, exit_model=a.exit_model, position_policy=a.position_policy, cost_points_roundtrip=a.cost_points, capital=a.capital, risk_pct=a.risk_pct).validate()
    return sp, bp


def build_signals(clean, chart_tf, sp, session):
    store = CandleStore(clean, session); chart = store.get(chart_tf); tfs = [t for t in PINE_TF_MAP[chart_tf] if t]
    return chart, strategy.run(chart, chart_tf, {t: store.get(t) for t in tfs}, sp)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.smart_uturn.cli", description=__doc__)
    ap.add_argument("cmd", choices=["validate", "resample", "signals", "backtest", "features"])
    ap.add_argument("--input", required=True, help="one 1-minute OHLCV csv (datetime,open,high,low,close[,volume,oi,iv,bid,ask,...])")
    ap.add_argument("--out", default="smart_uturn_out"); ap.add_argument("--session", choices=list(SESSIONS), default="nse"); ap.add_argument("--ts-is", choices=["open", "close"], default="open")
    ap.add_argument("--chart-tf", default="5", choices=list(PINE_TF_MAP)); ap.add_argument("--mintick", type=float, default=0.05); ap.add_argument("--use-volume", action="store_true")
    ap.add_argument("--entry-mode", default="NEXT_OPEN", choices=["NEXT_OPEN", "SIGNAL_CLOSE"]); ap.add_argument("--exit-model", default="THIRDS", choices=["THIRDS", "T1", "T2", "T3"])
    ap.add_argument("--position-policy", default="IGNORE_WHILE_OPEN", choices=["IGNORE_WHILE_OPEN", "REPLACE"]); ap.add_argument("--cost-points", type=float, default=2.0)
    ap.add_argument("--capital", type=float, default=500000.0); ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--tfs", default="1,3,5,15,30,45,D,W", help="resample: timeframes to build")
    a = ap.parse_args(argv); os.makedirs(a.out, exist_ok=True)
    raw, clean, q = _load(a); avail = feature_availability(clean)
    if a.cmd == "validate":
        json.dump({"quality": q, "features": avail}, open(os.path.join(a.out, "validate.json"), "w"), indent=1, default=str); print(json.dumps(q, indent=1, default=str)); return 0
    if a.cmd == "features":
        print(json.dumps(avail, indent=1, default=str)); return 0
    store = CandleStore(clean, SESSIONS[a.session])
    if a.cmd == "resample":
        for tf in a.tfs.split(","):
            b = store.get(tf.strip()); b.to_csv(os.path.join(a.out, f"bars_{tf.strip()}.csv")); print(f"{tf.strip():>3}: {len(b)} bars, complete {int(b.complete.sum())}, with gaps {int((b.n_src < b.expected).sum()) if b.expected.notna().all() else 'n/a'}")
        return 0
    sp, bp = _params(a); chart, sig = build_signals(clean, a.chart_tf, sp, SESSIONS[a.session])
    if a.cmd == "signals":
        sig.to_csv(os.path.join(a.out, f"signals_{a.chart_tf}.csv")); print(f"BUY {int(sig.buy_signal.sum())}  SELL {int(sig.sell_signal.sum())}  blocked(buy/sell) {int(sig.blocked_buy.sum())}/{int(sig.blocked_sell.sum())}"); print(json.dumps(strategy.dashboard(sig), indent=1, default=str)); return 0
    bt = run_backtest(chart, sig, bp, sp); meta = reports.run_metadata(a.input, sp, bp, a.chart_tf, {"session": a.session, "bars": int(len(chart))})
    files = reports.write_all(a.out, meta=meta, quality=q, availability=avail, sig=sig, bt=bt); print(json.dumps(bt["summary"], indent=1, default=str)); print("wrote", *files, sep="\n  "); return 0


if __name__ == "__main__":
    sys.exit(main())
