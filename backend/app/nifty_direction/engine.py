"""Contribution / breadth / sector analytics over validated quotes. Pure functions; no I/O. Refuses to score when the universe is not tradable."""
from __future__ import annotations

from collections import defaultdict


def contributions(master: dict, valid: dict) -> dict:
    """Index-point-weighted contribution proxy: weight% * pct_change. Sum ~ index % change (weights are static, so this is an approximation)."""
    rows = []
    for m in master["members"]:
        q = valid.get(m["symbol"])
        if q is None: continue
        chg = (q.ltp / q.prev_close - 1) * 100
        rows.append({"symbol": m["symbol"], "sector": m["sector"], "weight_pct": m["weight_pct"], "chg_pct": chg, "contrib": m["weight_pct"] * chg / 100})
    rows.sort(key=lambda r: -abs(r["contrib"]))
    tot = sum(r["contrib"] for r in rows)
    return {"rows": rows, "net": tot, "pos": sum(r["contrib"] for r in rows if r["contrib"] > 0), "neg": sum(r["contrib"] for r in rows if r["contrib"] < 0),
            "top10_share": (sum(abs(r["contrib"]) for r in rows[:10]) / (sum(abs(r["contrib"]) for r in rows) or 1))}


def breadth(rows: list[dict]) -> dict:
    n = len(rows) or 1; adv = sum(r["chg_pct"] > 0 for r in rows); dec = sum(r["chg_pct"] < 0 for r in rows)
    w = sum(r["weight_pct"] for r in rows) or 1
    return {"advancers": adv, "decliners": dec, "adv_ratio": adv / n, "weighted_adv": sum(r["weight_pct"] for r in rows if r["chg_pct"] > 0) / w}


def sectors(rows: list[dict]) -> list[dict]:
    g = defaultdict(lambda: {"weight_pct": 0.0, "contrib": 0.0, "n": 0})
    for r in rows: s = g[r["sector"]]; s["weight_pct"] += r["weight_pct"]; s["contrib"] += r["contrib"]; s["n"] += 1
    return sorted(({"sector": k, **v} for k, v in g.items()), key=lambda d: -abs(d["contrib"]))


def snapshot(master: dict, validation: dict) -> dict:
    """Fail closed: no analytics unless coverage + weights are valid."""
    if not validation["tradable"]:
        return {"status": "NO_SIGNAL", "reason": "UNIVERSE_NOT_TRADABLE", "weight_coverage": validation["weight_coverage"], "invalid": validation["invalid"]}
    c = contributions(master, validation["valid"])
    return {"status": "OK", "weight_coverage": validation["weight_coverage"], "invalid": validation["invalid"], "contribution": {k: c[k] for k in ("net", "pos", "neg", "top10_share")},
            "top": c["rows"][:10], "breadth": breadth(c["rows"]), "sectors": sectors(c["rows"])}
