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


COUNT_COVERAGE_MIN = 0.90          # share of the 50 members that must have a valid quote for the weight-free metrics


def unweighted(master: dict, valid: dict) -> dict:
    """Weight-free breadth and sector strength from valid quotes only (no weight is read or assumed). Unchanged = 0.00 % after rounding to 2 dp."""
    rows = []
    for m in master["members"]:
        q = valid.get(m["symbol"])
        if q is not None: rows.append({"symbol": m["symbol"], "sector": m["sector"], "chg_pct": (q.ltp / q.prev_close - 1) * 100})
    n_all = len(master["members"]) or 1; cov = len(rows) / n_all
    if cov < COUNT_COVERAGE_MIN: return {"status": "UNAVAILABLE", "reason": "INSUFFICIENT_QUOTES", "count_coverage": round(cov, 4), "valid": len(rows)}
    adv = sum(round(r["chg_pct"], 2) > 0 for r in rows); dec = sum(round(r["chg_pct"], 2) < 0 for r in rows); unch = len(rows) - adv - dec
    g = defaultdict(list)
    for r in rows: g[r["sector"]].append(r["chg_pct"])
    secs = []
    for k, v in g.items():
        a = sum(round(x, 2) > 0 for x in v); d = sum(round(x, 2) < 0 for x in v); sv = sorted(v); mid = len(sv) // 2
        med = sv[mid] if len(sv) % 2 else (sv[mid - 1] + sv[mid]) / 2
        secs.append({"sector": k, "n": len(v), "advancers": a, "decliners": d, "unchanged": len(v) - a - d, "median_chg_pct": med, "strength": (a - d) / len(v)})
    secs.sort(key=lambda x: -abs(x["strength"]))
    return {"status": "OK", "basis": "UNWEIGHTED", "count_coverage": round(cov, 4), "valid": len(rows), "advancers": adv, "decliners": dec, "unchanged": unch,
            "adv_dec_ratio": (adv - dec) / len(rows), "sectors": secs}


def snapshot(master: dict, validation: dict) -> dict:
    """Weighted analytics fail closed (verified weights + coverage). The unweighted block is independent of weights and is attached whenever the quotes allow it."""
    uw = unweighted(master, validation["valid"])
    if not validation["tradable"]:
        return {"status": "NO_SIGNAL", "reason": "UNIVERSE_NOT_TRADABLE", "weight_coverage": validation["weight_coverage"], "invalid": validation["invalid"], "unweighted": uw}
    c = contributions(master, validation["valid"])
    return {"status": "OK", "weight_coverage": validation["weight_coverage"], "invalid": validation["invalid"], "contribution": {k: c[k] for k in ("net", "pos", "neg", "top10_share")},
            "top": c["rows"][:10], "breadth": breadth(c["rows"]), "sectors": sectors(c["rows"]), "unweighted": uw}
