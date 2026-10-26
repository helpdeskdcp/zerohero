"""Configurable 100-point score. Weights of the components: contribution 30, breadth 20, sector 15, trend15 15, entry5 10, derivatives 10.
Direction is the sign of the score; actionable only when (a) every REQUIRED component is available, (b) the contribution component is not blocked
(official weights validated), (c) |score| crosses the threshold. Missing/blocked components score 0 and are listed - never substituted."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass
class ScoreConfig:
    w_contribution: float = 30
    w_breadth: float = 20
    w_sector: float = 15
    w_trend15: float = 15
    w_entry5: float = 10
    w_derivatives: float = 10
    strong: float = 70
    moderate: float = 55
    weak: float = 45
    def to_dict(self): return asdict(self)


def _clip(x): return max(-1.0, min(1.0, x))


def score(cfg: ScoreConfig, *, snapshot: dict | None, trend: dict | None, entry: dict | None, deriv: dict | None) -> dict:
    comps, missing, basis = {}, [], {}
    # contribution + breadth + sector need a tradable universe snapshot (weights validated)
    if snapshot and snapshot.get("status") == "OK":
        net = snapshot["contribution"]["net"]                          # index-% contribution; +-0.5 index-% counts as full strength
        comps["contribution"] = _clip(net / 0.5) * cfg.w_contribution
        b = snapshot["breadth"]; comps["breadth"] = _clip((b["weighted_adv"] - 0.5) * 2) * cfg.w_breadth
        sec = snapshot["sectors"]; tw = sum(s["weight_pct"] for s in sec) or 1
        comps["sector"] = _clip(sum((1 if s["contrib"] > 0 else -1 if s["contrib"] < 0 else 0) * s["weight_pct"] for s in sec) / tw) * cfg.w_sector
    else:
        why = (snapshot or {}).get("reason", "NO_SNAPSHOT")
        uw = (snapshot or {}).get("unweighted") or {}
        missing.append({"component": "contribution", "dependency": "VERIFIED_OFFICIAL_WEIGHTS" if why == "UNIVERSE_NOT_TRADABLE" else why})
        if uw.get("status") == "OK":                                   # weight-free substitutes, scored on counts only and labelled as such
            comps["breadth"] = _clip(uw["adv_dec_ratio"]) * cfg.w_breadth
            n = sum(x["n"] for x in uw["sectors"]) or 1
            comps["sector"] = _clip(sum(x["n"] * (1 if x["strength"] > 0 else -1 if x["strength"] < 0 else 0) for x in uw["sectors"]) / n) * cfg.w_sector
            basis["breadth"] = basis["sector"] = "UNWEIGHTED"
        else:
            miss = uw.get("reason") or why
            missing += [{"component": k, "dependency": "VERIFIED_OFFICIAL_WEIGHTS" if why == "UNIVERSE_NOT_TRADABLE" and not uw else miss} for k in ("breadth", "sector")]
    t = (trend or {}).get("state")
    if t in ("BULLISH", "BEARISH"): comps["trend15"] = (1 if t == "BULLISH" else -1) * cfg.w_trend15
    else: missing.append({"component": "trend15", "dependency": t or "NO_TREND"})
    s = (entry or {}).get("signal")
    if s in ("LONG", "SHORT"): comps["entry5"] = (1 if s == "LONG" else -1) * cfg.w_entry5
    else: missing.append({"component": "entry5", "dependency": (entry or {}).get("reason", "NO_ENTRY")})
    if deriv and deriv.get("available") and deriv.get("bias") is not None: comps["derivatives"] = _clip(deriv["bias"]) * cfg.w_derivatives
    else: missing.append({"component": "derivatives", "dependency": "NO_VALID_DERIVATIVES_DATA"})
    total = sum(comps.values()); mag = abs(total)
    blocked = [m for m in missing if m["component"] == "contribution"]
    side = "LONG" if total > 0 else "SHORT" if total < 0 else None
    grade = "STRONG" if mag >= cfg.strong else "MODERATE" if mag >= cfg.moderate else "WEAK" if mag >= cfg.weak else "NONE"
    # a trade idea also needs the 15m trend and the 5m entry to agree with the score's direction
    aligned = side is not None and t == ("BULLISH" if side == "LONG" else "BEARISH") and s == side
    actionable = bool(not blocked and aligned and grade in ("STRONG", "MODERATE"))
    reason = "ACTIONABLE" if actionable else ("BLOCKED_BY_" + blocked[0]["dependency"] if blocked else "NOT_ALIGNED_OR_BELOW_THRESHOLD")
    return {"total": round(total, 2), "abs": round(mag, 2), "side": side, "grade": grade, "actionable": actionable, "reason": reason, "components": {k: round(v, 2) for k, v in comps.items()},
            "missing": missing, "basis": basis, "max_possible_now": round(sum({"contribution": cfg.w_contribution, "breadth": cfg.w_breadth, "sector": cfg.w_sector, "trend15": cfg.w_trend15, "entry5": cfg.w_entry5, "derivatives": cfg.w_derivatives}[k] for k in comps), 1)}
