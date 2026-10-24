#!/usr/bin/env python3
"""
Zone Breakout Strict Consolidation — Angel One SmartAPI Paper Bot
Single-file Python implementation based on the user's Pine Script v1.3 logic.

SAFETY:
- PAPER TRADING ONLY. This file does not call placeOrder / modifyOrder / cancelOrder.
- Live ticks are read from Angel One SmartAPI WebSocket V2 and aggregated to candles.
- Optional historical OHLCV CSV seeds the indicator warm-up.
- A paper position is simulated locally; it is not a real broker position.
- Verify SmartAPI instrument token, exchange type, market session and signals before use.

Required CSV columns: timestamp (or date/time), open, high, low, close, volume.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
BOT_NAME = "Zone Breakout AngelOne Paper Bot v1.0"
STOP_EVENT = threading.Event()


def setup_logging(log_file: str) -> None:
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(log_file, encoding="utf-8")],
    )


def env_required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder smoothing approximating Pine ta.rma / ta.atr."""
    x = pd.Series(series, dtype=float)
    out = pd.Series(np.nan, index=x.index, dtype=float)
    seed = x.rolling(length, min_periods=length).mean()
    first = seed.first_valid_index()
    if first is None:
        return out
    start = int(first)
    out.iloc[start] = seed.iloc[start]
    for i in range(start + 1, len(x)):
        prev = out.iloc[i - 1]
        out.iloc[i] = (prev * (length - 1) + x.iloc[i]) / length if pd.notna(x.iloc[i]) else prev
    return out


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def normalize_candles(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d.columns = [str(c).strip().lower() for c in d.columns]
    aliases = {"datetime": "timestamp", "date": "timestamp", "time": "timestamp"}
    for old, new in aliases.items():
        if old in d.columns and new not in d.columns:
            d = d.rename(columns={old: new})
            break
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(d.columns)
    if missing:
        raise ValueError("Missing OHLCV columns: " + ", ".join(sorted(missing)))
    if "timestamp" not in d.columns:
        d["timestamp"] = pd.date_range(end=datetime.now(IST), periods=len(d), freq="min")
    d["timestamp"] = pd.to_datetime(d["timestamp"], errors="coerce")
    if d["timestamp"].dt.tz is None:
        d["timestamp"] = d["timestamp"].dt.tz_localize(IST, ambiguous="NaT", nonexistent="shift_forward")
    else:
        d["timestamp"] = d["timestamp"].dt.tz_convert(IST)
    for c in required:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d.dropna(subset=["timestamp", *required])
    d = d.sort_values("timestamp").drop_duplicates("timestamp", keep="last").reset_index(drop=True)
    return d[["timestamp", "open", "high", "low", "close", "volume"]]


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
    max_history: int = 2000


def add_indicators(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    d = normalize_candles(df)
    if d.empty:
        return d
    d["atr"] = rma(true_range(d), cfg.atr_len)
    d["resistance"] = d["high"].shift(1).rolling(cfg.zone_len, min_periods=cfg.zone_len).max()
    d["support"] = d["low"].shift(1).rolling(cfg.zone_len, min_periods=cfg.zone_len).min()
    d["ema"] = d["close"].ewm(span=cfg.ema_len, adjust=False, min_periods=cfg.ema_len).mean()
    d["vol_avg"] = d["volume"].shift(1).rolling(cfg.volume_len, min_periods=cfg.volume_len).mean()

    body_top = d[["open", "close"]].max(axis=1)
    body_bottom = d[["open", "close"]].min(axis=1)
    prev_top = body_top.shift(1)
    prev_bottom = body_bottom.shift(1)
    bull_engulf = (d["close"] > d["open"]) & (d["close"].shift(1) < d["open"].shift(1)) & \
                  (d["open"] <= d["close"].shift(1)) & (d["close"] >= d["open"].shift(1))
    bear_engulf = (d["close"] < d["open"]) & (d["close"].shift(1) > d["open"].shift(1)) & \
                  (d["open"] >= d["close"].shift(1)) & (d["close"] <= d["open"].shift(1))
    bull_harami = (d["close"] > d["open"]) & (d["close"].shift(1) < d["open"].shift(1)) & \
                  (body_top < prev_top) & (body_bottom > prev_bottom)
    bear_harami = (d["close"] < d["open"]) & (d["close"].shift(1) > d["open"].shift(1)) & \
                  (body_top < prev_top) & (body_bottom > prev_bottom)
    body_size = (d["close"] - d["open"]).abs()
    body_ok = body_size >= d["atr"] * cfg.min_body_atr
    d["bull_pattern"] = body_ok & ((cfg.use_engulfing & bull_engulf) | (cfg.use_harami & bull_harami))
    d["bear_pattern"] = body_ok & ((cfg.use_engulfing & bear_engulf) | (cfg.use_harami & bear_harami))
    d["volume_ok"] = True if not cfg.use_volume_filter else (
        d["vol_avg"].notna() & (d["vol_avg"] > 0) & (d["volume"] >= d["vol_avg"] * cfg.volume_multiplier)
    )
    d["trend_long_ok"] = True if not cfg.use_ema_filter else d["close"] > d["ema"]
    d["trend_short_ok"] = True if not cfg.use_ema_filter else d["close"] < d["ema"]
    d["break_buffer"] = d["atr"].fillna(0) * cfg.breakout_buffer_atr
    # Use prior close against the prior bar's zone to avoid rolling-level cross errors.
    d["bull_break"] = (
        d["resistance"].notna() & d["resistance"].shift(1).notna() &
        (d["close"] > d["resistance"] + d["break_buffer"]) &
        (d["close"].shift(1) <= d["resistance"].shift(1))
    )
    d["bear_break"] = (
        d["support"].notna() & d["support"].shift(1).notna() &
        (d["close"] < d["support"] - d["break_buffer"]) &
        (d["close"].shift(1) >= d["support"].shift(1))
    )
    d["consolidation_range"] = d["high"].shift(1).rolling(cfg.cons_bars, min_periods=cfg.cons_bars).max() - \
                               d["low"].shift(1).rolling(cfg.cons_bars, min_periods=cfg.cons_bars).min()
    d["consolidated"] = d["atr"].notna() & (d["atr"] > 0) & \
                        (d["consolidation_range"] <= d["atr"] * cfg.max_cons_atr)
    return d


class PaperEngine:
    def __init__(self, cfg: Config, event_log: str):
        self.cfg = cfg
        self.pending_dir = 0
        self.breakout_bar: Optional[int] = None
        self.breakout_level: Optional[float] = None
        self.position: Optional[dict] = None
        self.events_path = Path(event_log)
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.events_path.exists():
            with self.events_path.open("w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(["timestamp", "event", "price", "stop_loss", "target", "reason", "mode"])

    def event(self, ts, name, price, sl="", target="", reason=""):
        logging.info("%s | price=%s | SL=%s | target=%s | %s", name, price, sl, target, reason)
        with self.events_path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow([ts, name, price, sl, target, reason, "PAPER"])

    def process_frame(self, candles: pd.DataFrame, historical_replay: bool = False):
        d = add_indicators(candles, self.cfg)
        if len(d) < max(self.cfg.zone_len + self.cfg.cons_bars + 3, self.cfg.ema_len + 2):
            logging.info("Waiting for more candles: %d loaded.", len(d))
            return
        # Rebuild pending breakout state by replaying all completed bars on initial history.
        # For live updates, process only the newest completed bar after the seed replay.
        if historical_replay:
            self.pending_dir, self.breakout_bar, self.breakout_level, self.position = 0, None, None, None
            start = 0
            for i in range(len(d)):
                self._process_bar(d, i, allow_entry=False, replay=True)
            self.pending_dir, self.breakout_bar, self.breakout_level, self.position = 0, None, None, None
            logging.info("Historical warm-up loaded (%d candles); no historical paper positions retained.", len(d))
            return
        self._process_bar(d, len(d) - 1, allow_entry=True, replay=False)

    def _process_bar(self, d: pd.DataFrame, i: int, allow_entry: bool, replay: bool):
        row = d.iloc[i]
        if pd.isna(row["atr"]) or pd.isna(row["resistance"]) or pd.isna(row["support"]):
            return
        ts = row["timestamp"]
        close, high, low, atr = float(row["close"]), float(row["high"]), float(row["low"]), float(row["atr"])

        # Manage paper position first; conservative rule if SL and target both touched: SL wins.
        if self.position is not None:
            pos = self.position
            if pos["side"] == "BUY":
                if low <= pos["sl"]:
                    self.event(ts, "PAPER_EXIT_SL", pos["sl"], pos["sl"], pos["tp"], "stop loss hit")
                    self.position = None
                elif high >= pos["tp"]:
                    self.event(ts, "PAPER_EXIT_TARGET", pos["tp"], pos["sl"], pos["tp"], "target hit")
                    self.position = None
            else:
                if high >= pos["sl"]:
                    self.event(ts, "PAPER_EXIT_SL", pos["sl"], pos["sl"], pos["tp"], "stop loss hit")
                    self.position = None
                elif low <= pos["tp"]:
                    self.event(ts, "PAPER_EXIT_TARGET", pos["tp"], pos["sl"], pos["tp"], "target hit")
                    self.position = None
            if self.position is not None:
                return

        # State timeout/failure checks before accepting a new setup.
        bars_since = i - self.breakout_bar if self.breakout_bar is not None else 0
        if self.pending_dir == 1 and (close <= self.breakout_level or bars_since > self.cfg.max_wait):
            self.pending_dir, self.breakout_bar, self.breakout_level = 0, None, None
        elif self.pending_dir == -1 and (close >= self.breakout_level or bars_since > self.cfg.max_wait):
            self.pending_dir, self.breakout_bar, self.breakout_level = 0, None, None

        # Detect new breakout only if no setup pending.
        if self.pending_dir == 0:
            if bool(row["bull_break"]):
                self.pending_dir, self.breakout_bar, self.breakout_level = 1, i, float(row["resistance"])
                if not replay:
                    self.event(ts, "BREAKOUT_UP", close, reason=f"level={self.breakout_level:.2f}")
            elif bool(row["bear_break"]):
                self.pending_dir, self.breakout_bar, self.breakout_level = -1, i, float(row["support"])
                if not replay:
                    self.event(ts, "BREAKOUT_DOWN", close, reason=f"level={self.breakout_level:.2f}")

        if self.breakout_bar is None:
            return
        bars_since = i - self.breakout_bar
        if bars_since < self.cfg.cons_bars + 1 or not bool(row["consolidated"]):
            return
        if not (i - self.cfg.cons_bars >= self.breakout_bar + 1):
            return

        # All the N completed candles after the breakout must hold the frozen level.
        post = d.iloc[i-self.cfg.cons_bars:i]
        if len(post) != self.cfg.cons_bars:
            return
        if self.pending_dir == 1:
            holds = bool((post["close"] > self.breakout_level).all())
            ready = close > self.breakout_level and holds and bool(row["bull_pattern"]) and \
                    bool(row["volume_ok"]) and bool(row["trend_long_ok"])
            side = "BUY"
        elif self.pending_dir == -1:
            holds = bool((post["close"] < self.breakout_level).all())
            ready = close < self.breakout_level and holds and bool(row["bear_pattern"]) and \
                    bool(row["volume_ok"]) and bool(row["trend_short_ok"])
            side = "SELL"
        else:
            return
        if not ready:
            return

        sl = low - atr * self.cfg.sl_buffer_atr if side == "BUY" else high + atr * self.cfg.sl_buffer_atr
        risk = close - sl if side == "BUY" else sl - close
        if risk <= 0:
            return
        tp = close + risk * self.cfg.rr if side == "BUY" else close - risk * self.cfg.rr
        if allow_entry and not replay:
            self.position = {"side": side, "entry": close, "sl": sl, "tp": tp, "entry_ts": str(ts)}
            self.event(ts, "PAPER_ENTRY_" + side, close, sl, tp,
                       f"confirmed breakout+consolidation+candle; RR=1:{self.cfg.rr:g}")
        self.pending_dir, self.breakout_bar, self.breakout_level = 0, None, None


class LiveCandleBuilder:
    def __init__(self, interval_minutes: int, on_candle, seed: Optional[pd.DataFrame] = None):
        self.interval = interval_minutes
        self.on_candle = on_candle
        self.current = None
        self.lock = threading.Lock()
        self.seed = seed

    def on_tick(self, tick: dict):
        try:
            price_raw = tick.get("last_traded_price")
            if price_raw is None:
                return
            price = float(price_raw) / 100.0  # SmartAPI WebSocket prices are in paise.
            raw_ts = tick.get("exchange_timestamp")
            ts = datetime.fromtimestamp(float(raw_ts) / 1000, tz=IST) if raw_ts else datetime.now(IST)
            epoch_min = int(ts.timestamp() // (self.interval * 60)) * self.interval
            bucket = datetime.fromtimestamp(epoch_min * 60, tz=IST)
            with self.lock:
                if self.current is None:
                    self.current = {"timestamp": bucket, "open": price, "high": price, "low": price, "close": price, "volume": 0.0}
                    return
                if bucket < self.current["timestamp"]:
                    return  # ignore late/out-of-order tick
                if bucket > self.current["timestamp"]:
                    completed = self.current.copy()
                    self.current = {"timestamp": bucket, "open": price, "high": price, "low": price, "close": price, "volume": 0.0}
                    self.on_candle(completed)
                else:
                    self.current["high"] = max(self.current["high"], price)
                    self.current["low"] = min(self.current["low"], price)
                    self.current["close"] = price
                    # LTP subscription doesn't provide per-tick volume; keep zero rather than invent it.
        except Exception:
            logging.exception("Could not process live tick")


def interval_name(minutes: int) -> str:
    mapping = {1: "ONE_MINUTE", 3: "THREE_MINUTE", 5: "FIVE_MINUTE",
               10: "TEN_MINUTE", 15: "FIFTEEN_MINUTE", 30: "THIRTY_MINUTE",
               60: "ONE_HOUR"}
    if minutes not in mapping:
        raise ValueError(f"Unsupported SmartAPI historical interval: {minutes} minutes")
    return mapping[minutes]


def smartapi_login():
    try:
        import pyotp
        from SmartApi import SmartConnect
    except ImportError as e:
        raise RuntimeError("Install SmartAPI dependencies first: pip install smartapi-python pyotp logzero websocket-client") from e

    api_key = env_required("ANGEL_API_KEY")
    client_code = env_required("ANGEL_CLIENT_CODE")
    pin = env_required("ANGEL_PIN")
    totp_secret = env_required("ANGEL_TOTP_SECRET")
    api = SmartConnect(api_key)
    totp = pyotp.TOTP(totp_secret).now()
    response = api.generateSession(client_code, pin, totp)
    if not isinstance(response, dict) or not response.get("status"):
        raise RuntimeError(f"Angel One login failed: {response}")
    data = response.get("data") or {}
    auth_token = data.get("jwtToken")
    feed_token = api.getfeedToken()
    if not auth_token or not feed_token:
        raise RuntimeError("Login response did not contain JWT/feed token.")
    logging.info("Angel One SmartAPI login successful.")
    return api, auth_token, feed_token, client_code, api_key


def fetch_historical(api, exchange: str, token: str, minutes: int, days: int) -> pd.DataFrame:
    end = datetime.now(IST)
    start = end - timedelta(days=days)
    params = {
        "exchange": exchange,
        "symboltoken": token,
        "interval": interval_name(minutes),
        "fromdate": start.strftime("%Y-%m-%d %H:%M"),
        "todate": end.strftime("%Y-%m-%d %H:%M"),
    }
    response = api.getCandleData(params)
    if not isinstance(response, dict) or not response.get("status"):
        raise RuntimeError(f"Historical candle API failed: {response}")
    rows = (response.get("data") or [])
    if not rows:
        raise RuntimeError("SmartAPI returned no historical candles.")
    return normalize_candles(pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]))


def append_candle_csv(path: str, candle: dict):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    row = {k: candle[k] for k in ("timestamp", "open", "high", "low", "close", "volume")}
    exists = p.exists()
    pd.DataFrame([row]).to_csv(p, mode="a", header=not exists, index=False)


def run_live_paper(args):
    api, auth_token, feed_token, client_code, api_key = smartapi_login()
    exchange = os.getenv("ANGEL_EXCHANGE", "NSE").upper()
    symbol_token = env_required("ANGEL_SYMBOL_TOKEN")
    exchange_type = int(os.getenv("ANGEL_EXCHANGE_TYPE", "1"))  # 1=NSE cash, 2=NSE F&O, 5=MCX
    symbol_name = os.getenv("ANGEL_SYMBOL_NAME", symbol_token)
    logging.info("Starting PAPER mode: %s token=%s exchange=%s type=%s interval=%sm",
                 symbol_name, symbol_token, exchange, exchange_type, args.interval)

    seed_frames = []
    csv_path = os.getenv("HISTORICAL_CSV", "").strip()
    if csv_path and Path(csv_path).exists():
        try:
            csv_df = normalize_candles(pd.read_csv(csv_path))
            seed_frames.append(csv_df)
            logging.info("Loaded %d historical CSV candles from %s", len(csv_df), csv_path)
        except Exception:
            logging.exception("Could not load historical CSV; continuing to API history if available.")
    try:
        api_hist = fetch_historical(api, exchange, symbol_token, args.interval, args.history_days)
        seed_frames.append(api_hist)
        logging.info("Loaded %d historical API candles.", len(api_hist))
    except Exception:
        logging.exception("Historical API fetch failed; using CSV seed if available.")
    if not seed_frames:
        raise RuntimeError("No warm-up candles. Provide HISTORICAL_CSV or fix SmartAPI historical access.")
    history = normalize_candles(pd.concat(seed_frames, ignore_index=True))
    history = history.tail(args.max_history).reset_index(drop=True)
    cfg = Config(
        zone_len=args.zone_len, atr_len=args.atr_len, cons_bars=args.cons_bars,
        max_cons_atr=args.max_cons_atr, max_wait=args.max_wait, rr=args.rr,
        sl_buffer_atr=args.sl_buffer_atr, min_body_atr=args.min_body_atr,
        breakout_buffer_atr=args.breakout_buffer_atr, use_ema_filter=not args.no_ema_filter,
        ema_len=args.ema_len, use_volume_filter=args.use_volume_filter,
        volume_len=args.volume_len, volume_multiplier=args.volume_multiplier,
        max_history=args.max_history,
    )
    engine = PaperEngine(cfg, args.events)
    engine.process_frame(history, historical_replay=True)
    live_rows = history.copy()
    last_processed = history["timestamp"].max()

    def on_completed_candle(candle):
        nonlocal live_rows, last_processed
        try:
            # WebSocket LTP mode has no per-tick traded volume, so volume=0 is explicit.
            append_candle_csv(args.live_csv, candle)
            new = normalize_candles(pd.DataFrame([candle]))
            if new["timestamp"].iloc[0] <= last_processed:
                logging.info("Ignoring candle not newer than history: %s", candle["timestamp"])
                return
            live_rows = normalize_candles(pd.concat([live_rows, new], ignore_index=True)).tail(cfg.max_history).reset_index(drop=True)
            last_processed = new["timestamp"].iloc[0]
            engine.process_frame(live_rows, historical_replay=False)
        except Exception:
            logging.exception("Error processing completed live candle; bot remains in paper mode.")

    try:
        from SmartApi.smartWebSocketV2 import SmartWebSocketV2
    except ImportError as e:
        raise RuntimeError("SmartWebSocketV2 unavailable. Install/upgrade smartapi-python.") from e

    def websocket_loop():
        retry_delay = 3
        while not STOP_EVENT.is_set():
            ws = None
            try:
                ws = SmartWebSocketV2(
                    auth_token, api_key, client_code, feed_token,
                    max_retry_attempt=0, retry_strategy=0, retry_delay=retry_delay,
                )
                builder = LiveCandleBuilder(args.interval, on_completed_candle)

                def on_open(wsapp):
                    logging.info("WebSocket connected; subscribing to live ticks.")
                    ws.subscribe("ZBRK000001", 1, [{"exchangeType": exchange_type, "tokens": [symbol_token]}])

                def on_data(wsapp, message):
                    builder.on_tick(message)

                def on_error(wsapp, error):
                    logging.error("WebSocket error: %s", error)

                def on_close(wsapp):
                    logging.warning("WebSocket closed.")

                ws.on_open = on_open
                ws.on_data = on_data
                ws.on_error = on_error
                ws.on_close = on_close
                ws.connect()  # blocking; returns on disconnect
            except Exception:
                logging.exception("WebSocket connection failed.")
            if not STOP_EVENT.is_set():
                logging.warning("Reconnecting in %s seconds...", retry_delay)
                STOP_EVENT.wait(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    thread = threading.Thread(target=websocket_loop, name="smartapi-ws", daemon=True)
    thread.start()
    logging.info("Paper bot running. No live orders can be placed by this program. Ctrl+C to stop.")
    try:
        while not STOP_EVENT.wait(1):
            pass
    except KeyboardInterrupt:
        STOP_EVENT.set()
    finally:
        STOP_EVENT.set()
        try:
            api.terminateSession(client_code)
        except Exception:
            logging.exception("Session termination failed.")
        logging.info("Stopped. Paper event log: %s", args.events)


def run_csv_backtest(args):
    if not args.csv:
        raise ValueError("--csv is required for --mode csv-backtest")
    df = normalize_candles(pd.read_csv(args.csv))
    cfg = Config(zone_len=args.zone_len, atr_len=args.atr_len, cons_bars=args.cons_bars,
                 max_cons_atr=args.max_cons_atr, max_wait=args.max_wait, rr=args.rr,
                 sl_buffer_atr=args.sl_buffer_atr, min_body_atr=args.min_body_atr,
                 breakout_buffer_atr=args.breakout_buffer_atr, use_ema_filter=not args.no_ema_filter,
                 ema_len=args.ema_len, use_volume_filter=args.use_volume_filter,
                 volume_len=args.volume_len, volume_multiplier=args.volume_multiplier,
                 max_history=args.max_history)
    # Use the same event/position engine, but only replay signals as paper simulation.
    engine = PaperEngine(cfg, args.events)
    d = add_indicators(df, cfg)
    # Replay sequentially to simulate entries/exits deterministically.
    for i in range(len(d)):
        engine._process_bar(d, i, allow_entry=True, replay=False)
    logging.info("CSV processing complete: %d candles. Event log: %s", len(d), args.events)


def build_parser():
    p = argparse.ArgumentParser(description=BOT_NAME)
    p.add_argument("--mode", choices=["live-paper", "csv-backtest"], default="live-paper")
    p.add_argument("--csv", default="", help="Historical CSV for csv-backtest; also used as HISTORICAL_CSV seed in live mode")
    p.add_argument("--interval", type=int, default=1, choices=[1, 3, 5, 10, 15, 30, 60])
    p.add_argument("--history-days", type=int, default=5)
    p.add_argument("--max-history", type=int, default=2000)
    p.add_argument("--zone-len", type=int, default=20)
    p.add_argument("--atr-len", type=int, default=14)
    p.add_argument("--cons-bars", type=int, default=3)
    p.add_argument("--max-cons-atr", type=float, default=1.5)
    p.add_argument("--max-wait", type=int, default=12)
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--sl-buffer-atr", type=float, default=0.1)
    p.add_argument("--min-body-atr", type=float, default=0.10)
    p.add_argument("--breakout-buffer-atr", type=float, default=0.05)
    p.add_argument("--ema-len", type=int, default=50)
    p.add_argument("--no-ema-filter", action="store_true")
    p.add_argument("--use-volume-filter", action="store_true")
    p.add_argument("--volume-len", type=int, default=20)
    p.add_argument("--volume-multiplier", type=float, default=1.5)
    p.add_argument("--events", default="logs/zone_breakout_paper_events.csv")
    p.add_argument("--live-csv", default="data/zone_breakout_live_candles.csv")
    p.add_argument("--log-file", default="logs/zone_breakout_bot.log")
    return p


def main():
    args = build_parser().parse_args()
    setup_logging(args.log_file)
    logging.info("%s starting in %s mode", BOT_NAME, args.mode)
    if args.mode == "csv-backtest":
        run_csv_backtest(args)
    else:
        run_live_paper(args)


if __name__ == "__main__":
    def _stop_handler(signum, frame):
        logging.warning("Shutdown signal received.")
        STOP_EVENT.set()
    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)
    try:
        main()
    except Exception as exc:
        logging.exception("Fatal startup/runtime error: %s", exc)
        raise SystemExit(1)
