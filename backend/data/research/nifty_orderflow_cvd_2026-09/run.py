"""
NIFTY orderflow-proxy backtest -- tick-rule CVD divergence vs subsequent
price move, on real Dhan-sourced NIFTY INDEX 1-min bars (2021-09-29 to
2026-09-28, market_candles, source=DHAN_HISTORICAL).

HONEST SCOPE, stated up front because it materially changes what this
result means:
  - This is NOT a backtest of the live orderflow engine's actual signal.
    The live engine (app/engines/orderflow_engine.py) classifies each trade
    via the QUOTE RULE first (price vs real best bid/ask from Dhan's WS
    Full packets), falling back to the tick rule only when price is inside
    the spread. Historical OHLCV bars carry no bid/ask depth at all -- Dhan's
    REST historical endpoint doesn't return it -- so a genuine backtest of
    the quote-rule-driven signal is not possible against this dataset.
  - What IS tested here is the TICK-RULE-ONLY fallback classification
    (dhan_parser.classify_tick's own fallback path) applied to 1-min bars:
    a bar's full volume is counted as "buy" if close > prior close, "sell"
    if close < prior close, split/ignored if unchanged. This is a real,
    named method already used live (just not the primary one), not an
    invented proxy -- but it is cruder than the live signal and this
    backtest's result says nothing about the quote-rule path's own edge.
  - Trades are simulated on the NIFTY INDEX level itself, not on a tradeable
    instrument (future/option) -- no real execution costs are applied. A
    positive result here would still need a real-cost re-run (institutional_edge/costs.py)
    against an actual instrument before it means anything tradeable; this
    is a signal-existence screen, not a strategy result.

Hypothesis: cumulative tick-rule CVD, when it crosses its own N-period
moving average, signals directional continuation over the next M minutes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pandas as pd

from shared_harness import candles, report, simulate

DB_PATH = str(Path(__file__).resolve().parents[3] / "data" / "market_history.db")
CVD_SMA_WINDOW = 20      # minutes
HOLD_BARS = 15           # minutes
STOP_POINTS = 15.0
TARGET_POINTS = 30.0


def load_session_bars() -> pd.DataFrame:
    df = candles.load_market_candles(DB_PATH, "NIFTY", "1m", kind="INDEX", source="DHAN_HISTORICAL")
    df = df.between_time("09:15", "15:29")  # real NSE trading hours only -- drops the
    # off-hours trailing "current price" snapshot Dhan appends on every API call, and any
    # other stray pre/post-market noise, without needing to hand-pick specific rows to exclude.
    return df[df["close"] > 0]


def compute_cvd_proxy(df: pd.DataFrame) -> pd.Series:
    """Tick-rule CVD: cumulative sum of signed volume by price direction
    vs the PRIOR bar's close. Cumulative WITHIN each session only (reset
    at each new trading day) -- a CVD that silently carried across session
    gaps would produce fake "divergence" at every overnight jump."""
    prior_close = df.groupby(df.index.date)["close"].shift(1)
    direction = pd.Series(0, index=df.index)
    direction[df["close"] > prior_close] = 1
    direction[df["close"] < prior_close] = -1
    signed_vol = direction * df["volume"]
    return signed_vol.groupby(df.index.date).cumsum()


def find_cvd_cross_signals(df: pd.DataFrame, cvd: pd.Series) -> list[dict]:
    sma = cvd.groupby(df.index.date).transform(lambda s: s.rolling(CVD_SMA_WINDOW, min_periods=CVD_SMA_WINDOW).mean())
    above = cvd > sma
    cross_up = above & ~above.shift(1).fillna(False)
    cross_down = (~above) & above.shift(1).fillna(False)
    signals = []
    for ts in df.index[cross_up]:
        signals.append({"ts": ts, "direction": "LONG"})
    for ts in df.index[cross_down]:
        signals.append({"ts": ts, "direction": "SHORT"})
    return sorted(signals, key=lambda s: s["ts"])


def simulate_signals(df: pd.DataFrame, signals: list[dict]) -> list[dict]:
    trades = []
    idx = df.index
    i_lookup = {ts: i for i, ts in enumerate(idx)}
    last_scan_i = -1
    for sig in signals:
        i = i_lookup.get(sig["ts"])
        if i is None or i <= last_scan_i or i + 1 >= len(idx):
            continue
        entry_i = i + 1  # enter on the NEXT bar's open -- no look-ahead onto the signal bar itself
        if entry_i >= len(idx) or idx[entry_i].date() != idx[i].date():
            continue
        entry = df["open"].iloc[entry_i]
        is_long = sig["direction"] == "LONG"
        stop = entry - STOP_POINTS if is_long else entry + STOP_POINTS
        target = entry + TARGET_POINTS if is_long else entry - TARGET_POINTS
        bars = df.iloc[entry_i:entry_i + HOLD_BARS + 1]
        if bars.empty or bars.index[0].date() != idx[i].date():
            continue
        sim = simulate.simulate_trade(bars, 0, sig["direction"], entry, stop, target,
                                      tag=sig["direction"], max_bars=HOLD_BARS)
        trades.append(sim)
        # simulate_trade always produces a real trade (no reject/skip concept in that
        # function) -- fired=True unconditionally, advance past this trade's own exit bar
        # so the next signal can't open an overlapping position before this one closes.
        last_scan_i = simulate.next_scan_index(i, True, i_lookup.get(sim["exit_ts"]))
    return trades


def main():
    df = load_session_bars()
    cvd = compute_cvd_proxy(df)
    signals = find_cvd_cross_signals(df, cvd)
    trades = simulate_signals(df, signals)
    rep = report.compute_report(trades, pnl_fn=simulate.pnl_points, group_by="tag")

    sections = [
        ("Data", f"NIFTY INDEX 1-min bars, real NSE trading hours only, 2021-09-29 to 2026-09-28 "
                 f"({len(df):,} bars). {len(signals):,} raw CVD-cross signals found, {len(trades):,} "
                 f"actually simulated (after no-look-ahead scan-index dedup)."),
        ("Report (gross points, no costs applied -- see honest-scope note above)", rep),
    ]
    out_path = Path(__file__).parent / "NIFTY_ORDERFLOW_CVD_VERDICT.md"
    report.write_verdict_md(
        out_path,
        title="NIFTY tick-rule CVD proxy -- signal-existence screen",
        rule_description=f"CVD (tick-rule signed volume, session-reset) crosses its own "
                         f"{CVD_SMA_WINDOW}-bar SMA -> enter next-bar-open, {STOP_POINTS}pt stop / "
                         f"{TARGET_POINTS}pt target, {HOLD_BARS}-bar max hold, on the NIFTY index level.",
        data_sources=["market_candles (DHAN_HISTORICAL), NIFTY INDEX, 1m, 2021-09-29..2026-09-28"],
        disclosed_choices=[
            "Tick-rule proxy only -- NOT the live engine's quote-rule-primary classification "
            "(historical OHLCV has no bid/ask depth to run the quote rule at all)",
            "Simulated on the raw INDEX level, no real option/future execution cost applied",
            "CVD resets every session (no overnight carry)",
            f"Fixed {STOP_POINTS}pt/{TARGET_POINTS}pt stop/target, {HOLD_BARS}-min max hold -- not optimized",
        ],
        sections=sections,
        bottom_line=(
            f"n={rep.get('n', len(trades))}, verdict={rep.get('verdict')}. "
            + (f"profit_factor={rep.get('profit_factor')}, win_rate={rep.get('win_rate')}, "
               f"avg_pnl_points={rep.get('avg_pnl')}. " if rep.get('n', 0) > 0 else "")
            + "This is a tick-rule PROXY screen on the raw index level with no execution costs -- "
              "even a positive result here is NOT evidence the live quote-rule signal has edge, and NOT "
              "tradeable as-is without a real-cost, real-instrument re-run."
        ),
    )
    print(f"bars={len(df)} signals={len(signals)} trades={len(trades)}")
    print(rep)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
