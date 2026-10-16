"""Explainable confluence scoring -- PAPER / SIGNAL-ONLY, pure functions, NOT wired to any producer, no orders.

Every threshold comes from strategy_policy.json (all marked INITIAL/uncalibrated). The score is a count of confirmed evidence, NOT a
probability of winning. Correlated evidence is capped per group, opposing evidence forces WAIT, mandatory data/structure gates fail closed
to NO_TRADE / WAIT with machine-readable reasons. Adapters turn already-computed, already-confirmed (closed-candle) features into Evidence;
they never look at future bars."""
from __future__ import annotations

from dataclasses import dataclass, field

BULL, BEAR = "BULLISH", "BEARISH"
GENUINE, INFERRED, PROXY, UNAVAILABLE = "GENUINE", "INFERRED", "PROXY", "UNAVAILABLE"


@dataclass
class Evidence:
    component: str
    direction: str | None            # BULLISH / BEARISH / None (direction-neutral support, e.g. relative volume)
    points: float
    data_class: str = UNAVAILABLE
    reasons: list = field(default_factory=list)
    detail: dict = field(default_factory=dict)


def _none(component, reason, data_class=UNAVAILABLE, **detail):
    return Evidence(component, None, 0.0, data_class, [reason], detail)


# ------------------------------------------------------------------ adapters (closed-candle inputs only)
def structure_evidence(events: list[dict], now_bar: int, policy: dict) -> Evidence:
    """events: [{"type": "BOS"|"CHOCH", "direction", "bar_index" (confirmation candle), "close_confirmed": bool}]. A wick beyond the level is NOT structure."""
    r = policy["scoring"]["evidence_rules"]["structure"]
    best = None
    wick_only = False
    for e in events:
        if not e.get("close_confirmed"):
            wick_only = True
            continue
        if now_bar - e["bar_index"] > r["max_age_bars"] or e["bar_index"] > now_bar:
            continue
        if best is None or e["bar_index"] > best["bar_index"]:
            best = e
    if best is None:
        return _none("structure", "WICK_ONLY_NOT_STRUCTURE" if wick_only else "NO_CONFIRMED_STRUCTURE", PROXY)
    pts = float(r["BOS"] if best["type"] == "BOS" else r["CHOCH"])
    return Evidence("structure", best["direction"], pts, PROXY, [], {"type": best["type"], "bar_index": best["bar_index"]})


def sweep_evidence(sweep: dict | None, now_bar: int, policy: dict) -> Evidence:
    """sweep = liquidity_sweep.detect_sweep().to_dict() (already reclaimed inside the supplied bars). A wick without reclaim is not a setup."""
    r = policy["scoring"]["evidence_rules"]["liquidity_sweep"]
    if not sweep:
        return _none("liquidity_sweep", "NO_CONFIRMED_SWEEP", PROXY)
    if now_bar - sweep["reclaim_bar_index"] > r["max_age_bars"] or sweep["reclaim_bar_index"] > now_bar:
        return _none("liquidity_sweep", "SWEEP_TOO_OLD", PROXY)
    direction = BEAR if sweep["kind"] == "UPPER_SWEEP" else BULL           # liquidity above taken then reclaimed => bearish
    pts = float(r["full_points_fast"] if sweep["bars_to_reclaim"] <= r["fast_reclaim_bars"] else r["points_slow"])
    return Evidence("liquidity_sweep", direction, pts, PROXY, [], {"kind": sweep["kind"], "bars_to_reclaim": sweep["bars_to_reclaim"]})


def delta_cvd_evidence(delta_pct: float | None, cvd_slope: float | None, classified_fraction: float | None, data_class: str, policy: dict) -> Evidence:
    """data_class must be INFERRED (quote-rule). OHLC proxies are forbidden as delta; too little classified volume => no evidence."""
    f, r = policy["features"]["delta"], policy["scoring"]["evidence_rules"]["delta_cvd"]
    if data_class in f["forbidden_classes"]:
        return _none("delta_cvd", "FORBIDDEN_PROXY_AS_DELTA", PROXY)
    if data_class != INFERRED or delta_pct is None or classified_fraction is None:
        return _none("delta_cvd", "DATA_MISSING")
    if classified_fraction < f["min_classified_fraction"]:
        return _none("delta_cvd", "LOW_CLASSIFIED_FRACTION", INFERRED, classified_fraction=classified_fraction)
    a = abs(delta_pct)
    if a < r["delta_pct_1pt"]:
        return Evidence("delta_cvd", None, 0.0, INFERRED, ["DELTA_BELOW_THRESHOLD"], {"delta_pct": delta_pct})
    direction = BULL if delta_pct > 0 else BEAR
    agrees = cvd_slope is not None and (cvd_slope > 0) == (delta_pct > 0) and cvd_slope != 0
    pts = 2.0 if (a >= r["delta_pct_2pt"] and (agrees or not r["cvd_must_agree_for_2pt"])) else 1.0
    return Evidence("delta_cvd", direction, pts, INFERRED, [] if agrees else ["CVD_NOT_CONFIRMING"], {"delta_pct": delta_pct, "cvd_agrees": agrees})


def imbalance_evidence(stacked_runs: list[dict] | None, policy: dict) -> Evidence:
    """stacked_runs = FootprintBar.stacked_imbalances(). Absorption is REQUIRES_DATA and is never inferred here."""
    r = policy["scoring"]["evidence_rules"]["imbalance_absorption"]
    if not stacked_runs:
        return _none("imbalance_absorption", "NO_STACKED_IMBALANCE", INFERRED)
    verified = [x for x in stacked_runs if x.get("adjacency_verified")]
    if r["require_adjacency_verified"] and not verified:
        return _none("imbalance_absorption", "ADJACENCY_UNVERIFIED", INFERRED)
    run = max(verified, key=lambda x: x["levels"])
    pts = 2.0 if run["levels"] >= r["stacked_run_levels_for_2pt"] else float(r["stacked_run_points"])
    return Evidence("imbalance_absorption", BULL if run["direction"] == "BUY" else BEAR, pts, INFERRED, [], {"levels": run["levels"]})


def vwap_evidence(price: float | None, vwap: float | None, trend_dir: str | None, policy: dict) -> Evidence:
    if vwap is None or price is None:
        return _none("vwap_regime", "VWAP_UNAVAILABLE")             # indices have volume 0 => no VWAP; never faked
    side = BULL if price > vwap else BEAR if price < vwap else None
    if side is None or trend_dir != side:
        return Evidence("vwap_regime", None, 0.0, PROXY, ["VWAP_REGIME_NOT_ALIGNED"], {})
    return Evidence("vwap_regime", side, float(policy["scoring"]["evidence_rules"]["vwap_regime"]["points"]), PROXY, [], {})


def rvol_evidence(rvol: float | None, policy: dict) -> Evidence:
    r = policy["scoring"]["evidence_rules"]["relative_volume"]
    if rvol is None:
        return _none("relative_volume", "RVOL_UNAVAILABLE")
    if rvol < r["min_rvol"]:
        return Evidence("relative_volume", None, 0.0, PROXY, ["RVOL_BELOW_MIN"], {"rvol": rvol})
    return Evidence("relative_volume", None, float(r["points"]), PROXY, [], {"rvol": rvol})


def htf_evidence(htf_dir: str | None, htf_bar_completed: bool, policy: dict) -> Evidence:
    if htf_dir is None:
        return _none("htf_alignment", "HTF_UNAVAILABLE")
    if not htf_bar_completed:
        return _none("htf_alignment", "HTF_BAR_NOT_COMPLETED")        # only completed higher-timeframe candles
    return Evidence("htf_alignment", htf_dir, float(policy["scoring"]["evidence_rules"]["htf_alignment"]["points"]), PROXY, [], {})


# ------------------------------------------------------------------ core
def _session_ok(market: str, hhmm: str, policy: dict) -> bool:
    lo, hi = policy["risk"]["sessions_ist"][market]
    return lo <= hhmm <= hi


def score(evidence: list[Evidence], ctx: dict, policy: dict) -> dict:
    """ctx: symbol, now_hhmm_ist ("HH:MM"), kill_switch (bool), data_stale (bool), last_signal ({"direction","age_sec"}|None).
    Returns {"status": SIGNAL|WAIT|NO_TRADE, "direction", "total", "reasons", "components", "groups", ...}. Never raises on missing data: missing => NO_TRADE."""
    sc, mk = policy["scoring"], policy["markets"]
    out = {"status": "NO_TRADE", "direction": None, "total": 0.0, "reasons": [], "is_probability": False,
           "policy_version": policy["policy_version"], "validation_status": policy["validation_status"], "components": [], "groups": {}}
    sym = str(ctx.get("symbol", "")).upper()
    market = "MCX" if sym in mk["mcx"] else "NSE_BSE" if sym in mk["index_derivatives"] else None
    gates = []
    if ctx.get("kill_switch") is not False:
        gates.append("KILL_SWITCH")                                      # unknown kill-switch state fails closed
    if market is None:
        gates.append("UNSUPPORTED_INSTRUMENT")
    if ctx.get("data_stale") is not False:
        gates.append("DATA_STALE")                                       # unknown freshness fails closed
    if market and not _session_ok(market, str(ctx.get("now_hhmm_ist", "")), policy):
        gates.append("OUTSIDE_SESSION")
    if gates:
        out["reasons"] = gates
        return out

    by = {e.component: e for e in evidence}
    cmax = sc["components_max"]
    comps = {}
    for k in cmax:
        e = by.get(k) or _none(k, "NOT_PROVIDED")
        comps[k] = Evidence(k, e.direction, max(0.0, min(float(e.points), float(cmax[k]))), e.data_class, list(e.reasons), e.detail)
    totals = {BULL: 0.0, BEAR: 0.0}
    for g, members in sc["groups"].items():
        for d in (BULL, BEAR):
            raw = sum(comps[m].points for m in members if comps[m].direction == d)
            totals[d] += min(raw, sc["group_caps"].get(g, raw))
    lead = BULL if totals[BULL] > totals[BEAR] else BEAR if totals[BEAR] > totals[BULL] else None
    neutral = sum(c.points for c in comps.values() if c.direction is None and c.points > 0)
    out["components"] = [{"component": c.component, "direction": c.direction, "points": c.points, "data_class": c.data_class, "reasons": c.reasons, **c.detail} for c in comps.values()]
    if lead is None:
        out["status"], out["reasons"] = "WAIT", ["NO_EVIDENCE" if totals[BULL] == 0 else "CONFLICTING_EVIDENCE"]
        return out
    opp = BEAR if lead == BULL else BULL
    opposing = [c.component for c in comps.values() if c.direction == opp and c.points >= 1]
    # direction-neutral support (relative volume) counts for the lead direction, capped by its group cap
    groups = {}
    total = 0.0
    for g, members in sc["groups"].items():
        raw = sum(comps[m].points for m in members if comps[m].direction == lead) + sum(comps[m].points for m in members if comps[m].direction is None)
        counted = min(raw, sc["group_caps"].get(g, raw))
        groups[g] = {"raw": raw, "counted": counted, "cap": sc["group_caps"].get(g)}
        total += counted
    n_groups = sum(1 for g, members in sc["groups"].items() if any(comps[m].direction == lead and comps[m].points > 0 for m in members))
    out.update({"direction": lead, "total": total, "groups": groups, "distinct_groups": n_groups, "neutral_support": neutral})
    why = []
    if opposing:
        why.append("CONFLICTING_EVIDENCE")
        out["opposing"] = opposing
    if any(not (comps[m].direction == lead and comps[m].points > 0) for m in sc["mandatory_components"]):
        why.append("MANDATORY_COMPONENT_MISSING")
    if n_groups < sc["min_distinct_evidence_groups"]:
        why.append("INSUFFICIENT_EVIDENCE_GROUPS")
    if total < sc["min_total_score"]:
        why.append("SCORE_BELOW_MIN")
    ls = ctx.get("last_signal")
    if not why and ls and ls.get("direction") == lead and ls.get("age_sec", 1e9) < sc["duplicate_window_sec"]:
        why.append("DUPLICATE_SIGNAL")
    out["reasons"] = why
    out["status"] = "SIGNAL" if not why else "WAIT"
    return out
