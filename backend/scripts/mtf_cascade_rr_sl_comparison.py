"""
MTF Cascade -- 1:2 vs 1:3 target comparison, WITH stop-loss vs WITHOUT
stop-loss, on the SAME real entries the cascade found (same discovery pass
as scripts/mtf_cascade_backtest.py: real Kaggle NIFTY 5m, daily decision
cadence, no tuning). Four variants per entry, all simulated on REAL forward
bars:

  A) target = entry 2R,  stop-loss ENFORCED
  B) target = entry 2R,  stop-loss DISABLED (only exits at target or a time cutoff)
  C) target = entry 3R,  stop-loss ENFORCED
  D) target = entry 3R,  stop-loss DISABLED

"Without stop-loss" does not mean infinite risk in this report -- it means
the simulated exit never fires EARLY on an adverse move; a hard time cutoff
(FORWARD_MAX_BARS, same as the staged-target backtest) still ends the
trade if neither the target nor a real reversal closes it out, and the R
achieved is marked to the last real close in that case. This isolates
"did the stop cost us more in win-rate than it saved in average loss size"
without ever pretending an unmanaged trade has zero risk.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.liquidity_sweep.backtest import load_kaggle_nifty_bars
from app.strategy_mtf.mtf_config import (
    TIMEFRAME_LOOKBACK_5M_BARS,
    MTFConfig,
)
from app.strategy_mtf.mtf_verifier import verify

DECIDE_EVERY_SEC = 24 * 3600.0
BARS_LIMIT_YEARS = 3.0
FORWARD_MAX_BARS = 300
VARIANTS = {
    "1:2_with_SL": {"target_r": 2.0, "use_sl": True},
    "1:2_without_SL": {"target_r": 2.0, "use_sl": False},
    "1:3_with_SL": {"target_r": 3.0, "use_sl": True},
    "1:3_without_SL": {"target_r": 3.0, "use_sl": False},
}
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "research", "mtf_cascade")
REPORT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "MTF_CASCADE_RR_SL_COMPARISON_REPORT.md")


def _session_date(ts: str) -> str:
    return ts[:10]


def _find_entries(bars: list[dict], cfg: MTFConfig) -> list[dict]:
    monthly_lookback = TIMEFRAME_LOOKBACK_5M_BARS["1mo"]
    decision_start = monthly_lookback + 200
    if decision_start >= len(bars):
        return []
    decision_bars = bars[decision_start:]
    n = len(decision_bars)
    a, b = n // 3, 2 * n // 3
    splits = {"TRAIN": (0, a), "VALIDATION": (a, b), "OOS": (b, n)}

    entries = []
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
        try:
            sig = verify("NIFTY", window, as_of_ts=ts, session=_session_date(ts), mtf_cfg=cfg, send_telegram=False)
        except Exception:
            global_idx += 1
            continue
        if sig.decision in ("BUY_CE", "BUY_PE"):
            split_name = next(name for name, (lo, hi) in splits.items() if lo <= local_idx < hi)
            entries.append({"global_idx": global_idx, "ts": ts, "split": split_name,
                           "decision": sig.decision, "entry": sig.entry, "stop_loss": sig.stop_loss,
                           "is_zth": sig.is_zth_signal})
        global_idx += 1
    return entries


def _simulate_variant(entry_row: dict, all_bars: list[dict], *, target_r: float, use_sl: bool) -> dict:
    direction = "BULLISH" if entry_row["decision"] == "BUY_CE" else "BEARISH"
    sign = 1 if direction == "BULLISH" else -1
    entry = entry_row["entry"]
    r_unit = abs(entry - entry_row["stop_loss"])
    if r_unit <= 0:
        return {"exit_reason": "DEGENERATE_RISK", "r_achieved": 0.0, "resolved": False}
    target_price = entry + sign * target_r * r_unit
    sl_price = entry_row["stop_loss"]

    start = entry_row["global_idx"]
    for j in range(start + 1, min(start + 1 + FORWARD_MAX_BARS, len(all_bars))):
        bar = all_bars[j]
        hit_target = (bar["h"] >= target_price) if direction == "BULLISH" else (bar["l"] <= target_price)
        hit_sl = use_sl and ((bar["l"] <= sl_price) if direction == "BULLISH" else (bar["h"] >= sl_price))
        if hit_target and hit_sl:
            # ambiguous same-bar -- conservative: the adverse side resolves first
            return {"exit_reason": "STOPPED_OUT", "r_achieved": -1.0, "resolved": True}
        if hit_target:
            return {"exit_reason": "TARGET", "r_achieved": target_r, "resolved": True}
        if hit_sl:
            return {"exit_reason": "STOPPED_OUT", "r_achieved": -1.0, "resolved": True}

    last_bar = all_bars[min(start + FORWARD_MAX_BARS, len(all_bars) - 1)]
    r_open = sign * (last_bar["c"] - entry) / r_unit
    return {"exit_reason": "TIME_CUTOFF", "r_achieved": round(r_open, 3), "resolved": False}


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    if n == 0:
        return {"n": 0}
    wins = [r for r in rows if r["r_achieved"] > 0]
    losses = [r for r in rows if r["r_achieved"] <= 0]
    gross_win = sum(r["r_achieved"] for r in wins)
    gross_loss = -sum(r["r_achieved"] for r in losses)
    equity = peak = mdd = 0.0
    for r in rows:
        equity += r["r_achieved"]
        peak = max(peak, equity)
        mdd = min(mdd, equity - peak)
    return {
        "n": n, "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / n, 4),
        "avg_r": round(sum(r["r_achieved"] for r in rows) / n, 4),
        "total_r": round(sum(r["r_achieved"] for r in rows), 3),
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss else None,
        "max_drawdown_r": round(mdd, 3),
        "exit_reasons": {k: sum(1 for r in rows if r["exit_reason"] == k) for k in
                        set(r["exit_reason"] for r in rows)},
    }


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Loading real Kaggle NIFTY 5m bars (most recent {BARS_LIMIT_YEARS} years)...")
    bars = load_kaggle_nifty_bars(limit_years=BARS_LIMIT_YEARS)
    print(f"  {len(bars)} real bars, {bars[0]['t'][:10]}..{bars[-1]['t'][:10]}")

    cfg = MTFConfig()
    print("Finding real MTF cascade entries (daily cadence, no tuning)...")
    entries = _find_entries(bars, cfg)
    print(f"  {len(entries)} real entries found")

    all_results = {name: [] for name in VARIANTS}
    per_entry = []
    for e in entries:
        row = {"ts": e["ts"], "split": e["split"], "decision": e["decision"], "is_zth": e["is_zth"]}
        for name, params in VARIANTS.items():
            out = _simulate_variant(e, bars, target_r=params["target_r"], use_sl=params["use_sl"])
            all_results[name].append(out)
            row[name] = out
        per_entry.append(row)

    with open(os.path.join(OUT_DIR, "rr_sl_comparison_rows.json"), "w") as f:
        json.dump(per_entry, f, indent=2, default=str)

    _write_report(entries, all_results)


def _write_report(entries, all_results):
    lines = ["# MTF Cascade -- 1:2 vs 1:3 Target, With vs Without Stop-Loss", ""]
    lines.append("Real Kaggle NIFTY 5m data. All four variants are simulated on the SAME real entries "
                 "(identical timestamps/direction/entry/SL from one discovery pass) and the SAME "
                 "forward real bars -- only the target R-multiple and whether the stop-loss is honored "
                 "differ between variants, so this is a controlled, apples-to-apples comparison.")
    lines.append("")
    lines.append(f"- Total real entries found: {len(entries)}")
    if len(entries) < 30:
        lines.append(f"- **This is a very small sample ({len(entries)} entries) -- treat every number "
                     f"below as illustrative of the MECHANISM, not a reliable performance estimate. "
                     f"See the mechanism-only caveat in MTF_CASCADE_BACKTEST_REPORT.md for why the "
                     f"discovery cadence is this sparse.**")
    lines.append("")
    lines.append("## Comparison")
    lines.append("")
    lines.append("| Variant | n | Win rate | Avg R | Total R | Profit factor | Max DD (R) | Exit reasons |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name in VARIANTS:
        s = _stats(all_results[name])
        if s["n"] == 0:
            lines.append(f"| {name} | 0 | -- | -- | -- | -- | -- | -- |")
            continue
        lines.append(f"| {name} | {s['n']} | {s['win_rate']:.2%} | {s['avg_r']} | {s['total_r']} | "
                     f"{s['profit_factor']} | {s['max_drawdown_r']} | {s['exit_reasons']} |")
    lines.append("")
    lines.append("## What this isolates")
    lines.append("")
    lines.append("- **1:2 vs 1:3**: a smaller target hits more often but caps the payoff per winner; a "
                 "larger target hits less often but pays more when it does. Compare win_rate and avg_r "
                 "between the with-SL rows at each target to see which trade-off actually paid off on "
                 "these specific entries.")
    lines.append("- **With SL vs without SL**: removing the stop only changes the outcome for trades "
                 "that would have been stopped out -- those either recover to the target (SL was "
                 "'wrong', without-SL wins), keep going against the position (without-SL bleeds further "
                 "until the time cutoff, usually worse), or churn sideways (little difference). The "
                 "'without SL' avg-R and max-drawdown rows show which of those actually happened here.")
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append("- \"Without stop-loss\" still exits at a hard time cutoff "
                 f"({FORWARD_MAX_BARS} bars) if neither the target nor an early reversal closes the "
                 "trade first -- marked to the real last close, never left open indefinitely or treated "
                 "as zero risk.")
    lines.append("- Same-bar target+SL ambiguity (both levels touched within one real bar) resolves "
                 "conservatively to STOPPED_OUT, the same convention used throughout this codebase's "
                 "other backtests.")
    lines.append("- No threshold, weight, or entry criterion was changed from the approved MTF cascade "
                 "to produce these entries -- only the EXIT rule varies between the four variants.")
    lines.append("")
    lines.append("Raw per-entry, per-variant dump: `data/research/mtf_cascade/rr_sl_comparison_rows.json`")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")


if __name__ == "__main__":
    main()
