#!/usr/bin/env python3
"""Regenerate HCS_CALIBRATION_REPORT.md from the current resolved-outcome log.
READ-ONLY. Uses app/backtest/calibration.fit() -- trains nothing new."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.hcs import calibrate  # noqa: E402


def main():
    r = calibrate.report()
    if not r.get("available"):
        sys.exit(f"[STOP] {r.get('reason')}")
    g = r["global_curve"] or {}
    oos = r["oos_holdout"]
    cov = r["coverage"]
    L = ["# HCS calibration report", "",
         "Regenerate: `venv/bin/python scripts/hcs_calibrate.py` (or GET /api/hcs/calibration-report).",
         "Source: resolved AUTOSCALP `scalp_signals` outcomes; uses the EXISTING "
         "`app/backtest/calibration.fit()` + `reliability_curve` -- nothing new is trained.", "",
         f"**Verdict:** {r['verdict']}", "", "## Sample", "",
         f"- resolved WIN/LOSS: **{r['n_resolved']}**  (W {r['wins']} / L {r['losses']}, "
         f"base win-rate **{r['base_win_rate']}**)",
         f"- sessions: {len(r['sessions'])}  ({r['sessions'][0]} .. {r['sessions'][-1]})",
         f"- min rows for a fitted curve: {r['min_rows_for_a_curve']}", "",
         "## Global logistic curve  (p = sigmoid(k*(s-0.5) + b))", "",
         f"- k = {g.get('k')} , b = {g.get('b')} , n = {g.get('n')} , "
         f"fitted win-rate = {g.get('win_rate')} , spread buckets = {g.get('spread')}", "",
         "## Per-slice coverage", ""]
    for grp, title in (("per_regime", "Regime"), ("per_signal_type", "Signal type"),
                       ("per_tod", "Time-of-day")):
        L.append(f"**{title}**")
        for kk, vv in cov[grp].items():
            L.append(f"- `{kk}` : n={vv['n']} -> "
                     + ("FITS" if vv["fits"] else f"INSUFFICIENT (<{r['min_rows_for_a_curve']})"))
        L.append("")
    L.append("Fitted curves emitted: "
             + (", ".join(f"`{k}`" for k in r["fitted_curves"]) or "(only the global fallback)"))
    L += ["", "## Chronological hold-out reliability", "",
          f"- fit on first {oos['fit_on_first']} rows, scored last {oos['scored_last']}",
          f"- **Brier = {oos['brier']}** , **ECE = {oos['ece']}**  (lower is better; small sample -> noisy)",
          "", "| predicted-prob bin | n | predicted | actual |", "|---|--:|--:|--:|"]
    for b in (oos["reliability_bins"] or []):
        if b["n"]:
            L.append(f"| {b['bin'][0]:.1f}-{b['bin'][1]:.1f} | {b['n']} | {b['predicted']} | {b['actual']} |")
    L += ["", "## What this means", "",
          "- score->probability fits **globally** but there is **no per-regime / per-tod "
          "calibration** and **no walk-forward** (few sessions, one regime).",
          "- HCS runs **SHADOW**; its A+ gate is deliberately strict (HCS >= 68, calibrated "
          "p >= 0.56, confidence in {HIGH, MEDIUM}, zero hard vetoes).",
          "- Promote nothing until >= 40 resolved signals per regime across >= 2 regimes with a "
          "genuine chronological hold-out."]
    (ROOT / "HCS_CALIBRATION_REPORT.md").write_text("\n".join(L) + "\n")
    print(f"wrote {ROOT / 'HCS_CALIBRATION_REPORT.md'}  ({r['n_resolved']} resolved, {len(r['sessions'])} sessions)")
    print(f"verdict: {r['verdict']}")


if __name__ == "__main__":
    main()
