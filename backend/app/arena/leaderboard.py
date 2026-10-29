"""Leaderboard rows from settled results: rank by NET profit after costs (identical settlement for everyone, the AI included). Pure function; shows totals only, never signals."""
from __future__ import annotations

from datetime import datetime

from . import settle as S

from .markets import IST  # noqa: E402
DEFAULT_DISPLAY = {"ZEROHERO_AI": "ZeroHero AI", "ZEROHERO_AI_EVENT": "ZeroHero AI - NG event rule (separate shadow line)", "INSTITUTION_TRADER": "Mohit Agrawal", "NEERAJ_JOSHI": "Neeraj Joshi", "DHANANJAY_DALVI": "Dhananjay Dalvi"}


def _day(r: dict) -> str:
    return datetime.fromtimestamp(r["t"], tz=IST).strftime("%Y-%m-%d")


def build(results_by_participant: dict, names: list[str], display: dict, *, scope: str, today: str, capital: float = 50000.0, top: int = 10) -> dict:
    rows = []
    for nm in names:
        rs = results_by_participant.get(nm, [])
        if scope == "today": rs = [r for r in rs if _day(r) == today]
        sm = S.summarize(rs, capital); settled = sm.get("settled", 0)
        rows.append({"participant": nm, "display": display.get(nm) or DEFAULT_DISPLAY.get(nm) or nm, "signals": sm.get("signals", 0), "settled": settled, "net_rs": round(sm["net_rs"], 2) if settled else None, "net_per_lot": round(sm["net_rs_1lot"], 2) if settled else None, "lots_traded": sm.get("lots_traded", 0) if settled else None,
                     "return_pct": round(sm["return_pct"], 2) if settled else None, "win_rate": round(sm["win_rate"], 3) if settled else None, "not_filled": sm.get("by_status", {}).get("NOT_TRIGGERED", 0)})
    ranked = sorted([r for r in rows if r["settled"]], key=lambda r: (-r["net_per_lot"], r["display"]))                       # the RANKING is the per-lot (1-lot equivalent) net: the quantity a participant chooses never decides the rank
    for i, r in enumerate(ranked, 1): r["rank"] = i
    rest = sorted([r for r in rows if not r["settled"]], key=lambda r: r["display"])
    for r in rest: r["rank"] = None
    return {"scope": scope, "day": today if scope == "today" else None, "capital": capital, "rows": (ranked + rest)[:top], "n_participants": len(rows),
            "note": "Ranked by net profit after costs PER LOT (every participant is compared as if they had traded 1 lot, so the quantity chosen does not decide the rank); the column Net (your lots) shows the actual rupees for the lots traded. Identical settlement rules (ask in / bid out, 20 s delay, session-end exit). Totals only. Paper trading, not a recommendation; a small sample proves nothing."}
