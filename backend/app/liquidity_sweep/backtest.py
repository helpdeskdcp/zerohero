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

from . import engine, indicators as indicators_mod, probability as probability_mod, resample

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


# --------------------------------------------------------------------------- #
#  Phase 1 (index-first brief) -- enriched per-signal feature capture         #
# --------------------------------------------------------------------------- #
# Needs a MUCH longer lookback than WINDOW_BARS (250, ~3.3 trading days) to
# build a real daily/4h EMA(20) -- structure.htf_bias's own _tf_bias() needs
# >=21 candles per timeframe. ~75 5m-bars/session * 80 sessions gives ~20
# daily candles with margin, and correspondingly more for the coarser-than-
# daily-but-finer-than-15m timeframes.
HTF_LOOKBACK_BARS = 6000


def _build_bars_by_tf_at(bars: list[dict], i: int, *, exec_window: int = WINDOW_BARS,
                          htf_lookback: int = HTF_LOOKBACK_BARS) -> dict:
    """Real bars_by_tf for engine.evaluate() at step `i`: the same causal 5m
    execution window PLUS real (resampled, not fabricated -- see
    resample.py's module docstring) 15m/30m/1h/4h/1d built from a longer,
    still-strictly-<=i, real-history slice."""
    exec_bars = bars[max(0, i - exec_window):i]
    htf_source = bars[max(0, i - htf_lookback):i]
    return {
        "5m": exec_bars,
        "15m": resample.resample_bars(htf_source, 15),
        "30m": resample.resample_bars(htf_source, 30),
        "1h": resample.resample_bars(htf_source, 60),
        "4h": resample.resample_bars(htf_source, 240),
        "1d": resample.resample_daily(htf_source),
    }


@dataclass
class Stage1FeatureSample:
    """~30 named features per signal for the Phase 1 statistical-discrimination
    analysis -- deliberately a SEPARATE, additive dataclass/walk from
    Stage1Sample/_walk_raw_signals above (which stay untouched: same tested
    contract, same 86 passing tests). Built as a second pass over the SAME
    signal indices _walk_raw_signals already found (see
    `_walk_raw_signals_with_features`), now with real HTF bars supplied so
    `regime`/`htf_score` are genuine measurements, not the silent constant
    RANGE/0.0 that resulted from Stage 1's original bars_by_tf={"5m": ...}
    only call."""
    index: int
    timestamp: str
    direction: str                    # BULLISH | BEARISH
    outcome: str                      # WIN | LOSS | TIMEOUT
    setup_score: float
    passed_checks: int
    chk_structure_aligned: bool
    chk_secondary_confirmation: bool
    chk_htf_aligned: bool
    chk_vwap_aligned: bool
    chk_ema_aligned: bool
    chk_rsi_not_extreme: bool
    chk_adx_trending: bool
    chk_volume_above_average: bool | None   # recomputed independently -- see note below
    regime: str                       # real htf_bias label (BULLISH/BEARISH/RANGE/TRANSITION)
    htf_score: float
    structure_type: str                # BOS | CHOCH
    cisd: bool
    fvg: bool
    order_block: bool
    sweep_kind: str                    # UPPER_SWEEP | LOWER_SWEEP
    level_source: str                  # PDH | PDL | EQUAL_HIGH | EQUAL_LOW
    sweep_reaction: float
    sweep_reaction_atr_ratio: float | None
    bars_to_reclaim: int
    rsi14: float | None
    macd: float | None
    adx: float | None
    atr14: float | None
    above_vwap: bool | None
    above_ema20: bool | None
    ema20_gt_ema50: bool | None
    volume: float | None
    volume_ratio: float | None          # signal-bar volume / mean volume of the exec window
    probability: float
    confidence: float
    rr: float
    risk_amount: float
    reward_amount: float
    time_bucket: str                    # OPENING | MID | CLOSING (session-relative)
    day_of_week: str

    def to_dict(self) -> dict:
        return asdict(self)


def _time_bucket(ts: str) -> str:
    """Session-relative bucket in IST wall-clock time (see resample.py for
    the UTC->IST conversion rationale). NSE: 09:15-15:30 IST."""
    dt = resample._parse_ts(ts)
    if dt is None:
        return "UNKNOWN"
    minutes = dt.hour * 60 + dt.minute
    if minutes < 10 * 60:            # before 10:00 IST
        return "OPENING"
    if minutes >= 14 * 60 + 30:      # 14:30 IST onward
        return "CLOSING"
    return "MID"


def _day_of_week(ts: str) -> str:
    dt = resample._parse_ts(ts)
    return dt.strftime("%A") if dt else "UNKNOWN"


def _walk_raw_signals_with_features(bars: list[dict], *, window: int = WINDOW_BARS,
                                     horizon: int = HORIZON_BARS,
                                     cooldown_bars: int | None = None,
                                     htf_lookback: int = HTF_LOOKBACK_BARS) -> list[Stage1FeatureSample]:
    """Pass 1: reuse the existing, tested `_walk_raw_signals` unchanged to
    find the signal indices + real outcomes (outcome labeling is pure
    forward price-action grading, independent of htf_bias, so it is
    identical either way). Pass 2: for those indices ONLY, rebuild real
    bars_by_tf (5m + genuinely resampled 15m/30m/1h/4h/1d) and call
    engine.evaluate() again to capture the full feature-rich output --
    cheap (~2,100 rebuilds, not ~43,000), because the expensive HTF resample
    only runs where a signal was already found."""
    raw = _walk_raw_signals(bars, window=window, horizon=horizon, cooldown_bars=cooldown_bars)
    out: list[Stage1FeatureSample] = []
    for s in raw:
        i = s.index
        bars_by_tf = _build_bars_by_tf_at(bars, i, exec_window=window, htf_lookback=htf_lookback)
        result = engine.evaluate(symbol="NIFTY", bars_by_tf=bars_by_tf, spot=bars_by_tf["5m"][-1]["c"],
                                 config={"min_probability": 0.0})
        if result["decision"] == "NO_TRADE":
            continue   # structurally shouldn't happen (see docstring) -- skip defensively, don't crash a 2000-sample run
        reasons = set(result["setup_score"]["reasons"])
        exec_bars = bars_by_tf["5m"]
        avg_volume = (sum((b.get("v") or 0) for b in exec_bars) / len(exec_bars)) if exec_bars else None
        sweep_bar_volume = None
        sw = result["liquidity_sweep"]
        sweep_idx_local = sw.get("sweep_bar_index")
        if sweep_idx_local is not None and 0 <= sweep_idx_local < len(exec_bars):
            sweep_bar_volume = exec_bars[sweep_idx_local].get("v")
        chk_volume_above_average = (
            (avg_volume is not None and sweep_bar_volume is not None and sweep_bar_volume > avg_volume)
            if avg_volume is not None and sweep_bar_volume is not None else None)
        ind = indicators_mod.snapshot(exec_bars)
        atr = ind.get("atr14")
        ema20, ema50 = ind.get("ema20"), ind.get("ema50")
        out.append(Stage1FeatureSample(
            index=i, timestamp=result["timestamp"], direction=s.direction, outcome=s.outcome,
            setup_score=result["setup_score"]["score_0_100"], passed_checks=result["setup_score"]["passed_checks"],
            chk_structure_aligned="STRUCTURE_BREAK_ALIGNED" in reasons,
            chk_secondary_confirmation="SECONDARY_CONFIRMATION_PRESENT" in reasons,
            chk_htf_aligned="HTF_BIAS_ALIGNED" in reasons,
            chk_vwap_aligned="VWAP_ALIGNED" in reasons,
            chk_ema_aligned="EMA20_ALIGNED" in reasons,
            chk_rsi_not_extreme="RSI_NOT_EXTREME" in reasons,
            chk_adx_trending="ADX_TRENDING" in reasons,
            chk_volume_above_average=chk_volume_above_average,
            regime=result["market_regime"], htf_score=result["htf_bias"]["score"],
            structure_type=result["structure"]["type"],
            cisd=result["confirmation"]["cisd"], fvg=result["confirmation"]["fvg"],
            order_block=result["confirmation"]["order_block"],
            sweep_kind=sw["kind"], level_source=sw["level_source"],
            sweep_reaction=sw["reaction"],
            sweep_reaction_atr_ratio=(sw["reaction"] / atr if atr else None),
            bars_to_reclaim=sw["bars_to_reclaim"],
            rsi14=ind.get("rsi14"), macd=ind.get("macd"), adx=ind.get("adx"), atr14=atr,
            above_vwap=ind.get("above_vwap"), above_ema20=ind.get("above_ema20"),
            ema20_gt_ema50=(ema20 > ema50) if (ema20 is not None and ema50 is not None) else None,
            volume=ind.get("volume"),
            volume_ratio=(ind.get("volume") / avg_volume if ind.get("volume") and avg_volume else None),
            probability=(result["probability"]["up"] if s.direction == "BULLISH"
                        else result["probability"]["down"]),
            confidence=result["confidence"],
            rr=result["rr"], risk_amount=result["risk_amount"], reward_amount=result["reward_amount"],
            time_bucket=_time_bucket(result["timestamp"]), day_of_week=_day_of_week(result["timestamp"]),
        ))
    return out


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
