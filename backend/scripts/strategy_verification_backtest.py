"""
Standalone backtest: RAW signal performance vs Strategy-Verified signal
performance, on the real 35-day NIFTY option-chain archive
(/root/oi_dashboard/oi_history.db) -- the same real data source used by
scripts/signal_pipeline_audit.py.

RAW SIGNAL = the EXISTING, unmodified production signal generator
(app.engines.state_classifier.classify(), fed by app.engines.sr_engine.
compute_sr()) -- BULLISH -> "BUY", BEARISH -> "SELL", NONE -> no signal.
Neither function is modified; both are only imported and called, per the
mission's own "do not duplicate/replace existing signal-generation
engines" instruction.

VERIFIED SIGNAL = app.strategy.StrategyVerifier.verify() run on the SAME
raw signal + market features, only counting an ENTRY_READY-confirmed
CE/PE as a trade.

Chronological TRAIN / VALIDATION / OOS split of the real session dates
present in the archive (no shuffling). The Strategy Verification Engine
has no calibration-fit step of its own (it is a fixed, configured rule set,
not a fitted model) -- these three periods exist to show the SAME default,
untuned config performs consistently across time, not to fit anything on
TRAIN. No threshold was tuned to any period's result before this run.

Outcome grading (both RAW and VERIFIED, so the comparison is apples-to-
apples): the same ATR-normalized forward-index-move proxy used throughout
this codebase's other research work (12-bar horizon, 1x ATR threshold) --
WIN if the read direction is confirmed, LOSS otherwise. This backtest is
about DIRECTIONAL signal quality (raw vs verified), not option-premium P&L
-- a generic BUY/SELL raw signal has no option contract of its own to
price against, unlike scalp_strategy's fully-selected trades.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.backtest import oi_history_adapter as ad  # noqa: E402
from app.engines.sr_engine import compute_sr  # noqa: E402
from app.engines.state_classifier import classify  # noqa: E402
from app.strategy.base_strategy import MarketFeatures  # noqa: E402
from app.strategy.config import StrategyConfig  # noqa: E402
from app.strategy.strategy_verifier import StrategyVerifier  # noqa: E402

SYMBOL = "NIFTY"
FULL_RANGE = ("2026-07-13", "2026-08-28")
DECIDE_EVERY_SEC = 60.0
WINDOW_BARS = 60          # causal lookback fed to compute_sr/classify/the verifier each step
HORIZON_BARS = 12         # ~1h on 5m -- same convention as the rest of this codebase
ATR_THRESHOLD_MULT = 1.0

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "research", "strategy_verification")
REPORT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "STRATEGY_VERIFICATION_BACKTEST_REPORT.md")


def _label(entry_idx: int, bars: list[dict], atr: float | None) -> str:
    if not atr or atr <= 0 or entry_idx + 1 >= len(bars):
        return "TIMEOUT"
    entry = bars[entry_idx]["c"]
    up_t, dn_t = entry + ATR_THRESHOLD_MULT * atr, entry - ATR_THRESHOLD_MULT * atr
    for b in bars[entry_idx + 1: entry_idx + 1 + HORIZON_BARS]:
        hit_up, hit_dn = b["h"] >= up_t, b["l"] <= dn_t
        if hit_up and hit_dn:
            return "AMBIGUOUS"
        if hit_up:
            return "UP"
        if hit_dn:
            return "DOWN"
    return "TIMEOUT"


def _mfe_mae(entry_idx: int, bars: list[dict], direction_up: bool, horizon: int) -> tuple[float, float]:
    entry = bars[entry_idx]["c"]
    window = bars[entry_idx + 1: entry_idx + 1 + horizon]
    if not window:
        return 0.0, 0.0
    highs = [b["h"] - entry for b in window]
    lows = [b["l"] - entry for b in window]
    if direction_up:
        return max(highs, default=0.0), -min(lows, default=0.0)
    return -min(lows, default=0.0), max(highs, default=0.0)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Loading real {SYMBOL} 5m index bars {FULL_RANGE[0]}..{FULL_RANGE[1]}...")
    index_bars = ad.resample_candles(SYMBOL, "5m", kind="index", start=FULL_RANGE[0], end=FULL_RANGE[1]).get("candles") or []
    print(f"  {len(index_bars)} bars")

    session_dates = sorted({b["t"][:10] for b in index_bars})
    n = len(session_dates)
    a, b = n // 3, 2 * n // 3
    splits = {"TRAIN": set(session_dates[:a]), "VALIDATION": set(session_dates[a:b]), "OOS": set(session_dates[b:])}
    print(f"  {n} session dates -> TRAIN {a}, VALIDATION {b - a}, OOS {n - b}")

    verifier = StrategyVerifier(StrategyConfig())
    rows = []
    last_ts = None
    for i in range(WINDOW_BARS, len(index_bars) - HORIZON_BARS):
        ts = index_bars[i]["t"]
        if last_ts is not None:
            try:
                gap = (datetime.fromisoformat(ts) - datetime.fromisoformat(last_ts)).total_seconds()
            except (TypeError, ValueError):
                gap = DECIDE_EVERY_SEC
            if 0 <= gap < DECIDE_EVERY_SEC:
                continue
        last_ts = ts
        window = index_bars[i - WINDOW_BARS:i + 1]
        bars_by_tf = {"5m": window}

        sr = compute_sr(bars_by_tf, mode="index", symbol=SYMBOL)
        if sr.get("status") != "OK":
            continue
        state = classify(bars_by_tf, sr)
        raw_direction = state.get("direction")
        raw_signal = {"BULLISH": "BUY", "BEARISH": "SELL"}.get(raw_direction)
        if raw_signal is None:
            continue

        support = (sr.get("support") or {}).get("level")
        resistance = (sr.get("resistance") or {}).get("level")
        feats = MarketFeatures(bars=window, support=support, resistance=resistance)
        vr = verifier.verify(symbol=SYMBOL, raw_signal=raw_signal, features=feats, timestamp=ts)

        atr = sr.get("atr")
        label = _label(i, index_bars, atr)
        raw_win = (raw_signal == "BUY" and label == "UP") or (raw_signal == "SELL" and label == "DOWN")
        raw_loss = label in ("UP", "DOWN") and not raw_win
        mfe, mae = _mfe_mae(i, index_bars, direction_up=(raw_signal == "BUY"), horizon=HORIZON_BARS)

        session = ts[:10]
        split = next((k for k, s in splits.items() if session in s), "OOS")
        rows.append({
            "ts": ts, "split": split, "raw_signal": raw_signal,
            "strategy_direction": vr.strategy_direction, "status": vr.status, "state": vr.state,
            "entry_allowed": vr.entry_allowed, "ce_score": vr.ce_score, "pe_score": vr.pe_score,
            "label": label, "raw_win": raw_win, "raw_loss": raw_loss, "mfe": mfe, "mae": mae,
        })

    print(f"  {len(rows)} decision rows captured")
    with open(os.path.join(OUT_DIR, "backtest_rows.json"), "w") as f:
        json.dump(rows, f, indent=2, default=str)

    _write_report(rows, splits)


def _stats_for(rows: list[dict], *, verified: bool) -> dict:
    if verified:
        graded = [r for r in rows if r["entry_allowed"] and r["label"] in ("UP", "DOWN")]
        is_win = [(r["strategy_direction"] == "CE" and r["label"] == "UP")
                 or (r["strategy_direction"] == "PE" and r["label"] == "DOWN") for r in graded]
        side_of = lambda r: r["strategy_direction"]  # noqa: E731
    else:
        graded = [r for r in rows if r["label"] in ("UP", "DOWN")]
        is_win = [r["raw_win"] for r in graded]
        side_of = lambda r: r["raw_signal"]  # noqa: E731

    n = len(graded)
    n_wins = sum(is_win)
    n_losses = n - n_wins
    win_rate = round(n_wins / n, 4) if n else None
    r_per_trade = [1.0 if w else -1.0 for w in is_win]
    equity, peak, mdd = 0.0, 0.0, 0.0
    for r in r_per_trade:
        equity += r
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    avg_mfe = round(sum(r["mfe"] for r in graded) / n, 4) if n else None
    avg_mae = round(sum(r["mae"] for r in graded) / n, 4) if n else None

    ce_idx = [i for i, r in enumerate(graded) if side_of(r) in ("CE", "BUY")]
    pe_idx = [i for i, r in enumerate(graded) if side_of(r) in ("PE", "SELL")]
    ce_acc = round(sum(is_win[i] for i in ce_idx) / len(ce_idx), 4) if ce_idx else None
    pe_acc = round(sum(is_win[i] for i in pe_idx) / len(pe_idx), 4) if pe_idx else None
    return {
        "n_graded": n, "wins": n_wins, "losses": n_losses, "win_rate": win_rate,
        "profit_factor": round(n_wins / n_losses, 3) if n_losses else None,
        "expectancy_r": round(sum(r_per_trade) / n, 4) if n else None,
        "avg_favorable_excursion": avg_mfe, "avg_adverse_excursion": avg_mae,
        "max_drawdown_r": round(mdd, 2), "ce_accuracy": ce_acc, "pe_accuracy": pe_acc,
    }


def _write_report(rows: list[dict], splits: dict):
    lines = ["# Strategy Verification Engine -- Backtest Report", ""]
    lines.append("RAW signal = existing, unmodified `state_classifier.classify()` (fed by "
                 "`sr_engine.compute_sr()`), read-only, unchanged. VERIFIED signal = the same raw "
                 "signal passed through `StrategyVerifier` -- only counted when the state machine "
                 "reaches ENTRY_READY. Both graded with the same ATR-normalized forward-index-move "
                 "proxy (12-bar horizon, 1x ATR) -- this measures DIRECTIONAL signal quality, not "
                 "option-premium P&L (a generic BUY/SELL raw signal has no contract to price).")
    lines.append("")
    lines.append(f"- Data: real {SYMBOL} 5m index bars, `/root/oi_dashboard/oi_history.db`, "
                 f"{FULL_RANGE[0]}..{FULL_RANGE[1]}")
    lines.append(f"- Total decision rows: {len(rows)}")
    lines.append(f"- Total raw signals (BUY or SELL): {len(rows)}")
    verified_signals = [r for r in rows if r["strategy_direction"] in ("CE", "PE")]
    lines.append(f"- Verified signals (CE or PE scored, any state): {len(verified_signals)}")
    lines.append(f"- CE signals: {sum(1 for r in rows if r['strategy_direction']=='CE')}")
    lines.append(f"- PE signals: {sum(1 for r in rows if r['strategy_direction']=='PE')}")
    lines.append(f"- NO_TRADE: {sum(1 for r in rows if r['strategy_direction']=='NO_TRADE')}")
    lines.append(f"- Signal conflicts: {sum(1 for r in rows if r['status']=='SIGNAL_CONFLICT')}")
    lines.append(f"- Entry-ready (final verified trades): {sum(1 for r in rows if r['entry_allowed'])}")
    lines.append("")

    for split_name in ("TRAIN", "VALIDATION", "OOS"):
        split_rows = [r for r in rows if r["split"] == split_name]
        raw_stats = _stats_for(split_rows, verified=False)
        ver_stats = _stats_for(split_rows, verified=True)
        lines.append(f"## {split_name} ({len(split_rows)} decision rows)")
        lines.append("")
        lines.append("| Metric | BEFORE (raw signal) | AFTER (verified signal) |")
        lines.append("|---|---|---|")
        for key, label in (
            ("n_graded", "Graded signals"), ("win_rate", "Win rate"),
            ("profit_factor", "Profit factor"), ("expectancy_r", "Expectancy (R)"),
            ("avg_favorable_excursion", "Avg favorable excursion"),
            ("avg_adverse_excursion", "Avg adverse excursion"),
            ("max_drawdown_r", "Max drawdown (R)"), ("ce_accuracy", "CE accuracy"),
            ("pe_accuracy", "PE accuracy"),
        ):
            lines.append(f"| {label} | {raw_stats[key]} | {ver_stats[key]} |")
        false_signal_reduction = None
        if raw_stats["n_graded"]:
            false_signal_reduction = round(1 - (ver_stats["n_graded"] / raw_stats["n_graded"]), 4)
        lines.append(f"| Signal volume reduction | -- | {false_signal_reduction} |")
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append("- Outcome grading is an ATR-normalized directional proxy, not real option P&L -- "
                 "profit factor/expectancy/drawdown here are in R-multiples (1R = the ATR threshold), "
                 "not points or premium.")
    lines.append("- No threshold or weight was tuned on any of these three periods before this run -- "
                 "the same default StrategyConfig() was used throughout, so TRAIN/VALIDATION/OOS "
                 "consistency (or the lack of it) is genuine, not a leakage artifact.")
    lines.append("- \"Win rate\" for the raw signal counts EVERY BUY/SELL read; the verified signal's "
                 "win rate only counts rows that reached ENTRY_READY -- the entire point of comparing "
                 "them is whether the verification layer trades less often but more accurately.")
    lines.append("")
    lines.append("Raw per-row dump: `data/research/strategy_verification/backtest_rows.json`")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
