"""
Phase 1/2 (index-first prediction-engine brief) -- driver script.

Re-walks the SAME real 2-year Kaggle NIFTY 5m dataset Stage 1 already used
(app.liquidity_sweep.backtest.load_kaggle_nifty_bars, default limit_years=2.0)
with the enriched, additive `_walk_raw_signals_with_features` (real resampled
HTF bars, ~30 named features per signal -- see backtest.py/feature_analysis.py
docstrings), then runs the formal WIN-vs-LOSS statistical discrimination
suite (Cohen's d / Mann-Whitney U for continuous features, Cramer's V /
chi-square for categorical ones, mutual information as diagnostic context)
and writes:
  - data/research/liquidity_sweep/phase1_signal_features.csv (raw per-signal
    feature dump, for independent audit/reproduction)
  - LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md (the Phase 1 report)

Does NOT touch, re-run, or alter the original Stage 1/Stage 2 backtest or
its report (LIQUIDITY_SWEEP_FINAL_REPORT.md) -- this is additive analysis
of the same underlying signal set, per the brief's own "do not throw away
the existing audit."
"""
from __future__ import annotations

import csv
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.liquidity_sweep import backtest, feature_analysis, model_check

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(BACKEND_DIR, "data", "research", "liquidity_sweep")
CSV_PATH = os.path.join(OUT_DIR, "phase1_signal_features.csv")
REPORT_PATH = os.path.join(BACKEND_DIR, "LIQUIDITY_SWEEP_PHASE1_FEATURE_ANALYSIS.md")


def _fmt(v):
    if v is None:
        return "n/a"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _render_feature_row(name: str, v: dict) -> str:
    win_m = v["win_summary"].get("mean") if v["win_summary"] and "mean" in v["win_summary"] else None
    loss_m = v["loss_summary"].get("mean") if v["loss_summary"] and "mean" in v["loss_summary"] else None
    win_s = _fmt(win_m) if win_m is not None else (str(v["win_summary"]) if v["win_summary"] else "n/a")
    loss_s = _fmt(loss_m) if loss_m is not None else (str(v["loss_summary"]) if v["loss_summary"] else "n/a")
    tags = v["category"]
    if v["redundant_with"]:
        tags += f" (+REDUNDANT w/ {','.join(sorted(set(v['redundant_with'])))})"
    return (f"| {name} | {v['kind']} | {v['n_available']} | {v['missing_rate']:.0%} | {win_s} | {loss_s} | "
            f"{v['effect_size_name'] or 'n/a'}={_fmt(v['effect_size'])} | {_fmt(v['p_value'])} | "
            f"{_fmt(v['mutual_information'])} | **{tags}** |")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print("Loading 2-year real Kaggle NIFTY 5m bars...")
    bars = backtest.load_kaggle_nifty_bars(limit_years=2.0)
    print(f"  {len(bars)} real bars loaded")

    t0 = time.time()
    print("Pass 1: locating raw signals (reusing tested _walk_raw_signals)...")
    samples = backtest._walk_raw_signals_with_features(bars)
    elapsed = time.time() - t0
    print(f"  {len(samples)} enriched signals captured in {elapsed:.1f}s")

    fieldnames = list(samples[0].to_dict().keys()) if samples else []
    with open(CSV_PATH, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for s in samples:
            writer.writerow(s.to_dict())
    print(f"  raw feature dump written: {CSV_PATH}")

    print("Running WIN-vs-LOSS statistical discrimination...")
    report = feature_analysis.analyze_features(samples)

    strong = sorted([n for n, v in report["features"].items() if v["category"] == "STRONG"])
    weak = sorted([n for n, v in report["features"].items() if v["category"] == "WEAK"])
    misleading = sorted([n for n, v in report["features"].items() if v["category"] == "MISLEADING"])
    unavailable = sorted([n for n, v in report["features"].items() if v["category"] == "UNAVAILABLE"])
    none_cat = sorted([n for n, v in report["features"].items() if v["category"] == "NONE"])

    lines = []
    lines.append("# Liquidity Sweep -- Phase 1 Per-Signal Feature Discrimination Analysis")
    lines.append("")
    lines.append("Index-First Prediction Engine mission, Phase 0 (architecture) + Phase 1 "
                 "(measure, don't assume) + part of Phase 2 (formal statistical tests). "
                 "Additive to, and does not replace, `LIQUIDITY_SWEEP_FINAL_REPORT.md` "
                 "(Stage 1/2 backtest, verdict NOT READY) -- same underlying signal set, "
                 "far deeper per-feature measurement.")
    lines.append("")
    lines.append("## Phase 0 -- what changed vs. what was reused")
    lines.append("")
    lines.append("- **Reused unchanged, tested**: `_walk_raw_signals` (signal-finding walk, cooldown "
                 "dedup, chronological split), all sweep/CHoCH/CISD/FVG/OrderBlock/risk/strikes "
                 "detection, `app.backtest.calibration` for probability fitting.")
    lines.append("- **New, additive**: `app/liquidity_sweep/resample.py` (real 5m->15m/30m/1h/4h/1d "
                 "up-aggregation), `backtest._build_bars_by_tf_at` + "
                 "`_walk_raw_signals_with_features` (2-pass: cheap signal-finding pass unchanged, "
                 "then real-HTF feature enrichment only at the ~2,000-ish found signal indices), "
                 "`app/liquidity_sweep/feature_analysis.py` (Cohen's d / Mann-Whitney U / Cramer's V "
                 "/ chi-square / mutual information + a fixed, documented STRONG/WEAK/REDUNDANT/"
                 "MISLEADING/UNAVAILABLE/NONE categorization rule).")
    lines.append("- **Bug found and fixed for this analysis (not yet in the live `engine.py` scoring "
                 "path)**: Stage 1's original walk called `engine.evaluate(bars_by_tf={\"5m\": ...})` "
                 "only -- `structure.htf_bias()` only recognises 15m/30m/1h/4h/1d, so EVERY one of "
                 "the 2,110 original signals silently got regime=\"RANGE\"/htf_score=0.0 (the "
                 "function's own documented fallback for \"no timeframe has enough history\"), not a "
                 "real measurement. This analysis supplies genuine resampled HTF bars so `regime` is "
                 "measured for the first time. This does NOT retroactively invalidate the original "
                 "NOT READY verdict (no NO_TRADE gate in `engine.py` depends on htf_bias -- the same "
                 "2,110-ish raw signals fire either way; only their setup_score/probability/regime "
                 "tags were previously meaningless).")
    lines.append("- **Also newly measured (previously dead/unwired)**: setup_score's 8th check, "
                 "`VOLUME_ABOVE_AVERAGE`, is never actually populated by `engine.py`'s real call to "
                 "`setup_score.compute` (it never passes `avg_volume`/`sweep_bar_volume`) -- this "
                 "analysis independently recomputes what that check's value WOULD be "
                 "(`chk_volume_above_average`), to measure whether it's worth wiring up.")
    lines.append("")
    lines.append("## Dataset")
    lines.append("")
    lines.append("- Real Kaggle NIFTY 5m OHLC, 2-year window (same as the original Stage 1 backtest)")
    lines.append(f"- Bars walked: {len(bars)}")
    lines.append(f"- Signals captured with full feature set: **{report['n_total_signals']}**")
    lines.append(f"- WIN: {report['n_win']} | LOSS: {report['n_loss']} | TIMEOUT (excluded from "
                 f"discrimination, per `label_outcome`'s own convention): {report['n_timeout']}")
    lines.append("")
    lines.append("## Category summary")
    lines.append("")
    lines.append(f"- **STRONG** ({len(strong)}): {', '.join(strong) or 'none'}")
    lines.append(f"- **WEAK** ({len(weak)}): {', '.join(weak) or 'none'}")
    lines.append(f"- **MISLEADING** ({len(misleading)}): {', '.join(misleading) or 'none'}")
    lines.append(f"- **UNAVAILABLE** ({len(unavailable)}): {', '.join(unavailable) or 'none'}")
    lines.append(f"- **NONE** ({len(none_cat)}): {', '.join(none_cat) or 'none'}")
    if report["redundancy_pairs"]:
        pair_strs = [f"{p['a']}~{p['b']} (r={p['pearson_r']})" for p in report["redundancy_pairs"]]
        lines.append(f"- **REDUNDANT pairs**: {'; '.join(pair_strs)}")
    else:
        lines.append("- **REDUNDANT pairs**: none found (|r| >= 0.8 threshold)")
    lines.append("")
    lines.append("## Per-feature results")
    lines.append("")
    lines.append("Categorization rule is fixed and documented in `feature_analysis.py`'s module "
                 "docstring, decided BEFORE running this script -- not tuned after seeing results.")
    lines.append("")
    lines.append("| Feature | Type | N available | Missing % | WIN mean | LOSS mean | Effect size | "
                 "p-value | Mutual info | Category |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for name in list(feature_analysis.CONTINUOUS_FEATURES) + list(feature_analysis.CATEGORICAL_FEATURES):
        lines.append(_render_feature_row(name, report["features"][name]))
    lines.append("")
    lines.append("## Headline finding")
    lines.append("")
    if not strong and not weak:
        lines.append("**No feature in this set -- individually -- shows a real (STRONG or WEAK), "
                     "non-misleading discriminative relationship between WIN and LOSS outcomes.** "
                     "This is consistent with, and deepens, the original Stage 1 finding (WIN/LOSS "
                     "setup-score means were statistically identical, 57.5 vs 57.5): it is not just "
                     "the combined 8-check score that fails to discriminate -- none of its individual "
                     "components, nor any of the additional momentum/volatility/volume/structure/HTF "
                     "features captured here, do either, on this real 2-year NIFTY 5m dataset with "
                     "this signal-detection logic. This does not prove no possible feature set could "
                     "ever work; it means this specific, fairly comprehensive set does not, honestly "
                     "measured rather than assumed.")
    else:
        lines.append(f"Feature(s) showing a real (STRONG or WEAK) discriminative relationship: "
                     f"{', '.join(strong + weak)}. See the per-feature table above for effect sizes, "
                     f"significance, and the exact WIN vs LOSS means before treating any of these as "
                     f"actionable -- multiple-comparisons risk applies with "
                     f"{len(report['features'])} features tested; a single feature crossing p<0.05 "
                     f"among ~{len(report['features'])} tests is expected by chance alone roughly "
                     f"{len(report['features']) * 0.05:.1f} times even under pure noise.")
    lines.append("")
    lines.append("## Multiple-comparisons caveat")
    lines.append("")
    lines.append(f"{len(report['features'])} features were tested independently at alpha=0.05/0.01. "
                 f"No Bonferroni/FDR correction was applied to the category thresholds above -- "
                 f"treat any single WEAK/STRONG result as a hypothesis to re-test on a fresh split, "
                 f"not a confirmed edge, especially if the number of STRONG+WEAK features found is "
                 f"small relative to what chance alone would produce.")
    lines.append("")
    lines.append(f"Raw per-signal feature CSV: `data/research/liquidity_sweep/phase1_signal_features.csv` "
                 f"({len(samples)} rows, {len(fieldnames)} columns) -- for independent re-analysis.")
    lines.append("")

    print("Running leakage-safe JOINT feature check (Phase 2 completion)...")
    mc = model_check.run_leakage_safe_model_check(samples)
    lines.append("## Phase 2 completion -- leakage-safe joint feature check")
    lines.append("")
    lines.append("Individual features can each look like noise while a REAL combination still "
                 "predicts the outcome (e.g. XOR of two features) -- univariate tests above cannot "
                 "see this. This runs two standard models (L2 logistic regression; a shallow random "
                 "forest, to also catch nonlinear interactions) on ALL features jointly, using the "
                 "same TRAIN -> VALIDATION -> OOS chronological split discipline as the original "
                 "Stage 1 backtest -- OOS touched exactly once.")
    lines.append("")
    if mc.get("status") != "OK":
        lines.append(f"**{mc.get('status')}** -- {mc}")
    else:
        lines.append(f"- Train/Val/OOS sizes: {mc['n_train']} / {mc['n_val']} / {mc['n_oos']} "
                     f"(base rates {mc['train_base_rate']:.1%} / {mc['val_base_rate']:.1%} / "
                     f"{mc['oos_base_rate']:.1%})")
        lines.append(f"- Logistic regression: VAL AUC {_fmt(mc['logistic_val_auc'])}, "
                     f"OOS AUC {_fmt(mc['logistic_oos_auc'])}, "
                     f"OOS accuracy {_fmt(mc['logistic_oos_accuracy'])}, "
                     f"OOS Brier {_fmt(mc['logistic_oos_brier'])}")
        lines.append(f"- Random forest: VAL AUC {_fmt(mc['forest_val_auc'])}, "
                     f"OOS AUC {_fmt(mc['forest_oos_auc'])}, "
                     f"OOS accuracy {_fmt(mc['forest_oos_accuracy'])}, "
                     f"OOS Brier {_fmt(mc['forest_oos_brier'])}")
        lines.append(f"- OOS majority-class baseline accuracy: {_fmt(mc['oos_majority_baseline_accuracy'])} "
                     f"(selected model: **{mc['selected_model']}**)")
        lines.append(f"- **Pass bar (OOS AUC >= 0.57)**: **{'PASSES' if mc['passes_bar'] else 'DOES NOT PASS'}** "
                     f"-- {mc['bar_rationale']}")
        lines.append("")
        lines.append("Top-10 random forest feature importances (diagnostic only -- not evidence of "
                     "edge on its own, since the model as a whole did not pass the bar):")
        lines.append("")
        for r in mc["top_forest_importances"]:
            lines.append(f"  - {r['feature']}: {r['importance']:.4f}")
    lines.append("")

    with open(REPORT_PATH, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {REPORT_PATH}")
    print(f"STRONG={len(strong)} WEAK={len(weak)} MISLEADING={len(misleading)} "
          f"UNAVAILABLE={len(unavailable)} NONE={len(none_cat)}")


if __name__ == "__main__":
    main()
