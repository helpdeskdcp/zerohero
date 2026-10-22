"""
CPR (Central Pivot Range) + Opening Range Breakout -- backtest before any
production code, same standing discipline as the other strategies tested
this session.

RULE (as given, restated):
  Daily CPR from PRIOR day's H/L/C: Pivot=(H+L+C)/3, BC=(H+L)/2, TC=2*Pivot-BC.
  Opening Range: 09:15-09:30 high/low.
  Entry: ORH/ORL breakout ALIGNED with CPR direction.

MY DISCLOSED INTERPRETATION CHOICES:
  - "Aligned with CPR direction" = price trading ABOVE TC (bullish bias) only
    takes CE on an ORH breakout; price trading BELOW BC (bearish bias) only
    takes PE on an ORL breakout. Price INSIDE the CPR band (BC<=price<=TC)
    = no clear bias, no trade -- standard real CPR-trading interpretation
    (narrow/outside CPR = trending bias day, inside = no edge).
  - No fixed SL/TP was given for this specific sub-strategy in the spec (the
    index-wise SL/TP table applies to the portfolio generally) -- reused
    6pt/12pt premium (SL/TP, 1:2 R:R), same as the other two rules, for
    apples-to-apples comparison.
  - Entries any time from 09:30 (after the opening range closes) to 15:00.
  - Simple breakout entry (no failure/reversal wait) -- this IS a
    continuation strategy, unlike the other two (reversal) rules tested.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

NIFTY_CSV = ("/root/zerohero/backend/data/historical/kaggle/"
             "mavrick136__10-years-of-nifty-50-1-minute-data-20152025/NIFTY_1MIN_2015-2025.csv")

MAX_TRADES_PER_DAY = 3
MAX_CONSEC_SL = 2


def load_1m(csv_path: str = NIFTY_CSV) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    cols = {c.lower(): c for c in df.columns}
    if "datetime" in cols:
        df["datetime"] = pd.to_datetime(df[cols["datetime"]])
    elif "date" in cols and "time" in cols:
        df["datetime"] = pd.to_datetime(df[cols["date"]] + " " + df[cols["time"]], format="%d-%m-%Y %H:%M:%S")
    else:
        raise ValueError(f"{csv_path}: no recognizable datetime column(s)")
    df = df.rename(columns={cols.get("open", "Open"): "open", cols.get("high", "High"): "high",
                            cols.get("low", "Low"): "low", cols.get("close", "Close"): "close"})
    df = df.set_index("datetime").sort_index()
    return df[~df.index.duplicated(keep="first")][["open", "high", "low", "close"]]


def build_levels(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    daily = df.resample("1D", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    daily["pivot"] = (daily["high"] + daily["low"] + daily["close"]) / 3.0
    daily["bc"] = (daily["high"] + daily["low"]) / 2.0
    daily["tc"] = 2 * daily["pivot"] - daily["bc"]
    # normalize so bc <= tc always (CPR can invert when pivot < bc)
    lo = daily[["bc", "tc"]].min(axis=1)
    hi = daily[["bc", "tc"]].max(axis=1)
    daily["bc"], daily["tc"] = lo, hi
    shifted = daily.shift(1)  # PRIOR day's CPR, no look-ahead
    for col in ("bc", "tc"):
        s = shifted[[col]].reset_index()
        merged = pd.merge_asof(out.reset_index()[["datetime"]], s, on="datetime", direction="backward")
        out[col] = merged[col].values

    out["date"] = out.index.date
    or_high, or_low = {}, {}
    for date, day_df in out.groupby("date"):
        t = day_df.index.time
        rng = day_df[(t >= pd.Timestamp("09:15").time()) & (t < pd.Timestamp("09:30").time())]
        if not rng.empty:
            or_high[date] = rng["high"].max()
            or_low[date] = rng["low"].min()
    out["or_high"] = out["date"].map(or_high)
    out["or_low"] = out["date"].map(or_low)
    before_930 = pd.Series([t < pd.Timestamp("09:30").time() for t in out.index.time], index=out.index)
    out.loc[before_930, ["or_high", "or_low"]] = np.nan
    return out


def in_window(ts: pd.Timestamp) -> bool:
    t = (ts.hour, ts.minute)
    return (9, 30) <= t <= (15, 0)


def get_trades(csv_path: str = NIFTY_CSV, sl_pts: float = 6.0, tp_pts: float = 12.0,
               index_delta: float = 0.5) -> list[dict]:
    df = load_1m(csv_path)
    lv = build_levels(df)
    sl_idx, tp_idx = sl_pts / index_delta, tp_pts / index_delta

    highs, lows, closes = lv["high"].values, lv["low"].values, lv["close"].values
    orh, orl = lv["or_high"].values, lv["or_low"].values
    tc, bc = lv["tc"].values, lv["bc"].values
    dates = lv["date"].values
    idx = lv.index
    n = len(lv)

    trades = []
    already_traded_today: set = set()
    for i in range(n):
        if not in_window(idx[i]):
            continue
        date = dates[i]
        if not np.isnan(orh[i]) and not np.isnan(tc[i]) and closes[i] > tc[i] and highs[i] > orh[i] \
                and (date, "CE") not in already_traded_today:
            entry = closes[i]
            stop, target = entry - sl_idx, entry + tp_idx
            trades.append(_simulate(lv, i, "CE", entry, stop, target, "ORH_CPR_BULL", idx[i]))
            already_traded_today.add((date, "CE"))
        elif not np.isnan(orl[i]) and not np.isnan(bc[i]) and closes[i] < bc[i] and lows[i] < orl[i] \
                and (date, "PE") not in already_traded_today:
            entry = closes[i]
            stop, target = entry + sl_idx, entry - tp_idx
            trades.append(_simulate(lv, i, "PE", entry, stop, target, "ORL_CPR_BEAR", idx[i]))
            already_traded_today.add((date, "PE"))
    return trades


def _simulate(lv, entry_idx, direction, entry, stop, target, tag, entry_ts, max_bars=60):
    highs, lows, closes = lv["high"].values, lv["low"].values, lv["close"].values
    dates = lv["date"].values
    entry_date = dates[entry_idx]
    n = len(lv)
    base = {"direction": direction, "entry": entry, "stop": stop, "target": target,
            "level": tag, "entry_ts": entry_ts}
    for k in range(1, max_bars + 1):
        j = entry_idx + k
        if j >= n or dates[j] != entry_date:
            return {**base, "exit": closes[min(j - 1, n - 1)], "reason": "EOD"}
        if direction == "CE":
            if lows[j] <= stop:
                return {**base, "exit": stop, "reason": "SL"}
            if highs[j] >= target:
                return {**base, "exit": target, "reason": "TP"}
        else:
            if highs[j] >= stop:
                return {**base, "exit": stop, "reason": "SL"}
            if lows[j] <= target:
                return {**base, "exit": target, "reason": "TP"}
    return {**base, "exit": closes[min(entry_idx + max_bars, n - 1)], "reason": "MAXBARS"}


def apply_daily_limits(trades: list[dict]) -> list[dict]:
    by_date: dict = {}
    for t in sorted(trades, key=lambda x: x["entry_ts"]):
        by_date.setdefault(t["entry_ts"].date(), []).append(t)
    kept = []
    for date, day_trades in by_date.items():
        count, consec_sl = 0, 0
        for t in day_trades:
            if count >= MAX_TRADES_PER_DAY or consec_sl >= MAX_CONSEC_SL:
                break
            kept.append(t)
            count += 1
            consec_sl = consec_sl + 1 if t["reason"] == "SL" else 0
    return kept


def pnl_of(t):
    return (t["exit"] - t["entry"]) if t["direction"] == "CE" else (t["entry"] - t["exit"])


def report(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "verdict": "INSUFFICIENT_SAMPLE"}
    pnl = np.array([pnl_of(t) for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
    by_dir = {}
    for t, p in zip(trades, pnl):
        by_dir.setdefault(t["direction"], []).append(p)
    by_dir_stats = {k: {"n": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
                        "avg_pnl": round(float(np.mean(v)), 2)} for k, v in by_dir.items()}
    return {"n": len(pnl), "win_rate": round(len(wins) / len(pnl), 4),
            "profit_factor": round(pf, 3) if pf else None,
            "avg_pnl": round(float(pnl.mean()), 3), "total_pnl": round(float(pnl.sum()), 1),
            "by_direction": by_dir_stats, "verdict": "OK" if len(pnl) >= 30 else "INSUFFICIENT_SAMPLE"}


if __name__ == "__main__":
    import json
    raw = get_trades()
    print(f"raw signals (before daily limits): {len(raw)}")
    limited = apply_daily_limits(raw)
    print(json.dumps(report(limited), indent=2))
