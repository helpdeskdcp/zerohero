"""
Reversal-at-key-level backtest -- user's own rule, tested on real data.

RULE (as given, Hinglish, restated exactly):
  Mark prior-period high/low on Daily, Weekly, 4H timeframes. On the 15-min
  chart: if price breaks ABOVE a marked high and that breakout candle
  itself closes RED -> potential SHORT reversal. If price breaks BELOW a
  marked low and that candle closes GREEN -> potential LONG reversal.
  The reversal is only valid if a "turnaround" (close back on the correct
  side of the level) happens within 3 15-min candles of the break candle;
  beyond that, the reversal doesn't hold (skip it).

DATA: real NIFTY 1-min spot, 2015-01-01 to 2025-12-24 (Kaggle,
data/historical/kaggle/mavrick136__10-years-of-nifty-50-1-minute-data-20152025).
No synthetic/fabricated bars.

MY DISCLOSED INTERPRETATION CHOICES (the rule didn't fully specify these --
stated explicitly so results aren't silently dependent on an unstated guess):
  - "Turnaround" = the confirmation bar's CLOSE crosses back to the correct
    side of the broken level (not just an intrabar wick touch).
  - Entry = the confirmation bar's close (not the original break candle).
  - Stop = the ORIGINAL break candle's extreme (its high for a SHORT, its
    low for a LONG) -- the natural structural invalidation point.
  - Target = tested at three fixed R-multiples (1.0R, 1.5R, 2.0R) since the
    rule specifies no target; whichever level of the stop/target ladder is
    hit first each trade is taken, with a same-day EOD time-stop as backstop.
  - "Tight zone" and "key psychological" levels are NOT implemented in this
    pass (no unambiguous definition was given) -- this tests the Daily/
    Weekly/4H sweep-and-reject core only. Flagged, not silently dropped.

INDEX-LEVEL ONLY: this measures the pattern's edge on NIFTY spot points.
Whether that edge survives real option premium (theta/spread) is a SEPARATE,
later question -- see this session's own repeated history of index-level
patterns dying on real option P&L (Inside-Bar 2m, SSL Hybrid, etc.).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CSV = ("/root/zerohero/backend/data/historical/kaggle/"
       "mavrick136__10-years-of-nifty-50-1-minute-data-20152025/NIFTY_1MIN_2015-2025.csv")


def load_1m(csv_path: str = CSV) -> pd.DataFrame:
    """Handles both real formats seen in data/historical/kaggle/: a single
    'datetime' column (NIFTY), or separate 'Date'+'Time' columns with
    capitalized OHLC and no volume (BANKNIFTY) -- never invents a volume
    column when the source has none; downstream code doesn't need it."""
    df = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in df.columns}
    if "datetime" in cols:
        df["datetime"] = pd.to_datetime(df[cols["datetime"]])
    elif "date" in cols and "time" in cols:
        df["datetime"] = pd.to_datetime(df[cols["date"]] + " " + df[cols["time"]],
                                        format="%d-%m-%Y %H:%M:%S")
    else:
        raise ValueError(f"{csv_path}: no recognizable datetime column(s)")
    df = df.rename(columns={cols.get("open", "Open"): "open", cols.get("high", "High"): "high",
                            cols.get("low", "Low"): "low", cols.get("close", "Close"): "close"})
    df = df.set_index("datetime").sort_index()
    df = df[~df.index.duplicated(keep="first")]
    return df[["open", "high", "low", "close"]]


def resample_ohlc(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    # label='left'/closed='left' EXPLICITLY for all three periods (D/W/4h) --
    # pandas' own defaults disagree (W defaults to label='right'), which
    # would silently break the uniform shift(1)-for-"prior completed
    # period" logic in build_levels() if left on defaults. With label='left'
    # every bar's index is its own START, consistently, for every rule.
    r = df.resample(rule, label="left", closed="left")
    out = pd.concat([r["open"].first(), r["high"].max(), r["low"].min(), r["close"].last()],
                    axis=1, keys=["open", "high", "low", "close"]).dropna()
    return out


def build_levels(m15: pd.DataFrame, daily: pd.DataFrame, weekly: pd.DataFrame, h4: pd.DataFrame) -> pd.DataFrame:
    """Vectorized prior-COMPLETED-period high/low via merge_asof(direction=
    'backward') against each period series shifted by 1 -- with every
    period series using label='left' (its own start timestamp), an asof-
    backward match on the CURRENT period's own start would otherwise return
    that period's own (still-forming) high/low; shifting by one row first
    makes the match return the PRIOR period's values instead."""
    out = m15.copy()
    for name, pdf in (("d", daily), ("w", weekly), ("h4", h4)):
        shifted = pdf.shift(1)  # each row now holds the PRIOR period's own values
        for col in ("high", "low"):
            key = f"{name}_{col}"
            s = shifted[[col]].reset_index().rename(columns={pdf.index.name or "index": "datetime"})
            merged = pd.merge_asof(out[[]].reset_index(), s, on="datetime", direction="backward")
            out[key] = merged[col].values
    return out


def get_trades(csv_path: str = CSV, confirm_bars: int = 3, target_r: float = 1.5) -> list[dict]:
    m1 = load_1m(csv_path)
    m15 = resample_ohlc(m1, "15min")
    daily = resample_ohlc(m1, "1D")
    weekly = resample_ohlc(m1, "1W")
    h4 = resample_ohlc(m1, "4h")

    lv = build_levels(m15, daily, weekly, h4)
    lv["date"] = lv.index.date

    trades = []
    n = len(lv)
    highs, lows, opens, closes = lv["high"].values, lv["low"].values, lv["open"].values, lv["close"].values
    dates = lv["date"].values

    level_cols = {"d_high": "DAILY", "w_high": "WEEKLY", "h4_high": "4H"}
    level_cols_low = {"d_low": "DAILY", "w_low": "WEEKLY", "h4_low": "4H"}

    i = 0
    while i < n - confirm_bars - 1:
        red = closes[i] < opens[i]
        green = closes[i] > opens[i]

        fired = False
        fired_at = None
        # SHORT: broke above a level, break candle is RED
        if red:
            for col, tag in level_cols.items():
                lvl = lv[col].values[i]
                if lvl is not None and not np.isnan(lvl) and highs[i] > lvl:
                    for k in range(1, confirm_bars + 1):
                        j = i + k
                        if j >= n or dates[j] != dates[i]:
                            break  # confirmation must be same session
                        if closes[j] < lvl:
                            entry = closes[j]
                            stop = highs[i]
                            risk = stop - entry
                            if risk <= 0:
                                break
                            target = entry - target_r * risk
                            trades.append(_simulate(lv, j, "SHORT", entry, stop, target, tag, lv.index[j]))
                            fired = True
                            fired_at = j
                            break
                    if fired:
                        break
        # LONG: broke below a level, break candle is GREEN
        if not fired and green:
            for col, tag in level_cols_low.items():
                lvl = lv[col].values[i]
                if lvl is not None and not np.isnan(lvl) and lows[i] < lvl:
                    for k in range(1, confirm_bars + 1):
                        j = i + k
                        if j >= n or dates[j] != dates[i]:
                            break
                        if closes[j] > lvl:
                            entry = closes[j]
                            stop = lows[i]
                            risk = entry - stop
                            if risk <= 0:
                                break
                            target = entry + target_r * risk
                            trades.append(_simulate(lv, j, "LONG", entry, stop, target, tag, lv.index[j]))
                            fired = True
                            fired_at = j
                            break
                    if fired:
                        break
        # Advance PAST the confirmation bar on a fire -- otherwise the very
        # next bar(s) of the same continuing move re-trigger as "new"
        # breakouts that all resolve to the same real confirmation event
        # (confirmed live: 3 overlapping signals from 09:00/09:15/09:30
        # break-bars all confirming at the same 09:45 bar, 2026-09-15).
        i = fired_at + 1 if fired else i + 1

    return trades


def run(target_r: float, confirm_bars: int = 3, csv_path: str = CSV) -> dict:
    trades = get_trades(csv_path, confirm_bars, target_r)
    return _report(trades, target_r)


def _simulate(lv, entry_idx, direction, entry, stop, target, level_tag, entry_ts, max_bars=26):
    """Walk forward bar-by-bar from entry_idx+1 until stop/target/EOD."""
    highs, lows, closes = lv["high"].values, lv["low"].values, lv["close"].values
    dates = lv["date"].values
    entry_date = dates[entry_idx]
    n = len(lv)
    base = {"direction": direction, "entry": entry, "stop": stop, "target": target,
            "level": level_tag, "entry_ts": entry_ts}
    for k in range(1, max_bars + 1):
        j = entry_idx + k
        if j >= n or dates[j] != entry_date:
            exit_price = closes[min(j - 1, n - 1)]
            return {**base, "exit": exit_price, "reason": "EOD"}
        if direction == "SHORT":
            if highs[j] >= stop:
                return {**base, "exit": stop, "reason": "STOP"}
            if lows[j] <= target:
                return {**base, "exit": target, "reason": "TARGET"}
        else:
            if lows[j] <= stop:
                return {**base, "exit": stop, "reason": "STOP"}
            if highs[j] >= target:
                return {**base, "exit": target, "reason": "TARGET"}
    exit_price = closes[min(entry_idx + max_bars, n - 1)]
    return {**base, "exit": exit_price, "reason": "MAXBARS"}


def _report(trades, target_r) -> dict:
    if not trades:
        return {"target_r": target_r, "n": 0, "verdict": "INSUFFICIENT_SAMPLE"}
    pnl = []
    for t in trades:
        sign = 1 if t["direction"] == "SHORT" else -1
        # SHORT profits when price falls (entry - exit); LONG profits when price rises
        p = (t["entry"] - t["exit"]) if t["direction"] == "SHORT" else (t["exit"] - t["entry"])
        pnl.append(p)
    pnl = np.array(pnl)
    wins = pnl[pnl > 0]
    losses = pnl[pnl <= 0]
    win_rate = len(wins) / len(pnl)
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else float("inf")
    by_level = {}
    for t, p in zip(trades, pnl):
        by_level.setdefault(t["level"], []).append(p)
    by_level_stats = {k: {"n": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
                          "avg_pnl": round(float(np.mean(v)), 2)}
                      for k, v in by_level.items()}
    by_reason = {}
    for t in trades:
        by_reason[t["reason"]] = by_reason.get(t["reason"], 0) + 1
    return {
        "target_r": target_r, "n": len(trades),
        "win_rate": round(win_rate, 4), "profit_factor": round(pf, 3) if pf != float("inf") else None,
        "avg_pnl_pts": round(float(pnl.mean()), 3), "total_pnl_pts": round(float(pnl.sum()), 1),
        "expectancy_r": round(float(pnl.mean()), 3),
        "by_level": by_level_stats, "by_exit_reason": by_reason,
        "verdict": "OK" if len(trades) >= 30 else "INSUFFICIENT_SAMPLE",
    }


if __name__ == "__main__":
    import json
    for tr in (1.0, 1.5, 2.0):
        print(f"=== target {tr}R ===")
        print(json.dumps(run(tr), indent=2))
