"""
Event-driven backtest engine (spec Section 4) -- bar-by-bar, stateful,
NOT a vectorized close-only shortcut. Order-flow confirmation (delta/cvd/
absorption sequencing across several bars) is inherently stateful; a
vectorized engine would have to re-implement the same bar-by-bar walk
internally anyway, just less legibly. engine/sensitivity.py's separate
screening pass is vectorized, but its numbers are NEVER the official ones
-- only this module's are.

TIMESTAMP CONVENTION (restated from strategy/_common.py, the one rule that
matters most for spec C2/B1): a setup's scan(df, i, state) may only read
columns whose value at row i depends on bar i's own close and earlier bars
-- it decides based on what's knowable AT bar i's close. The resulting
entry always executes at bar (i+1)'s OPEN (enforced inside each setup's
own scan(), not re-checked here) -- the backtester never lets a signal
fill at its own decision bar's price.
"""
from __future__ import annotations

from datetime import time as _time

import numpy as np
import pandas as pd

from engine.costs import estimate_index_future_cost
from strategy.risk import RiskState, apply_partial_booking, position_size


def is_long(direction: str) -> bool:
    return direction in ("LONG", "CE", "BUY")


def simulate_trade_with_partials(df: pd.DataFrame, entry_i: int, signal: dict, cfg: dict) -> dict:
    """Walks forward from entry_i, bar by bar, applying partial booking at
    tp1 (spec: book partial_pct at 1:2, move stop to breakeven, run
    remainder to 1:3). Never looks further ahead than the current bar being
    examined -- each decision at bar j only uses bar j's own high/low/close."""
    direction, entry, stop = signal["direction"], signal["entry"], signal["stop"]
    long_ = is_long(direction)
    plan = apply_partial_booking(entry, stop, signal["targets"], direction, cfg["risk"])
    max_bars = cfg["backtest"]["max_hold_bars"]
    n = len(df)
    entry_date = df.index[entry_i].date()

    remaining = 1.0 - plan["tp1_fraction"]
    realized_r_frac = 0.0  # R contributed by the tp1 partial, scaled by its fraction
    r_unit = abs(entry - stop)
    current_stop = stop
    tp1_done = False
    mfe_r, mae_r = 0.0, 0.0  # max favorable/adverse excursion, in R, over the trade's whole life

    def _update_excursion(row):
        nonlocal mfe_r, mae_r
        fav = (row["high"] - entry) / r_unit if long_ else (entry - row["low"]) / r_unit
        adv = (entry - row["low"]) / r_unit if long_ else (row["high"] - entry) / r_unit
        mfe_r = max(mfe_r, fav)
        mae_r = max(mae_r, adv)

    for k in range(entry_i, min(entry_i + max_bars, n)):
        row = df.iloc[k]
        if row.name.date() != entry_date:  # session end -- exit whatever remains at prior close
            exit_price = df["close"].iloc[k - 1]
            frac = remaining if tp1_done else 1.0
            r = (exit_price - entry) / r_unit if long_ else (entry - exit_price) / r_unit
            return _finish(signal, entry_i, k - 1, exit_price, realized_r_frac + frac * r,
                           "EOD", tp1_done, cfg, mfe_r, mae_r)
        _update_excursion(row)
        hit_stop = row["low"] <= current_stop if long_ else row["high"] >= current_stop
        if hit_stop:
            frac = remaining if tp1_done else 1.0
            r = (current_stop - entry) / r_unit if long_ else (entry - current_stop) / r_unit
            reason = "BE_STOP" if tp1_done else "SL"
            return _finish(signal, entry_i, k, current_stop, realized_r_frac + frac * r,
                           reason, tp1_done, cfg, mfe_r, mae_r)
        if not tp1_done:
            hit_tp1 = row["high"] >= plan["tp1"] if long_ else row["low"] <= plan["tp1"]
            if hit_tp1:
                r1 = (plan["tp1"] - entry) / r_unit if long_ else (entry - plan["tp1"]) / r_unit
                realized_r_frac += plan["tp1_fraction"] * r1
                tp1_done = True
                if plan["breakeven_after_tp1"]:
                    current_stop = plan["breakeven_price"]
                continue
        else:
            hit_tp2 = row["high"] >= plan["tp2"] if long_ else row["low"] <= plan["tp2"]
            if hit_tp2:
                r2 = (plan["tp2"] - entry) / r_unit if long_ else (entry - plan["tp2"]) / r_unit
                return _finish(signal, entry_i, k, plan["tp2"], realized_r_frac + remaining * r2,
                              "TP2", tp1_done, cfg, mfe_r, mae_r)

    j_final = min(entry_i + max_bars, n) - 1
    exit_price = df["close"].iloc[j_final]
    frac = remaining if tp1_done else 1.0
    r = (exit_price - entry) / r_unit if long_ else (entry - exit_price) / r_unit
    return _finish(signal, entry_i, j_final, exit_price, realized_r_frac + frac * r,
                   "MAXBARS", tp1_done, cfg, mfe_r, mae_r)


def _finish(signal, entry_i, exit_i, exit_price, r_multiple_gross, reason, tp1_done, cfg,
           mfe_r, mae_r) -> dict:
    entry = signal["entry"]
    cost = estimate_index_future_cost(entry, exit_price, cfg["costs"]["lot_size"],
                                      lots=cfg["costs"]["lots"], slippage_points=cfg["costs"]["slippage_points"])
    r_unit = abs(entry - signal["stop"])
    r_multiple_net = r_multiple_gross - (cost["total_cost_points"] / r_unit if r_unit else 0)
    return {
        "tag": signal["tag"], "direction": signal["direction"], "entry": entry, "stop": signal["stop"],
        "targets": signal["targets"], "entry_i": entry_i, "exit_i": exit_i, "exit": exit_price,
        "reason": reason, "tp1_hit": tp1_done, "r_multiple_gross": round(r_multiple_gross, 4),
        "r_multiple_net": round(r_multiple_net, 4), "cost_points": cost["total_cost_points"],
        "cost_detail": cost, "mfe_r": round(mfe_r, 4), "mae_r": round(mae_r, 4),
        "holding_bars": exit_i - entry_i,
    }


def run_setups(df: pd.DataFrame, setups: list, cfg: dict) -> list[dict]:
    """One full pass: for each setup, scan every bar, simulate every entry
    signal with the risk engine's session/concurrency/daily-loss gates
    applied. Returns the flat trade list (all setups combined) -- report.py
    groups by `tag`."""
    trades = []
    risk = RiskState(cfg["risk"])
    for setup in setups:
        sdf = setup.prepare(df)
        state = {}
        i = 0
        n = len(sdf)
        while i < n:
            signal = setup.scan(sdf, i, state)
            if signal is None:
                i += 1
                continue
            entry_i = signal["entry_i"]
            entry_ts = sdf.index[entry_i].to_pydatetime()
            if not risk.can_enter(entry_ts):
                i = entry_i + 1
                continue
            risk.on_entry()
            trade = simulate_trade_with_partials(sdf, entry_i, signal, cfg)
            trade["entry_ts"] = sdf.index[trade["entry_i"]]
            trade["exit_ts"] = sdf.index[trade["exit_i"]]
            risk.on_exit(sdf.index[trade["exit_i"]].to_pydatetime(), trade["r_multiple_net"])
            trades.append(trade)
            i = trade["exit_i"] + 1  # never scan for a new entry before this one has closed
    return trades
