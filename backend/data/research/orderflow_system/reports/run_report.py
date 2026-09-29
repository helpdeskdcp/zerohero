"""Report generation -- JSON + human-readable, per-setup + per-RR
breakdown, breakeven PASS/FAIL table, walk-forward table, and a manifest
(full config + git commit hash + data version + seed) so any number can be
traced back to exactly what produced it (spec B9)."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from metrics.breakeven import breakeven_table
from metrics.performance import compute_performance, per_group, sub_period

DISCLAIMER = (
    "This system is for RESEARCH and EDUCATION only. Backtested results are historical and do NOT "
    "guarantee future performance. Order-flow data on NSE for retail traders is often delayed or "
    "proxy-based; OHLCV proxy mode produces APPROXIMATIONS, not true order flow. All performance "
    "figures are net of modelled costs but real slippage may differ. This is NOT investment advice. "
    "Consult a SEBI-registered advisor before trading real capital. Risk is the user's own."
)


def _git_commit_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[4],
                                       stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        return "UNKNOWN"


def build_manifest(cfg: dict, data_source, symbol: str, timeframe: str, start: str, end: str) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit_hash(),
        "config_version": cfg.get("version"),
        "data_mode": data_source.mode, "is_proxy": data_source.is_proxy,
        "symbol": symbol, "timeframe": timeframe, "date_range": [start, end],
        "seed": cfg["backtest"]["seed"],
    }


def build_report(trades: list[dict], cfg: dict, manifest: dict, walk_forward_folds: list[dict] | None,
                 warning_banner: str | None) -> dict:
    min_sample = cfg["backtest"]["min_sample_for_verdict"]
    by_setup = per_group(trades, "tag", min_sample)
    trades_by_setup: dict[str, list[dict]] = {}
    for t in trades:
        trades_by_setup.setdefault(t["tag"], []).append(t)

    rr_targets = sorted({rr for k in cfg if k.startswith("setup") and isinstance(cfg[k], dict) and "rr_targets" in cfg[k]
                         for rr in cfg[k]["rr_targets"]})
    breakeven = breakeven_table(trades_by_setup, rr_targets)

    return {
        "warning_banner": warning_banner,
        "manifest": manifest,
        "global": compute_performance(trades, min_sample),
        "by_setup": by_setup,
        "by_direction": per_group(trades, "direction", min_sample),
        "breakeven_table": breakeven,
        "sub_period_monthly": sub_period(trades, "M", min_sample),
        "walk_forward_folds": walk_forward_folds or [],
        "disclaimer": DISCLAIMER,
    }


def write_report(report: dict, out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "report.md").write_text(_render_markdown(report))


def _render_markdown(report: dict) -> str:
    lines = []
    if report.get("warning_banner"):
        lines += [f"> **{report['warning_banner']}**", ""]
    lines += ["# Backtest Report", "", "## Manifest", "```json",
             json.dumps(report["manifest"], indent=2), "```", ""]
    lines += ["## Global", "```json", json.dumps(report["global"], indent=2, default=str), "```", ""]
    lines += ["## Per-setup", "```json", json.dumps(report["by_setup"], indent=2, default=str), "```", ""]
    lines += ["## Break-even win-rate PASS/FAIL", "", "| Setup | RR | n | Actual WR | Pure BE WR | "
             "Cost-adj BE WR | Margin | Result |", "|---|---|---|---|---|---|---|---|"]
    for tag, by_rr in report["breakeven_table"].items():
        for rr, row in by_rr.items():
            if row.get("n", 0) == 0:
                lines.append(f"| {tag} | {rr} | 0 | - | - | - | - | INSUFFICIENT_SAMPLE |")
            else:
                lines.append(f"| {tag} | {rr} | {row['n']} | {row['actual_win_rate']:.1%} | "
                             f"{row['pure_breakeven_wr']:.1%} | {row['cost_adjusted_breakeven_wr']:.1%} | "
                             f"{row['margin']:+.1%} | **{row['result']}** |")
    lines.append("")
    if report["walk_forward_folds"]:
        lines += ["## Walk-forward folds", "", "| Train | Test | n | Expectancy (R) | Verdict |",
                 "|---|---|---|---|---|"]
        for f in report["walk_forward_folds"]:
            lines.append(f"| {f['train_start']}..{f['train_end']} | {f['test_start']}..{f['test_end']} | "
                        f"{f['n_trades']} | {f['expectancy_r']} | {f['verdict']} |")
        lines.append("")
    lines += ["## Disclaimer", "", report["disclaimer"]]
    return "\n".join(lines)
