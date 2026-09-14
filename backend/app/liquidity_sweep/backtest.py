"""
Two-stage backtest -- sections 19-20 (index-first brief) / 17-18 (original
brief). Kept strictly separate, per the user's explicit instruction: index
directional accuracy and option trade win rate are never combined into one
number.

STAGE 1 (rigorous): index direction + probability calibration, walked
CAUSALLY (bar-by-bar, a bounded recent window only -- see `WINDOW_BARS`'s
own docstring for why a full-history rescan per bar would make this
O(n^2) and untenable) over the REAL Kaggle NIFTY 5m dataset
(data/historical/kaggle/research_historical.db, 2015-2026, ~210K bars).
Chronological TRAIN -> VALIDATION -> OOS split (no shuffling -- shuffling a
time series before a walk-forward split would itself be a look-ahead leak).

STAGE 2 (pipeline validation only): the SAME engine, given real captured
option-chain candidates from the 10-day window (market_history.db,
2026-09-02..11). Explicitly and repeatedly labelled
"PIPELINE VALIDATION -- INSUFFICIENT FOR LONG-TERM PROFITABILITY CLAIM",
never reported as a profitability result.

Execution timeframe note: Kaggle has 5m bars only for NIFTY (no 1m), so a
genuine 3m confirmation candle cannot be constructed without fabricating
data (forbidden by the brief's own section 14) -- 5m is used as both the
sweep AND confirmation timeframe for Stage 1, a documented substitution,
not a silent one.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from . import engine, probability as probability_mod

KAGGLE_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "historical", "kaggle", "research_historical.db")

# Bounded lookback window fed to engine.evaluate() at each step. A full
# from-genesis rescan of swings/equal-levels at every one of ~200K bars
# would be O(n^2) (untenable); a live system would never do this either --
# a rolling window is both the tractable choice and the realistic one.
WINDOW_BARS = 250
HORIZON_BARS = 12          # ~1 hour on 5m bars -- the outcome-labeling horizon
THRESHOLD_PTS_ATR_MULT = 1.0   # the WIN/LOSS threshold for label_outcome, in ATR


def load_kaggle_nifty_bars(*, limit_years: float | None = 2.0, db_path: str | None = None) -> list[dict]:
    """Real NIFTY 5m OHLC from the Kaggle dataset. `limit_years`: restrict to
    the most RECENT N years for tractability (documented scope reduction,
    not a data-quality choice -- the full 2015-2026 range genuinely exists
    on disk, see backend/PHASE0-adjacent data-availability report)."""
    path = db_path or KAGGLE_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    where = "symbol='NIFTY' AND instrument_type='INDEX' AND timeframe='5m'"
    if limit_years is not None:
        max_ts = conn.execute(f"SELECT max(timestamp) FROM normalized_bars WHERE {where}").fetchone()[0]
        cutoff_year = int(max_ts[:4]) - int(limit_years)
        cutoff = f"{cutoff_year}-01-01"
        where += f" AND timestamp >= '{cutoff}'"
    rows = conn.execute(
        f"SELECT timestamp, open, high, low, close, volume FROM normalized_bars "
        f"WHERE {where} ORDER BY timestamp").fetchall()
    conn.close()
    return [{"t": r["timestamp"], "o": r["open"], "h": r["high"], "l": r["low"],
            "c": r["close"], "v": r["volume"] or 0.0} for r in rows]


@dataclass
class Stage1Sample:
    index: int
    timestamp: str
    direction: str            # BULLISH | BEARISH
    setup_score: float
    regime: str
    signal_type: str
    outcome: str              # WIN | LOSS | TIMEOUT

    def to_dict(self) -> dict:
        return asdict(self)


def _walk_raw_signals(bars: list[dict], *, window: int = WINDOW_BARS,
                       horizon: int = HORIZON_BARS, cooldown_bars: int | None = None) -> list[Stage1Sample]:
    """Causal walk: at each index i, evaluate() sees ONLY bars[max(0,i-window):i]
    -- never bars[i:] (the future). A signal's outcome is labeled using
    bars[i:i+horizon], which is fine (standard backtest grading, not a
    look-ahead: those bars are never used to COMPUTE the signal, only to
    grade it afterward).

    `cooldown_bars` (default: `horizon`, a plain, documented choice -- don't
    consider a new signal until the previous one's own outcome window has
    finished) exists because sweep.detect_sweep's reclaim_window means a
    single real sweep event can otherwise satisfy the "confirmed sweep"
    condition on several CONSECUTIVE bars in a row (found empirically while
    building this backtest: an un-deduplicated walk fired 3 near-identical
    "signals" at bars 250/251/252 for what was clearly one underlying
    event). Without a cooldown this inflates the raw signal count and
    double(triple)-counts the same real event's outcome in the accuracy
    stats -- a real bug this pilot run caught, not a stylistic choice."""
    cooldown = horizon if cooldown_bars is None else cooldown_bars
    samples: list[Stage1Sample] = []
    n = len(bars)
    i = window
    while i < n - horizon:
        window_bars = bars[i - window:i]
        out = engine.evaluate(symbol="NIFTY", bars_by_tf={"5m": window_bars}, spot=window_bars[-1]["c"],
                              config={"min_probability": 0.0})   # capture every STRUCTURALLY valid setup
        if out["decision"] == "NO_TRADE":
            i += 1
            continue
        direction = "BULLISH" if out["decision"] == "BUY_CE" else "BEARISH"
        entry = out["entry"]
        risk = out["risk_amount"]
        threshold = risk if risk else (window_bars[-1]["c"] * 0.003)
        outcome = probability_mod.label_outcome(bars[i:i + horizon], entry=entry, direction=direction,
                                                threshold_pts=threshold, horizon=horizon)
        samples.append(Stage1Sample(
            index=i, timestamp=out["timestamp"], direction=direction,
            setup_score=out["setup_score"]["score_0_100"], regime=out["market_regime"],
            signal_type=out["liquidity_sweep"]["kind"], outcome=outcome))
        i += cooldown   # skip the cooldown window -- next signal must be a genuinely new event
    return samples


def _split_chronological(samples: list[Stage1Sample], *, train_frac=0.5, val_frac=0.25):
    n = len(samples)
    a, b = int(n * train_frac), int(n * (train_frac + val_frac))
    return samples[:a], samples[a:b], samples[b:]


def _accuracy(samples: list[Stage1Sample]) -> dict:
    graded = [s for s in samples if s.outcome != "TIMEOUT"]
    if not graded:
        return {"n": len(samples), "n_graded": 0, "directional_accuracy": None}
    wins = sum(1 for s in graded if s.outcome == "WIN")
    return {"n": len(samples), "n_graded": len(graded),
            "n_timeout": len(samples) - len(graded),
            "directional_accuracy": round(wins / len(graded), 4)}


def run_stage1_backtest(bars: list[dict] | None = None, *, window: int = WINDOW_BARS,
                         horizon: int = HORIZON_BARS, thresholds=(0.55, 0.60, 0.65, 0.70, 0.75, 0.80)) -> dict:
    bars = bars if bars is not None else load_kaggle_nifty_bars()
    samples = _walk_raw_signals(bars, window=window, horizon=horizon)

    train, val, oos = _split_chronological(samples)
    calib_input = [{"score": s.setup_score, "regime": s.regime, "signal_type": s.signal_type,
                    "win": s.outcome == "WIN"} for s in train if s.outcome != "TIMEOUT"]
    calib = probability_mod.fit_index_calibration(calib_input, version="liquidity-sweep-stage1-v1")

    def _score_split(split: list[Stage1Sample]) -> dict:
        graded = [s for s in split if s.outcome != "TIMEOUT"]
        pairs = [(probability_mod.predict_directional_probability(
                    calib, s.setup_score, regime=s.regime, signal_type=s.signal_type),
                  s.outcome == "WIN") for s in graded]
        reliability = probability_mod.reliability_report(pairs, bins=10) if pairs else None
        by_threshold = {}
        for thr in thresholds:
            passed = [(p, w) for p, w in pairs if p >= thr]
            by_threshold[thr] = {
                "n": len(passed),
                "accuracy": round(sum(w for _, w in passed) / len(passed), 4) if passed else None,
            }
        return {**_accuracy(split), "reliability": reliability, "by_threshold": by_threshold}

    return {
        "status": "OK" if samples else "NO_SIGNALS_GENERATED",
        "data_source": "Kaggle NIFTY 5m real OHLC (data/historical/kaggle/research_historical.db)",
        "n_bars_walked": max(0, len(bars) - window - horizon),
        "n_raw_signals": len(samples),
        "window_bars": window, "horizon_bars": horizon,
        "execution_timeframe_note": "5m used for both sweep and confirmation -- Kaggle has no 1m NIFTY "
                                    "bars to build genuine 3m candles from without fabricating data",
        "train": {"n": len(train), **_score_split(train)},
        "validation": {"n": len(val), **_score_split(val)},
        "oos": {"n": len(oos), **_score_split(oos)},
        "calibration": calib,
    }


# --------------------------------------------------------------------------- #
#  Stage 2 -- option execution pipeline validation (10-day real window)       #
# --------------------------------------------------------------------------- #
MARKET_HISTORY_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))), "data", "market_history.db")


def load_real_option_candidates(*, symbol: str, as_of_ts: str, option_type: str,
                                 db_path: str | None = None, max_age_sec: int = 300) -> list[dict]:
    """Real captured option-chain legs for `symbol`/`option_type`, as of the
    most recent snapshot at or before `as_of_ts` (never after -- same
    causal contract as everything else in this package)."""
    path = db_path or MARKET_HISTORY_DB
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT strike, ltp, bid, ask, oi, volume FROM quote_snapshots "
        "WHERE symbol=? AND option_type=? AND received_ts <= ? "
        "AND received_ts >= datetime(?, ?) ORDER BY received_ts DESC LIMIT 200",
        (symbol, option_type, as_of_ts, as_of_ts, f"-{max_age_sec} seconds")).fetchall()
    by_strike = {}
    for r in rows:
        if r["strike"] not in by_strike:
            by_strike[r["strike"]] = {"strike": r["strike"], "ltp": r["ltp"], "bid": r["bid"],
                                      "ask": r["ask"], "oi": r["oi"], "volume": r["volume"], "delta": None}
    # real Greeks, same as_of/max_age window -- never a value from AFTER as_of_ts
    greek_rows = conn.execute(
        "SELECT strike, delta FROM option_greeks WHERE underlying=? AND option_type=? "
        "AND received_ts <= ? AND received_ts >= datetime(?, ?) ORDER BY received_ts DESC",
        (symbol, option_type, as_of_ts, as_of_ts, f"-{max_age_sec} seconds")).fetchall()
    conn.close()
    for r in greek_rows:
        if r["strike"] in by_strike and by_strike[r["strike"]]["delta"] is None:
            by_strike[r["strike"]]["delta"] = r["delta"]
    return list(by_strike.values())


def run_stage2_pipeline_validation(*, symbol: str = "NIFTY", db_path: str | None = None) -> dict:
    """NOT a profitability backtest -- see module docstring. Walks the real
    10-day captured underlying+option window and reports whether the
    end-to-end pipeline (index signal -> ITM strike -> entry) produces
    sane output, and exactly how many real signals it found."""
    path = db_path or MARKET_HISTORY_DB
    if not os.path.exists(path):
        return {"status": "BLOCKED", "reason": f"{path} not found"}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT bar_start AS t, o, h, l, c, v FROM market_candles "
        "WHERE symbol=? AND kind='INDEX' AND tf='5m' ORDER BY bar_start", (symbol,)).fetchall()
    conn.close()
    bars = [dict(r) for r in rows]
    if len(bars) < WINDOW_BARS + 5:
        return {"status": "BLOCKED", "reason": f"only {len(bars)} real {symbol} index bars captured "
                "(need >= {WINDOW_BARS+5}) -- see PHASE0 data-availability report"}

    results = []
    n_samples = 25   # pipeline validation only -- not exhaustive coverage; quote_snapshots (1.8M rows,
                      # unindexed for this ad-hoc time-range query) makes each iteration expensive
    step = max(1, (len(bars) - WINDOW_BARS) // n_samples)
    for i in range(WINDOW_BARS, len(bars), step):
        window_bars = bars[i - WINDOW_BARS:i]
        spot = window_bars[-1]["c"]
        ts = window_bars[-1]["t"]
        ce = load_real_option_candidates(symbol=symbol, as_of_ts=ts, option_type="CE", db_path=path)
        pe = load_real_option_candidates(symbol=symbol, as_of_ts=ts, option_type="PE", db_path=path)
        out = engine.evaluate(symbol=symbol, bars_by_tf={"5m": window_bars}, spot=spot,
                              option_candidates={"CE": ce, "PE": pe}, config={"min_probability": 0.0})
        results.append(out)

    signals = [r for r in results if r["decision"] != "NO_TRADE"]
    option_evaluated = [r for r in signals if r.get("option", {}).get("status") == "OK"]
    return {
        "status": "PIPELINE VALIDATION -- INSUFFICIENT FOR LONG-TERM PROFITABILITY CLAIM",
        "symbol": symbol, "window_checked": len(results), "n_bars_available": len(bars),
        "n_index_signals": len(signals), "n_with_eligible_option_strike": len(option_evaluated),
        "date_range": (bars[0]["t"], bars[-1]["t"]) if bars else None,
        "sample_signals": [r for r in signals[:5]],
        "note": "10 real captured days -- confirms the pipeline runs end-to-end on real option data; "
                "not remotely enough for a win-rate/PF claim (see PHASE0 data-availability report).",
    }
