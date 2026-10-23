from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timezone

import pandas as pd

from .config import VERSION


def _git_head() -> str | None:
    try: return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, timeout=5).decode().strip()
    except Exception: return None


def run_metadata(input_path: str, sp, bp, chart_tf: str, extra: dict | None = None) -> dict:
    return {"engine": VERSION, "created_utc": datetime.now(timezone.utc).isoformat(), "input": input_path, "chart_tf": chart_tf, "signal_params": sp.to_dict(), "backtest_params": bp.to_dict(),
            "python": platform.python_version(), "git_head": _git_head(), **(extra or {})}


def write_all(outdir: str, *, meta: dict, quality: dict, availability: dict, sig: pd.DataFrame, bt: dict) -> list[str]:
    os.makedirs(outdir, exist_ok=True); files = []

    def w(name, writer):
        p = os.path.join(outdir, name); writer(p); files.append(p)
    w("trades.csv", lambda p: bt["trades"].to_csv(p, index=False)); w("rejections.csv", lambda p: bt["rejections"].to_csv(p, index=False)); w("equity_curve.csv", lambda p: bt["equity"].to_csv(p, index=False))
    ev = sig[sig.buy_signal | sig.sell_signal | sig.blocked_buy | sig.blocked_sell]
    w("signals.csv", lambda p: ev[["buy_signal", "sell_signal", "blocked_buy", "blocked_sell", "mtf_dir", "up_votes", "dn_votes", "entry", "stop", "t1", "t2", "t3"]].to_csv(p))
    w("summary.json", lambda p: json.dump(bt["summary"], open(p, "w"), indent=1, default=str))
    w("data_quality.json", lambda p: json.dump(quality, open(p, "w"), indent=1, default=str))
    w("feature_availability.json", lambda p: json.dump(availability, open(p, "w"), indent=1, default=str))
    w("run_metadata.json", lambda p: json.dump(meta, open(p, "w"), indent=1, default=str))
    w("report.md", lambda p: open(p, "w").write(markdown(meta, quality, availability, bt)))
    return files


def markdown(meta, quality, availability, bt) -> str:
    s = bt["summary"]; en = availability["_enabled"]
    L = [f"# Smart U-Turn replay: chart {meta['chart_tf']}", "", f"engine `{meta['engine']}`  |  input `{meta['input']}`  |  {quality.get('first')} .. {quality.get('last')}", "",
         "## Result (research replay, NOT a profitability claim)", ""]
    if s.get("trades", 0) == 0: L.append(f"No trades. Signals: {s.get('signals')}, rejected: {s.get('rejected')} {s.get('rejection_reasons')}.")
    else:
        L += [f"- trades **{s['trades']}** (long {s['long_trades']} / short {s['short_trades']}), signals {s['signals']}, rejected {s['rejected']} {s['rejection_reasons']}",
              f"- net points **{s['net_points']:.1f}** (gross {s['gross_points']:.1f}); net P&L Rs {s['net_pnl_rs']:.0f}; win rate {s['win_rate']:.1%}; profit factor {s['profit_factor'] if s['profit_factor'] is None else round(s['profit_factor'], 2)}",
              f"- expectancy {s['expectancy_points']:.2f} points = {s['expectancy_R']:.3f} R (95% bootstrap CI {s['expectancy_R_ci95'][0]:.3f} .. {s['expectancy_R_ci95'][1]:.3f})",
              f"- avg win {s['avg_win_points']} / avg loss {s['avg_loss_points']} points; max drawdown Rs {s['max_drawdown_rs']:.0f} ({s['max_drawdown_pct']:.2f}%); max consecutive wins {s['max_consecutive_wins']} / losses {s['max_consecutive_losses']}",
              f"- exposure {s['exposure']:.1%}; average hold {s['avg_bars_held']:.1f} bars; long net {s['net_points_long']:.1f} / short net {s['net_points_short']:.1f} points", f"- verdict: **{s['verdict']}**"]
    L += ["", "## Assumptions", "", f"`{meta['backtest_params']}`", "", "Costs are a configured assumption (index points per round trip), not a measured cost. This replays the INDEX signal; it is not an option-premium result.", "",
          "## Data quality", "", "```", json.dumps({k: v for k, v in quality.items() if k != "sessions_partial"}, indent=1, default=str), "```", "",
          "## Features", "", f"enabled: {en}", "", "OI/IV/bid-ask analysis is enabled only where the source supplies the field; otherwise UNAVAILABLE (never zero-filled).", ""]
    return "\n".join(L)
