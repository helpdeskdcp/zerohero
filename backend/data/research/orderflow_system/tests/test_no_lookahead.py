"""Spec B1: prove no look-ahead bias by shifting FUTURE data and asserting
signals computed up to bar i do not change. Uses real NIFTY data (the same
5-min series the actual backtest runs on) rather than synthetic bars, so
this exercises the exact code path the real report depends on."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pytest

from config_loader import load_config, resolve_db_path
from data import build_data_source
from strategy._common import compute_context_columns
from strategy.setup1_bull_breakout import Setup1BullBreakout
from strategy.setup2_bear_breakout import Setup2BearBreakout
from strategy.setup3_bull_reversal import Setup3BullReversal
from strategy.setup4_bear_reversal import Setup4BearReversal

SETUPS = [Setup1BullBreakout, Setup2BearBreakout, Setup3BullReversal, Setup4BearReversal]


@pytest.fixture(scope="module")
def real_df():
    cfg = load_config("config/v1.yaml")
    db_path = resolve_db_path(cfg, "db_path")
    tick_db_path = resolve_db_path(cfg, "tick_db_path")
    ds = build_data_source(cfg, db_path, tick_db_path)
    df = ds.load(symbol="NIFTY", kind="INDEX", timeframe="5m", start="2024-01-01", end="2024-06-30")
    return compute_context_columns(df, cfg), cfg


def _run_up_to(setup_cls, cfg, df, cutoff_i):
    setup = setup_cls(cfg)
    sdf = setup.prepare(df)
    state = {}
    signals = []
    for i in range(cutoff_i + 1):
        sig = setup.scan(sdf, i, state)
        if sig:
            signals.append((i, sig["entry"], sig["stop"], tuple(sig["targets"])))
    return signals


@pytest.mark.parametrize("setup_cls", SETUPS)
def test_signals_up_to_cutoff_are_unchanged_when_future_bars_are_mutated(real_df, setup_cls):
    df, cfg = real_df
    cutoff_i = len(df) // 2

    baseline = _run_up_to(setup_cls, cfg, df, cutoff_i)

    mutated = df.copy()
    rng = np.random.default_rng(42)
    future = mutated.iloc[cutoff_i + 1:]
    # Violently perturb every future bar's price/volume/orderflow columns --
    # a real look-ahead bug would very likely change SOME signal below the
    # cutoff if it read even one of these mutated future values.
    for col in ("open", "high", "low", "close", "delta", "cvd"):
        mutated.iloc[cutoff_i + 1:, mutated.columns.get_loc(col)] = (
            future[col].values * rng.uniform(0.5, 1.5, size=len(future)))
    mutated.iloc[cutoff_i + 1:, mutated.columns.get_loc("volume")] = (
        future["volume"].values * rng.uniform(0.1, 5.0, size=len(future)))
    mutated.iloc[cutoff_i + 1:, mutated.columns.get_loc("is_absorption")] = True
    mutated.iloc[cutoff_i + 1:, mutated.columns.get_loc("is_imbalance")] = True

    after_mutation = _run_up_to(setup_cls, cfg, mutated, cutoff_i)

    assert after_mutation == baseline, (
        f"{setup_cls.__name__}: signal(s) at/before bar {cutoff_i} changed after mutating ONLY bars "
        f"after {cutoff_i} -- this is a look-ahead bias bug")


@pytest.mark.parametrize("setup_cls", SETUPS)
def test_a_signal_never_fires_before_its_own_entry_bar_exists(real_df, setup_cls):
    """entry_i must always be a real, already-known index position (>= the
    scan bar i, since entries execute at i+1's open at the earliest) --
    never referencing a bar that doesn't exist yet relative to when scan()
    is called."""
    df, cfg = real_df
    setup = setup_cls(cfg)
    sdf = setup.prepare(df)
    state = {}
    for i in range(len(sdf)):
        sig = setup.scan(sdf, i, state)
        if sig:
            assert sig["entry_i"] <= i + 1, (
                f"{setup_cls.__name__}: scan at bar {i} produced an entry_i={sig['entry_i']} "
                f"that reaches further into the future than the very next bar")
