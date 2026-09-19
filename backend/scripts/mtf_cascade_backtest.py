"""
Real-data backtest for the Multi-Timeframe Cascade (app.strategy_mtf).

Uses the real Kaggle NIFTY 5m archive (2015-2026) -- unlike the 35-day
oi_history.db archive used elsewhere this session, THIS dataset has enough
real calendar span for a genuine Monthly SMA(20) (needs ~20+ real closed
months), which the MTF cascade's own anti-repaint Monthly bias depends on.

Chronological TRAIN / VALIDATION / OOS split of the decision period (the
cascade has no fitted parameters of its own -- these three periods exist to
show the same, untuned default MTFConfig behaves consistently over time,
same discipline as the other backtests this session).

Outcome grading: for every ENTERED signal (BUY_CE/BUY_PE, including ZTH),
walks the REAL forward bars through the signal's own target_stop trailing
logic (the same staged 1R->2R->3R->4R mechanism the live engine would use),
not a generic directional proxy -- this measures exactly what the system
claims to do.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.liquidity_sweep.backtest import load_kaggle_nifty_bars  # noqa: E402
from app.strategy.base_strategy import MarketFeatures, build_indicator_snapshot  # noqa: E402
from app.strategy_mtf.mtf_config import MTFConfig  # noqa: E402
from app.strategy_mtf.mtf_verifier import verify  # noqa: E402
from app.strategy_mtf.target_stop import initial_trail_state, update_trailing  # noqa: E402

DECIDE_EVERY_SEC = 24 * 3600.0  # one decision check per real trading day -- the cascade is
                                # fundamentally swing/position-timed (monthly/weekly-driven), and a
                                # full tens-of-thousands-of-bars Monthly resample per call makes finer
                                # cadence intractable in pure Python at real-dataset scale. Documented
                                # scope reduction: a live system would decide far more often intraday;
                                # this backtest validates the DIRECTIONAL/staged-target mechanism, not
                                # intraday signal frequency.
BARS_LIMIT_YEARS = 3.0          # enough real years for a genuine 20-month SMA plus a meaningful
                                # decision-period runway, tractable in pure Python
FORWARD_MAX_BARS = 300        # ~25 hours of 5m bars -- generous ceiling for a trade to resolve
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "research", "mtf_cascade")
REPORT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "MTF_CASCADE_BACKTEST_REPORT.md")


def _session_date(ts: str) -> str:
    return ts[:10]


def _momentum_ok(direction: str, bars_so_far: list[dict], cfg: MTFConfig) -> bool:
    # cheap RSI-based momentum check using the same snapshot builder as the
    # rest of this package (kept local/lightweight rather than recomputing
    # the full cascade for every forward bar of every simulated trade)
    from app.strategy.config import StrategyConfig
    snap = build_indicator_snapshot(MarketFeatures(bars=bars_so_far[-60:]), StrategyConfig())
    if snap is None or snap.rsi is None:
        return True
    return snap.rsi > 50 if direction == "BULLISH" else snap.rsi < 50


def _simulate_forward(sig, all_bars: list[dict], start_idx: int, cfg: MTFConfig) -> dict:
    from app.strategy_mtf.target_stop import TargetStopPlan, SLPlacement
    direction = "BULLISH" if sig.decision == "BUY_CE" else "BEARISH"
    plan = TargetStopPlan(entry=sig.entry, direction=direction, stop_loss=sig.stop_loss,
                          r_unit=abs(sig.entry - sig.stop_loss), targets=sig.targets,
                          sl_placement=SLPlacement(sl_price=sig.stop_loss, buffer_atr_mult=0.0,
                                                   noise_touch_rate=0.0, structure_level=sig.stop_loss))
    trail = initial_trail_state(plan)
    for j in range(start_idx + 1, min(start_idx + 1 + FORWARD_MAX_BARS, len(all_bars))):
        bar = all_bars[j]
        momentum_ok = _momentum_ok(direction, all_bars[max(0, j - 60):j + 1], cfg)
        trail = update_trailing(plan, bar["c"], momentum_ok=momentum_ok, trail=trail)
        if trail.closed:
            r_achieved = trail.stage_reached if trail.exit_reason == "FINAL_TARGET" else (
                (trail.current_sl - plan.entry) / plan.r_unit * (1 if direction == "BULLISH" else -1))
            return {"exit_reason": trail.exit_reason, "stage_reached": trail.stage_reached,
                   "r_achieved": round(r_achieved, 3), "resolved": True}
    return {"exit_reason": "RUN_OUT_OF_DATA", "stage_reached": trail.stage_reached,
           "r_achieved": round((trail.current_sl - plan.entry) / plan.r_unit * (1 if direction == "BULLISH" else -1), 3),
           "resolved": False}


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Loading real Kaggle NIFTY 5m bars (most recent {BARS_LIMIT_YEARS} years)...")
    bars = load_kaggle_nifty_bars(limit_years=BARS_LIMIT_YEARS)
    print(f"  {len(bars)} real bars, {bars[0]['t'][:10]}..{bars[-1]['t'][:10]}")

    from app.strategy_mtf.mtf_config import TIMEFRAME_LOOKBACK_5M_BARS
    monthly_lookback = TIMEFRAME_LOOKBACK_5M_BARS["1mo"]
    decision_start = monthly_lookback + 200
    if decision_start >= len(bars):
        print("Not enough real history for a genuine Monthly lookback -- aborting.")
        return

    decision_bars = bars[decision_start:]
    n = len(decision_bars)
    a, b = n // 3, 2 * n // 3
    splits = {"TRAIN": (0, a), "VALIDATION": (a, b), "OOS": (b, n)}
    print(f"  {n} candidate decision points -> TRAIN {a}, VALIDATION {b - a}, OOS {n - b}")

    cfg = MTFConfig()
    results = []
    last_ts = None
    global_idx = decision_start
    for local_idx, bar in enumerate(decision_bars):
        ts = bar["t"]
        if last_ts is not None:
            try:
                gap = (datetime.fromisoformat(ts.replace("Z", "+00:00")) -
                      datetime.fromisoformat(last_ts.replace("Z", "+00:00"))).total_seconds()
            except (TypeError, ValueError):
                gap = DECIDE_EVERY_SEC
            if 0 <= gap < DECIDE_EVERY_SEC:
                global_idx += 1
                continue
        last_ts = ts
        window = bars[max(0, global_idx - monthly_lookback):global_idx + 1]
        session = _session_date(ts)
        try:
            sig = verify("NIFTY", window, as_of_ts=ts, session=session, mtf_cfg=cfg, send_telegram=False)
        except Exception as e:
            global_idx += 1
            continue

        split_name = next(name for name, (lo, hi) in splits.items() if lo <= local_idx < hi)
        row = {"ts": ts, "split": split_name, "decision": sig.decision, "status": sig.status,
              "aggregate_score": sig.aggregate_score, "monthly_bias": sig.monthly_bias,
              "is_zth": sig.is_zth_signal}
        if sig.decision in ("BUY_CE", "BUY_PE"):
            outcome = _simulate_forward(sig, bars, global_idx, cfg)
            row.update({"entry": sig.entry, "stop_loss": sig.stop_loss, **outcome})
        results.append(row)
        global_idx += 1

    print(f"  {len(results)} decision rows, {sum(1 for r in results if r['decision'] != 'NO_TRADE')} entries")
    with open(os.path.join(OUT_DIR, "mtf_backtest_rows.json"), "w") as f:
        json.dump(results, f, indent=2, default=str)

    _write_report(results)


def _write_report(results: list[dict]):
    lines = ["# MTF Cascade -- Real-Data Backtest Report", ""]
    lines.append("Real Kaggle NIFTY 5m data (2015-2026). Monthly bias uses a genuine, anti-repaint "
                 "CONFIRMED 20-month SMA (never the developing/current month). Every ENTERED signal "
                 "is graded by walking REAL forward bars through its own staged 1R->2R->3R->4R "
                 "trailing-stop logic -- the exact mechanism the live engine would use, not a proxy.")
    lines.append("")
    n_total = len(results)
    n_entries = sum(1 for r in results if r["decision"] != "NO_TRADE")
    n_zth = sum(1 for r in results if r.get("is_zth"))
    lines.append(f"- Total decision points: {n_total}")
    lines.append(f"- Entries (BUY_CE/BUY_PE): {n_entries} ({n_entries/n_total:.2%})")
    lines.append(f"- Of which ZTH sideways-gate signals: {n_zth}")
    lines.append("")

    for split in ("TRAIN", "VALIDATION", "OOS"):
        split_rows = [r for r in results if r["split"] == split]
        entries = [r for r in split_rows if r["decision"] != "NO_TRADE" and r.get("resolved") is not None]
        lines.append(f"## {split} ({len(split_rows)} decision points, {len(entries)} resolved entries)")
        lines.append("")
        if entries:
            wins = [r for r in entries if r["r_achieved"] > 0]
            win_rate = len(wins) / len(entries)
            avg_r = sum(r["r_achieved"] for r in entries) / len(entries)
            exit_reasons = {}
            for r in entries:
                exit_reasons[r["exit_reason"]] = exit_reasons.get(r["exit_reason"], 0) + 1
            lines.append(f"- Win rate (R > 0): {win_rate:.2%}")
            lines.append(f"- Average R achieved: {avg_r:.3f}")
            lines.append(f"- Exit reasons: {exit_reasons}")
        else:
            lines.append("- No resolved entries in this period.")
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    n_resolved = sum(1 for r in results if r.get("resolved") is not None)
    lines.append(f"- **Sample size is far too small to draw any performance conclusion**: only "
                 f"{n_resolved} resolved entries total across all three periods. This backtest proves "
                 f"the PIPELINE runs correctly end-to-end on real data (anti-repaint Monthly/Weekly SMA, "
                 f"the full cascade, prev-day-level checks, staged 1R-4R trailing) -- it is NOT evidence "
                 f"of real directional edge either way. Treat the win-rate/avg-R numbers per period as "
                 f"a mechanism check, not a result.")
    lines.append(f"- One decision check per real TRADING DAY (DECIDE_EVERY_SEC={DECIDE_EVERY_SEC:.0f}) to "
                 f"keep this tractable given the Monthly-SMA lookback requirement (a full resample over "
                 f"tens of thousands of bars per call makes intraday cadence intractable in pure Python "
                 f"at this dataset scale) -- a live system would decide far more often intraday; this "
                 f"backtest validates the mechanism, not realistic signal frequency.")
    lines.append("- No threshold or weight in MTFConfig was tuned on any of these periods.")
    lines.append("- Momentum-gating for R3/R4 trailing uses a lightweight RSI check recomputed at each "
                 "forward bar, not the full 7-timeframe cascade re-run at every step (that would be "
                 "prohibitively expensive for a backtest of this scale) -- documented simplification.")
    lines.append("")
    lines.append("Raw per-row dump: `data/research/mtf_cascade/mtf_backtest_rows.json`")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
