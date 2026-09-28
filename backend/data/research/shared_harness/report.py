"""Standardized report computation + VERDICT.md template shared across
data/research/*/ backtests -- a consistent shape across every strategy
tested, so a reader (or a future me) doesn't have to re-learn a new report
layout for each hypothesis."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .simulate import pnl_points

MIN_SAMPLE = 30


def compute_report(trades: list[dict], *, pnl_fn=pnl_points, group_by: str | None = "tag") -> dict:
    if not trades:
        return {"n": 0, "verdict": "INSUFFICIENT_SAMPLE"}
    pnl = np.array([pnl_fn(t) for t in trades])
    wins, losses = pnl[pnl > 0], pnl[pnl <= 0]
    pf = (wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
    out = {
        "n": len(pnl), "win_rate": round(len(wins) / len(pnl), 4),
        "profit_factor": round(pf, 3) if pf is not None else None,
        "avg_pnl": round(float(pnl.mean()), 3), "total_pnl": round(float(pnl.sum()), 1),
        "verdict": "OK" if len(pnl) >= MIN_SAMPLE else "INSUFFICIENT_SAMPLE",
    }
    if group_by:
        groups: dict = {}
        for t, p in zip(trades, pnl):
            groups.setdefault(t.get(group_by, "UNKNOWN"), []).append(p)
        out[f"by_{group_by}"] = {
            k: {"n": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v), 3),
                "avg_pnl": round(float(np.mean(v)), 2)}
            for k, v in groups.items()
        }
    return out


def write_verdict_md(path: str, *, title: str, rule_description: str, data_sources: list[str],
                     disclosed_choices: list[str], sections: list[tuple[str, dict | str]],
                     bottom_line: str) -> None:
    lines = [f"# {title}", "",
            f"**Generated: {datetime.now(timezone.utc).isoformat()}**", "",
            "## Rule tested", "", rule_description, "",
            "## Data sources (real, not fabricated)", ""]
    lines += [f"- {s}" for s in data_sources]
    lines += ["", "## Disclosed interpretation choices", ""]
    lines += [f"- {c}" for c in disclosed_choices]
    lines += [""]
    for heading, content in sections:
        lines += [f"## {heading}", ""]
        if isinstance(content, str):
            lines.append(content)
        else:
            lines.append("```json")
            lines.append(json.dumps(content, indent=2, default=str))
            lines.append("```")
        lines.append("")
    lines += ["## Bottom line", "", bottom_line, ""]
    Path(path).write_text("\n".join(lines))
