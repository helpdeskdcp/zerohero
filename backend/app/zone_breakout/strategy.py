"""Zone Breakout strategy: indicators + the bar-by-bar state machine of the owner's bot, re-implemented as a pure function returning trades.
Semantics are identical to the original script in mode entry="close", intraday=False (parity-tested against the original file); the intraday / next-open variants are the realistic versions."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd


@dataclass
class Config:
    zone_len: int = 20
    atr_len: int = 14
    cons_bars: int = 3
    max_cons_atr: float = 1.5
    max_wait: int = 12
    rr: float = 2.0
    sl_buffer_atr: float = 0.1
    min_body_atr: float = 0.10
    breakout_buffer_atr: float = 0.05
    use_engulfing: bool = True
    use_harami: bool = True
    use_ema_filter: bool = True
    ema_len: int = 50
    use_volume_filter: bool = False
    volume_len: int = 20
    volume_multiplier: float = 1.5

    def as_dict(self) -> dict: return asdict(self)


def rma(x: np.ndarray, length: int) -> np.ndarray:
    """Wilder smoothing seeded with the SMA of the first `length` values (same as the original's rma)."""
    n = len(x); out = np.full(n, np.nan)
    if n < length: return out
    seed = pd.Series(x).rolling(length, min_periods=length).mean().values; first = np.argmax(~np.isnan(seed)) if (~np.isnan(seed)).any() else None
    if first is None: return out
    out[first] = seed[first]
    for i in range(first + 1, n):
        out[i] = (out[i - 1] * (length - 1) + x[i]) / length if not np.isnan(x[i]) else out[i - 1]
    return out


def indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """df: timestamp (tz-aware), open, high, low, close, volume; ascending, unique. Returns the original's feature frame."""
    d = df.reset_index(drop=True).copy(); pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = rma(tr.values.astype(float), cfg.atr_len)
    d["resistance"] = d["high"].shift(1).rolling(cfg.zone_len, min_periods=cfg.zone_len).max()
    d["support"] = d["low"].shift(1).rolling(cfg.zone_len, min_periods=cfg.zone_len).min()
    d["ema"] = d["close"].ewm(span=cfg.ema_len, adjust=False, min_periods=cfg.ema_len).mean()
    d["vol_avg"] = d["volume"].shift(1).rolling(cfg.volume_len, min_periods=cfg.volume_len).mean()
    body_top = d[["open", "close"]].max(axis=1); body_bottom = d[["open", "close"]].min(axis=1); prev_top, prev_bottom = body_top.shift(1), body_bottom.shift(1)
    bull_engulf = (d["close"] > d["open"]) & (d["close"].shift(1) < d["open"].shift(1)) & (d["open"] <= d["close"].shift(1)) & (d["close"] >= d["open"].shift(1))
    bear_engulf = (d["close"] < d["open"]) & (d["close"].shift(1) > d["open"].shift(1)) & (d["open"] >= d["close"].shift(1)) & (d["close"] <= d["open"].shift(1))
    bull_harami = (d["close"] > d["open"]) & (d["close"].shift(1) < d["open"].shift(1)) & (body_top < prev_top) & (body_bottom > prev_bottom)
    bear_harami = (d["close"] < d["open"]) & (d["close"].shift(1) > d["open"].shift(1)) & (body_top < prev_top) & (body_bottom > prev_bottom)
    body_ok = (d["close"] - d["open"]).abs() >= d["atr"] * cfg.min_body_atr
    d["bull_pattern"] = body_ok & ((cfg.use_engulfing & bull_engulf) | (cfg.use_harami & bull_harami)); d["bear_pattern"] = body_ok & ((cfg.use_engulfing & bear_engulf) | (cfg.use_harami & bear_harami))
    d["volume_ok"] = True if not cfg.use_volume_filter else (d["vol_avg"].notna() & (d["vol_avg"] > 0) & (d["volume"] >= d["vol_avg"] * cfg.volume_multiplier))
    d["trend_long_ok"] = True if not cfg.use_ema_filter else d["close"] > d["ema"]; d["trend_short_ok"] = True if not cfg.use_ema_filter else d["close"] < d["ema"]
    d["break_buffer"] = d["atr"].fillna(0) * cfg.breakout_buffer_atr
    d["bull_break"] = d["resistance"].notna() & d["resistance"].shift(1).notna() & (d["close"] > d["resistance"] + d["break_buffer"]) & (d["close"].shift(1) <= d["resistance"].shift(1))
    d["bear_break"] = d["support"].notna() & d["support"].shift(1).notna() & (d["close"] < d["support"] - d["break_buffer"]) & (d["close"].shift(1) >= d["support"].shift(1))
    d["consolidation_range"] = d["high"].shift(1).rolling(cfg.cons_bars, min_periods=cfg.cons_bars).max() - d["low"].shift(1).rolling(cfg.cons_bars, min_periods=cfg.cons_bars).min()
    d["consolidated"] = d["atr"].notna() & (d["atr"] > 0) & (d["consolidation_range"] <= d["atr"] * cfg.max_cons_atr)
    return d


class State:
    """Mutable per-instrument state (also what the live runner persists)."""
    def __init__(self):
        self.pending_dir = 0; self.breakout_bar: int | None = None; self.breakout_level: float | None = None; self.position: dict | None = None; self.queued: dict | None = None


def _ist_minutes(ts) -> int:
    t = pd.Timestamp(ts).tz_convert("Asia/Kolkata"); return t.hour * 60 + t.minute


def step(d: pd.DataFrame, i: int, cfg: Config, st: State, entry: str = "close", intraday: bool = False, interval_min: int = 5, session_dates=None, events: list | None = None) -> list[dict]:
    """Advance the state machine by bar i of the indicator frame `d`; returns the trades CLOSED on this bar. `events` (optional list) collects (kind, ts, price, sl, tp)."""
    closed: list[dict] = []; row = d.iloc[i]; ts = row["timestamp"]
    if intraday and session_dates is not None and i > 0 and session_dates[i] != session_dates[i - 1]:
        st.pending_dir, st.breakout_bar, st.breakout_level = 0, None, None                      # a new session starts clean (intraday variants only)
    if pd.isna(row["atr"]) or pd.isna(row["resistance"]) or pd.isna(row["support"]): return closed
    close, high, low, atr, opn = float(row["close"]), float(row["high"]), float(row["low"]), float(row["atr"]), float(row["open"])
    ev = events.append if events is not None else (lambda *_: None)

    def close_trade(px, why):
        p = st.position; sg = 1.0 if p["side"] == "BUY" else -1.0; risk = p["risk"]
        closed.append({**p, "exit_ts": ts, "exit": px, "reason": why, "R": sg * (px - p["entry"]) / risk, "exit_i": i}); ev(("PAPER_EXIT_" + {"sl": "SL", "tp": "TARGET", "eod": "EOD"}[why], ts, px, p["sl"], p["tp"])); st.position = None

    if st.queued is not None:                                                                   # next-open entry decided on the previous bar
        q, st.queued = st.queued, None
        if not (intraday and session_dates is not None and session_dates[i] != session_dates[i - 1]):
            sg = 1.0 if q["side"] == "BUY" else -1.0; risk = (opn - q["sl"]) * sg
            if risk > 0:
                tp = opn + sg * risk * cfg.rr; st.position = {"side": q["side"], "entry": opn, "sl": q["sl"], "tp": tp, "entry_ts": ts, "entry_i": i, "risk": risk, "signal_i": q["signal_i"]}; ev(("PAPER_ENTRY_" + q["side"], ts, opn, q["sl"], tp))
    if st.position is not None:
        p = st.position
        if p["side"] == "BUY":
            if low <= p["sl"]: close_trade(p["sl"], "sl")
            elif high >= p["tp"]: close_trade(p["tp"], "tp")
        else:
            if high >= p["sl"]: close_trade(p["sl"], "sl")
            elif low <= p["tp"]: close_trade(p["tp"], "tp")
        if st.position is not None:
            if intraday and _ist_minutes(ts) + interval_min >= 15 * 60 + 15: close_trade(close, "eod")             # flat at the 15:15 close
            return closed                                                                        # original: no new setups while a position is open
    bars_since = i - st.breakout_bar if st.breakout_bar is not None else 0
    if st.pending_dir == 1 and (close <= st.breakout_level or bars_since > cfg.max_wait): st.pending_dir, st.breakout_bar, st.breakout_level = 0, None, None
    elif st.pending_dir == -1 and (close >= st.breakout_level or bars_since > cfg.max_wait): st.pending_dir, st.breakout_bar, st.breakout_level = 0, None, None
    if st.pending_dir == 0:
        if bool(row["bull_break"]): st.pending_dir, st.breakout_bar, st.breakout_level = 1, i, float(row["resistance"]); ev(("BREAKOUT_UP", ts, close, None, None))
        elif bool(row["bear_break"]): st.pending_dir, st.breakout_bar, st.breakout_level = -1, i, float(row["support"]); ev(("BREAKOUT_DOWN", ts, close, None, None))
    if st.breakout_bar is None: return closed
    bars_since = i - st.breakout_bar
    if bars_since < cfg.cons_bars + 1 or not bool(row["consolidated"]): return closed
    if not (i - cfg.cons_bars >= st.breakout_bar + 1): return closed
    post = d.iloc[i - cfg.cons_bars:i]
    if len(post) != cfg.cons_bars: return closed
    if st.pending_dir == 1:
        ready = close > st.breakout_level and bool((post["close"] > st.breakout_level).all()) and bool(row["bull_pattern"]) and bool(row["volume_ok"]) and bool(row["trend_long_ok"]); side = "BUY"
    elif st.pending_dir == -1:
        ready = close < st.breakout_level and bool((post["close"] < st.breakout_level).all()) and bool(row["bear_pattern"]) and bool(row["volume_ok"]) and bool(row["trend_short_ok"]); side = "SELL"
    else: return closed
    if not ready: return closed
    sl = low - atr * cfg.sl_buffer_atr if side == "BUY" else high + atr * cfg.sl_buffer_atr
    risk = close - sl if side == "BUY" else sl - close
    if risk <= 0: return closed
    tp = close + risk * cfg.rr if side == "BUY" else close - risk * cfg.rr
    late = intraday and _ist_minutes(ts) >= 15 * 60                                              # no new signal on a bar starting at/after 15:00
    if not late:
        if entry == "close":
            st.position = {"side": side, "entry": close, "sl": sl, "tp": tp, "entry_ts": ts, "entry_i": i, "risk": risk, "signal_i": i}; ev(("PAPER_ENTRY_" + side, ts, close, sl, tp))
        else:
            st.queued = {"side": side, "sl": sl, "signal_i": i}
    st.pending_dir, st.breakout_bar, st.breakout_level = 0, None, None
    return closed


def simulate(df: pd.DataFrame, cfg: Config | None = None, entry: str = "close", intraday: bool = False, interval_min: int = 5, with_events: bool = False):
    """Run the strategy over a whole frame. Returns (trades DataFrame, events list or None). Trade columns: side, entry_ts, entry, sl, tp, exit_ts, exit, reason, R (gross R multiple), risk (price units), risk_bp."""
    cfg = cfg or Config(); d = indicators(df, cfg); st = State(); out: list[dict] = []; events = [] if with_events else None
    sd = pd.DatetimeIndex(d["timestamp"]).tz_convert("Asia/Kolkata").date if intraday else None
    for i in range(len(d)): out += step(d, i, cfg, st, entry, intraday, interval_min, sd, events)
    t = pd.DataFrame(out)
    if len(t): t["risk_bp"] = t["risk"] / t["entry"] * 1e4; t["session"] = pd.DatetimeIndex(t["entry_ts"]).tz_convert("Asia/Kolkata").date
    return t, events
