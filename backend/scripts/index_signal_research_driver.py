"""
Index-First Signal Research -- Phases 1-7 driver.

Runs the full pipeline (real data -> causal features -> Phase-4 label ->
all 8 hypothesis variants -> chronological TRAIN/VAL/OOS evaluation with a
leakage-safe, TRAIN-decided baseline and Bonferroni-corrected pass bar) on
the SAME real 2-year Kaggle NIFTY 5m window the frozen liquidity_sweep
baseline used (`limit_years=2.0`), so any result here is directly
comparable to that baseline's own numbers (Phase 19's requirement).

Per Phase 22 ("do not build elaborate machinery before finding an edge")
and this mission's explicit GIT/COMMIT RULE, this script does NOT proceed
to walk-forward/ablation/regime/CE-PE-mapping (Phases 8-17) unless at least
one hypothesis clears `passes_bar` here -- it reports the Phase 1-7 result
and stops, honestly, if nothing does.

NIFTY only (not BANKNIFTY): NIFTY's Kaggle table is confirmed clean (no
duplicate rows, see app/index_signal_research/data.py's own audit note);
using the same instrument as the frozen baseline keeps this an apples-to-
apples comparison. BANKNIFTY (after the documented DISTINCT dedup fix) is
available via the same `data.load_index_5m("BANKNIFTY")` call for a future
robustness extension, not run here to keep this driver's scope matched to
what Phase 19 actually requires (compare against the SAME instrument the
baseline was tested on).
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.index_signal_research import data, evaluate, features, hypotheses, labels  # noqa: E402

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BACKEND_DIR, "data", "research", "index_signal_research")
REPORT_PATH = os.path.join(BACKEND_DIR, "INDEX_SIGNAL_RESEARCH_PHASE1_7_REPORT.md")
RESULTS_JSON_PATH = os.path.join(OUT_DIR, "phase1_7_hypothesis_results.json")


def _fmt(v):
    return "n/a" if v is None else (f"{v:.4f}" if isinstance(v, float) else str(v))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    t0 = time.time()
    print("Loading real 2-year Kaggle NIFTY 5m bars (same window as the frozen liquidity_sweep baseline)...")
    df = data.load_index_5m("NIFTY", limit_years=2.0)
    print(f"  {len(df)} real bars loaded ({df['t'].iloc[0]} .. {df['t'].iloc[-1]})")

    print("Building causal features (price/time/HTF-bias)...")
    feat = features.build_feature_frame(df)
    print(f"  {feat.shape[1]} columns")

    print("Computing Phase-4 outcome labels (registered before any hypothesis run)...")
    label = labels.compute_labels(feat)
    graded = label[label.notna()]
    print(f"  labels: UP={int((graded == 'UP').sum())} DOWN={int((graded == 'DOWN').sum())} "
          f"RANGE={int((graded == 'RANGE').sum())} (of {len(graded)} graded bars)")

    results = {}
    for name, fn in hypotheses.HYPOTHESES.items():
        print(f"Evaluating hypothesis {name}...")
        signal = fn(feat)
        results[name] = evaluate.evaluate_hypothesis(feat, label, signal, name)

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    with open(RESULTS_JSON_PATH, "w") as f:
        json.dump(results, f, indent=2, default=str)

    passing = [n for n, r in results.items() if r["passes_bar"]]

    lines = []
    lines.append("# Index-First Signal Research -- Phase 1-7 Report")
    lines.append("")
    lines.append("Pivot mission, following the frozen `liquidity_sweep` NOT READY verdict "
                 "(see `LIQUIDITY_SWEEP_FINAL_REPORT.md` / `LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md`, "
                 "not modified or re-tuned by anything here). Tests 8 distinct, independently "
                 "falsifiable index-direction hypotheses on the SAME real 2-year Kaggle NIFTY 5m "
                 "window the baseline used, with the SAME chronological TRAIN/VALIDATION/OOS "
                 "discipline, a leakage-safe baseline decided on TRAIN only, and a Bonferroni-"
                 f"corrected pass bar (alpha={evaluate.BONFERRONI_ALPHA:.5f} across "
                 f"{evaluate.N_HYPOTHESES_TESTED} hypothesis variants).")
    lines.append("")
    lines.append("## Phase 1-2 -- data audit")
    lines.append("")
    lines.append("- NIFTY INDEX 5m: 2015-01-09 .. 2026-05-18, 209,888 real rows, no duplicate "
                 "timestamps (confirmed by direct query).")
    lines.append("- BANKNIFTY INDEX 5m: same date range, but EVERY row is stored TWICE in the "
                 "source table (confirmed by direct query) -- `data.load_index_5m` fixes this with "
                 "`SELECT DISTINCT`; not used in this run to keep an apples-to-apples comparison "
                 "against the baseline's own NIFTY-only test.")
    lines.append("- Volume: 100% NULL/0 for NIFTY/BANKNIFTY 5m (confirmed) -- no volume-derived "
                 "feature (real VWAP, volume expansion) was computed; not fabricated, not attempted.")
    lines.append("- market_history.db (real 10-day option-chain capture) reserved for Phase 15/16 "
                 "CE/PE mapping validation only, never for these long-term index-hypothesis "
                 "statistics -- 10 days is not a usable chronological-split sample size.")
    lines.append("")
    lines.append(f"- Bars walked: {len(df)}")
    lines.append(f"- Graded bars (complete forward horizon): {len(graded)} "
                 f"(UP {int((graded=='UP').sum())}, DOWN {int((graded=='DOWN').sum())}, "
                 f"RANGE {int((graded=='RANGE').sum())})")
    lines.append(f"- Label definition: horizon={labels.HORIZON_BARS} bars, "
                 f"threshold={labels.ATR_THRESHOLD_MULT}x ATR -- IDENTICAL to the frozen "
                 f"liquidity_sweep baseline's own label definition (not re-picked for this mission).")
    lines.append("")
    lines.append("## Phase 6/7 -- per-hypothesis results (OOS is the number that matters)")
    lines.append("")
    lines.append("| Hypothesis | TRAIN n / acc (vs base) | VAL n / acc (vs base) | OOS n / acc | "
                 "OOS baseline | OOS p (vs base) | OOS p (vs 0.5) | Passes bar? |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for name, r in results.items():
        tr, va, oo = r["splits"]["train"], r["splits"]["validation"], r["splits"]["oos"]
        lines.append(f"| {name} | {tr['n_signals']} / {_fmt(tr['accuracy'])} (base {_fmt(tr['baseline_accuracy'])}) | "
                     f"{va['n_signals']} / {_fmt(va['accuracy'])} (p={_fmt(va['p_value'])}) | "
                     f"{oo['n_signals']} / {_fmt(oo['accuracy'])} | {_fmt(oo['baseline_accuracy'])} | "
                     f"{_fmt(oo['p_value'])} | {_fmt(oo['p_value_vs_half'])} | "
                     f"{'**YES**' if r['passes_bar'] else 'no'} |")
    lines.append("")
    lines.append("`passes_bar` requires ALL of: OOS accuracy beats both its TRAIN-decided baseline "
                 "AND plain 50/50 (the second check exists because a TRAIN-decided single-direction "
                 "baseline can drift out of sync with a later split's true UP/DOWN base rate, making a "
                 "balanced-mix signal look like it 'beats baseline' from that drift alone, while still "
                 "being indistinguishable from a coin flip in absolute terms -- found and fixed during "
                 "this mission's own review, see evaluate.py), Bonferroni-significant against both, "
                 "n>=20, TRAIN accuracy >= TRAIN baseline (not merely OOS), and VALIDATION shows at "
                 "least a weak (p<0.10) same-direction effect -- a hypothesis that only 'works' on the "
                 "one untouched split is not stable, per Phase 20's own acceptance criteria.")
    lines.append("")

    if not passing:
        lines.append("## Verdict")
        lines.append("")
        lines.append("**NO ROBUST EDGE FOUND.** None of the 8 tested hypothesis variants "
                     "(trend continuation, breakout, mean reversion x2, pullback-in-trend, "
                     "opening range, volatility-expansion, multi-timeframe structure) beats its own "
                     "TRAIN-decided baseline on held-out OOS data at the Bonferroni-corrected "
                     f"significance level (alpha={evaluate.BONFERRONI_ALPHA:.5f}). This is "
                     "consistent with, not contradicted by, the frozen liquidity_sweep baseline's "
                     "own null result -- across two structurally different families of hypotheses "
                     "(SMC/liquidity-sweep pattern detection, and now these 8 classical technical-"
                     "structure hypotheses), on the same real 2-year NIFTY 5m dataset, none shows a "
                     "real directional edge.")
        lines.append("")
        lines.append("Per Phase 22 (\"do not build elaborate machinery before finding an edge\") and "
                     "the mission's own instruction not to force a positive conclusion, this driver "
                     "STOPS here -- Phases 8-20 (walk-forward, probability calibration, confidence, "
                     "no-trade engine, ablation, regime analysis, CE/PE mapping, robustness) are not "
                     "run, because there is no candidate to carry into them.")
        lines.append("")
        lines.append("## What would actually be required next")
        lines.append("")
        lines.append("1. **A fundamentally different data source**: real intraday volume/OI/tick "
                     "data for NIFTY/BANKNIFTY (completely absent from the Kaggle dataset) would open "
                     "up an entire feature family (order-flow imbalance, volume-confirmed breakouts, "
                     "VWAP mean reversion) that is structurally untestable here.")
        lines.append("2. **A different instrument or timeframe class** -- these 8 hypotheses were "
                     "tested on 5m bars only (the finest granularity Kaggle provides); a genuinely "
                     "different holding-period hypothesis (multi-day swing, not intraday) was "
                     "already tested and rejected separately (see the project's own "
                     "`trend-swing-nogo` research).")
        lines.append("3. **A fundamentally different signal class** not yet tried in this codebase: "
                     "cross-asset/cross-index lead-lag relationships, options-market-implied "
                     "positioning (once more than 10 real days of option-chain history exist), or "
                     "macro/event-driven conditioning -- none of which this dataset can currently "
                     "support.")
    else:
        lines.append("## Verdict")
        lines.append("")
        lines.append(f"**{len(passing)} hypothesis variant(s) clear the pass bar**: {', '.join(passing)}. "
                     "Per Phase 22, deeper work (walk-forward, calibration, ablation, regime analysis, "
                     "CE/PE mapping) should now proceed ONLY for the single strongest of these -- see "
                     "the per-hypothesis detail below before treating this as a production candidate; "
                     "multiple-hypothesis risk means a re-test on a fresh period is still warranted "
                     "before this is called PROMISING per Phase 20's own criteria.")
        for name in passing:
            r = results[name]
            lines.append("")
            lines.append(f"### {name}")
            lines.append(f"- OOS: {json.dumps(r['splits']['oos'])}")
            lines.append(f"- Per-year OOS stability: {json.dumps(r['oos_per_year'])}")
            lines.append(f"- Per-time-bucket OOS stability: {json.dumps(r['oos_per_time_bucket'])}")

    lines.append("")
    lines.append("## Full per-hypothesis detail (train/val/oos + per-year + per-time-bucket)")
    lines.append("")
    lines.append("Raw JSON: `data/research/index_signal_research/phase1_7_hypothesis_results.json`")
    lines.append("")
    for name, r in results.items():
        lines.append(f"### {name}")
        lines.append(f"- {r['bar_rationale']}")
        lines.append(f"- Per-year OOS: {json.dumps(r['oos_per_year'])}")
        lines.append(f"- Per-time-bucket OOS: {json.dumps(r['oos_per_time_bucket'])}")
        lines.append("")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")
    print(f"Hypotheses passing the bar: {passing or 'NONE'}")


if __name__ == "__main__":
    main()
