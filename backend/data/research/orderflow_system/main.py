"""CLI entry point -- see README.md's "Running it" section.

    python main.py backtest --config config/v1.yaml --symbol NIFTY --timeframe 5m
    python main.py walkforward --config config/v1.yaml --symbol NIFTY --timeframe 5m
"""
from __future__ import annotations

import argparse

from config_loader import load_config, resolve_db_path
from data import build_data_source
from engine.backtester import run_setups
from engine.walkforward import split_oos_holdout, walk_forward_folds
from metrics.performance import compute_performance
from reports.run_report import build_manifest, build_report, write_report
from strategy._common import compute_context_columns
from strategy.setup1_bull_breakout import Setup1BullBreakout
from strategy.setup2_bear_breakout import Setup2BearBreakout
from strategy.setup3_bull_reversal import Setup3BullReversal
from strategy.setup4_bear_reversal import Setup4BearReversal

SETUP_CLASSES = [Setup1BullBreakout, Setup2BearBreakout, Setup3BullReversal, Setup4BearReversal]


def build_setups(cfg: dict) -> list:
    return [cls(cfg) for cls in SETUP_CLASSES if cfg[_key_for(cls)]["enabled"]]


def _key_for(cls) -> str:
    return {"Setup1BullBreakout": "setup1_bull_breakout", "Setup2BearBreakout": "setup2_bear_breakout",
           "Setup3BullReversal": "setup3_bull_reversal", "Setup4BearReversal": "setup4_bear_reversal"}[cls.__name__]


def _load_and_prep(cfg, db_path, tick_db_path, symbol, kind, timeframe, start, end):
    from strategy.smartmoney import structure_shift
    ds = build_data_source(cfg, db_path, tick_db_path)
    df = ds.load(symbol=symbol, kind=kind, timeframe=timeframe, start=start, end=end)
    df = compute_context_columns(df, cfg)
    # Always precomputed (not gated by require_sm_alignment) so a baseline
    # (A) and SM-gated (B) run share byte-identical underlying data -- any
    # difference between them is then only ever the SM gate itself.
    df = structure_shift.precompute_swings(df, cfg["smartmoney"])
    return ds, df


def cmd_backtest(args):
    cfg = load_config(args.config)
    db_path, tick_db_path = resolve_db_path(cfg, "db_path"), resolve_db_path(cfg, "tick_db_path")
    d = cfg["data"]
    ds, df = _load_and_prep(cfg, db_path, tick_db_path, args.symbol, d["kind"], args.timeframe,
                            d["start_date"], d["end_date"])

    in_sample, oos = split_oos_holdout(df, d["oos_holdout_days"])

    in_sample_trades = run_setups(in_sample, build_setups(cfg), cfg)
    oos_trades = run_setups(oos, build_setups(cfg), cfg)
    folds = walk_forward_folds(in_sample, build_setups(cfg), cfg)

    manifest = build_manifest(cfg, ds, args.symbol, args.timeframe, d["start_date"], d["end_date"])
    manifest["in_sample_range"] = [str(in_sample.index.min()), str(in_sample.index.max())] if len(in_sample) else None
    manifest["oos_range"] = [str(oos.index.min()), str(oos.index.max())] if len(oos) else None

    in_sample_report = build_report(in_sample_trades, cfg, manifest, folds, ds.warning_banner())
    oos_report = {"performance": compute_performance(oos_trades, cfg["backtest"]["min_sample_for_verdict"]),
                 "n_trades": len(oos_trades), "warning_banner": ds.warning_banner()}

    out_dir = args.out or f"reports/run_{cfg['version']}_{args.symbol.lower()}_{args.timeframe}"
    write_report(in_sample_report, out_dir)
    import json
    from pathlib import Path
    (Path(out_dir) / "oos_report.json").write_text(json.dumps(oos_report, indent=2, default=str))

    print(f"[{args.symbol} {args.timeframe}] in-sample n={len(in_sample_trades)} "
         f"verdict={in_sample_report['global']['verdict']} expectancy_r={in_sample_report['global']['expectancy_r']}")
    print(f"[{args.symbol} {args.timeframe}] OOS (untouched holdout) n={len(oos_trades)} "
         f"verdict={oos_report['performance']['verdict']} expectancy_r={oos_report['performance']['expectancy_r']}")
    print(f"[{args.symbol} {args.timeframe}] walk-forward folds: {len(folds)}")
    print(f"Wrote {out_dir}/report.md, report.json, oos_report.json")


def cmd_walkforward(args):
    cmd_backtest(args)  # backtest already runs the full walk-forward + OOS pipeline


def cmd_sensitivity(args):
    from engine.sensitivity import robustness_score, sweep
    cfg = load_config(args.config)
    db_path, tick_db_path = resolve_db_path(cfg, "db_path"), resolve_db_path(cfg, "tick_db_path")
    d = cfg["data"]
    ds, df = _load_and_prep(cfg, db_path, tick_db_path, args.symbol, d["kind"], args.timeframe,
                            d["start_date"], d["end_date"])
    grid = {"delta_mult": [1.0, 1.5, 2.0], "stop_buffer_ticks": [3.0, 5.0, 8.0]}
    rows = sweep(df, Setup1BullBreakout, "setup1_bull_breakout", grid, cfg)
    print("SCREENING PASS (not official numbers):")
    for r in rows:
        print(r)
    print("robustness_score:", robustness_score(rows))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("backtest", cmd_backtest), ("walkforward", cmd_walkforward), ("sensitivity", cmd_sensitivity)):
        sp = sub.add_parser(name)
        sp.add_argument("--config", default="config/v1.yaml")
        sp.add_argument("--symbol", default="NIFTY")
        sp.add_argument("--timeframe", default="5m")
        sp.add_argument("--out", default=None)
        sp.set_defaults(func=fn)
    args = p.parse_args()
    args.func(args)
