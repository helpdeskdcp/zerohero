"""Regenerate reports/funnel_diagnostic.json -- standalone driver, not part
of the main CLI. Run after any change to confirm_breakdown()/detect_context()
that could shift condition fire rates (e.g. the 2026-09-29 zero-volume-bug
fix) so the diagnostic never goes stale relative to the code it describes.

Usage: venv/bin/python metrics/run_funnel_diagnostic.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_loader import load_config, resolve_db_path
from data import build_data_source
from metrics.funnel import run_funnel
from strategy._common import compute_context_columns
from strategy.setup1_bull_breakout import Setup1BullBreakout
from strategy.setup2_bear_breakout import Setup2BearBreakout
from strategy.setup3_bull_reversal import Setup3BullReversal
from strategy.setup4_bear_reversal import Setup4BearReversal

SETUP_CLASSES = [Setup1BullBreakout, Setup2BearBreakout, Setup3BullReversal, Setup4BearReversal]


def main() -> None:
    cfg = load_config("config/v1.yaml")
    db_path, tick_db_path = resolve_db_path(cfg, "db_path"), resolve_db_path(cfg, "tick_db_path")
    d = cfg["data"]
    out = {}
    for tf in ("5m", "15m"):
        ds = build_data_source(cfg, db_path, tick_db_path)
        df = ds.load(symbol="NIFTY", kind=d["kind"], timeframe=tf, start=d["start_date"], end=d["end_date"])
        df = compute_context_columns(df, cfg)
        for cls in SETUP_CLASSES:
            setup = cls(cfg)
            result = run_funnel(df, setup)
            out[f"{tf}_{setup.name_tag}"] = result
            print(f"{tf} {setup.name_tag}: n_bars={result['n_bars']} confirmed={result['confirmed_count']} "
                 f"bottleneck={result['bottleneck_condition']}")
    Path("reports/funnel_diagnostic.json").write_text(json.dumps(out, indent=2, default=str))
    print("Wrote reports/funnel_diagnostic.json")


if __name__ == "__main__":
    main()
