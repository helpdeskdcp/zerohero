"""
AI-OI-OPTIONS engine — deterministic strike selection from an option chain.
Ported 1:1 from the n8n Code node logic.
"""
import math
from datetime import datetime, timezone

MODEL_VERSION = "oi-options-rule-based-v1"


def _num(x):
    try:
        if x is None:
            return None
        f = float(x)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _round(x, d=2):
    if x is None or not math.isfinite(x):
        return None
    p = 10 ** d
    return round(x * p) / p


def run_oi_options_engine(inp: dict) -> dict:
    inp = inp or {}
    cfg = inp.get("config") or {}
    C = {
        "min_oi": _num(cfg.get("min_oi")) or 500,
        "max_spread_pct": _num(cfg.get("max_spread_pct")) or 3,
        "min_volume": _num(cfg.get("min_volume")) or 100,
    }

    def base(status, extra=None):
        out = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "underlying": inp.get("underlying"),
            "spot": _num(inp.get("spot")),
            "expiry": inp.get("expiry"),
            "data_status": "DATA_UNAVAILABLE" if status == "DATA_UNAVAILABLE" else "OK",
            "decision": status,
            "recommended_strike": None,
            "option_type": None,
            "direction": "NONE",
            "strike_selection_score": None,
            "liquidity_status": None,
            "oi_evidence": {},
            "confidence": None,
            "reason": [],
            "metrics": {},
            "model_version": MODEL_VERSION,
        }
        if extra:
            out.update(extra)
        return out

    spot = _num(inp.get("spot"))
    chain = inp.get("chain") or []
    if spot is None:
        return base("DATA_UNAVAILABLE", {"reason": ["FACT: spot price missing"]})
    if len(chain) < 3:
        return base("DATA_UNAVAILABLE", {"reason": [f"FACT: option chain too thin ({len(chain)} strikes)"]})

    rows = []
    for r in chain:
        strike = _num(r.get("strike"))
        if strike is None:
            continue
        rows.append({
            "strike": strike,
            "ce_ltp": _num(r.get("ce_ltp")), "pe_ltp": _num(r.get("pe_ltp")),
            "ce_oi": _num(r.get("ce_oi")) or 0, "pe_oi": _num(r.get("pe_oi")) or 0,
            "ce_oi_change": _num(r.get("ce_oi_change")) or 0, "pe_oi_change": _num(r.get("pe_oi_change")) or 0,
            "ce_volume": _num(r.get("ce_volume")) or 0, "pe_volume": _num(r.get("pe_volume")) or 0,
            "ce_iv": _num(r.get("ce_iv")), "pe_iv": _num(r.get("pe_iv")),
            "ce_bid": _num(r.get("ce_bid")), "ce_ask": _num(r.get("ce_ask")),
            "pe_bid": _num(r.get("pe_bid")), "pe_ask": _num(r.get("pe_ask")),
        })
    rows.sort(key=lambda r: r["strike"])

    atm = min(rows, key=lambda r: abs(r["strike"] - spot))

    tot_call_oi = sum(r["ce_oi"] for r in rows)
    tot_put_oi = sum(r["pe_oi"] for r in rows)
    pcr = _round(tot_put_oi / tot_call_oi, 2) if tot_call_oi > 0 else None

    max_put_row = max(rows, key=lambda r: r["pe_oi"])
    max_call_row = max(rows, key=lambda r: r["ce_oi"])
    oi_support = max_put_row["strike"]
    oi_resistance = max_call_row["strike"]

    def max_pain():
        best_k, best_val = None, float("inf")
        for k in rows:
            pain = 0.0
            for r in rows:
                pain += max(0, k["strike"] - r["strike"]) * r["ce_oi"]
                pain += max(0, r["strike"] - k["strike"]) * r["pe_oi"]
            if pain < best_val:
                best_val, best_k = pain, k["strike"]
        return best_k

    mp = max_pain()

    dir_ = inp.get("directional_bias") if inp.get("directional_bias") in ("BUY", "SELL") else "NONE"
    reasons = []
    if dir_ == "NONE":
        call_build = sum(r["ce_oi_change"] for r in rows)
        put_build = sum(r["pe_oi_change"] for r in rows)
        if put_build > call_build and (pcr is None or pcr >= 1):
            dir_ = "BUY"
            reasons.append("CALC: put writing > call writing -> bullish bias")
        elif call_build > put_build:
            dir_ = "SELL"
            reasons.append("CALC: call writing > put writing -> bearish bias")

    if dir_ == "NONE":
        return base("NO_TRADE", {
            "reason": ["CALC: no directional bias from OI and none supplied"] + reasons,
            "metrics": {"pcr": pcr, "oi_support": oi_support, "oi_resistance": oi_resistance,
                        "max_pain": mp, "total_call_oi": tot_call_oi, "total_put_oi": tot_put_oi},
        })

    opt_type = "CE" if dir_ == "BUY" else "PE"
    idx_atm = rows.index(atm)
    cand_idx = [i for i in (idx_atm - 2, idx_atm - 1, idx_atm, idx_atm + 1, idx_atm + 2) if 0 <= i < len(rows)]

    def leg_for(r):
        if opt_type == "CE":
            return {"ltp": r["ce_ltp"], "oi": r["ce_oi"], "oiChg": r["ce_oi_change"],
                    "vol": r["ce_volume"], "iv": r["ce_iv"], "bid": r["ce_bid"], "ask": r["ce_ask"]}
        return {"ltp": r["pe_ltp"], "oi": r["pe_oi"], "oiChg": r["pe_oi_change"],
                "vol": r["pe_volume"], "iv": r["pe_iv"], "bid": r["pe_bid"], "ask": r["pe_ask"]}

    scored = []
    for i in cand_idx:
        r = rows[i]
        leg = leg_for(r)
        if leg["ltp"] is None or leg["ltp"] <= 0:
            continue
        spread_pct = None
        if leg["bid"] is not None and leg["ask"] is not None and (leg["bid"] + leg["ask"]) > 0:
            spread_pct = 100 * (leg["ask"] - leg["bid"]) / ((leg["ask"] + leg["bid"]) / 2)
        step_dist = abs(i - idx_atm)
        score = 0.0
        why = []
        if leg["oi"] >= C["min_oi"]:
            score += 25; why.append("OI ok")
        else:
            score -= 20; why.append("OI low")
        if leg["vol"] >= C["min_volume"]:
            score += 15; why.append("volume ok")
        else:
            score -= 10; why.append("volume low")
        if spread_pct is not None:
            if spread_pct <= C["max_spread_pct"]:
                score += 20; why.append("tight spread")
            else:
                score -= 25; why.append(f"wide spread {_round(spread_pct,1)}%")
        score += max(0, 20 - step_dist * 8)
        if step_dist == 0:
            why.append("ATM")
        if leg["oiChg"] > 0:
            score += 10; why.append("OI building")
        scored.append({"strike": r["strike"], "score": _round(score, 1), "leg": leg,
                        "spreadPct": _round(spread_pct, 1), "stepDist": step_dist, "why": why})

    if not scored:
        return base("NO_TRADE", {
            "direction": dir_, "option_type": opt_type,
            "reason": ["CALC: no candidate strike had a usable premium"] + reasons,
            "metrics": {"pcr": pcr, "oi_support": oi_support, "oi_resistance": oi_resistance, "max_pain": mp},
        })

    scored.sort(key=lambda s: -s["score"])
    top = scored[0]

    liquidity = "OK"
    if top["leg"]["oi"] < C["min_oi"] or (top["spreadPct"] is not None and top["spreadPct"] > C["max_spread_pct"]):
        liquidity = "POOR"

    if liquidity == "POOR" or top["score"] < 30:
        return base("NO_TRADE", {
            "direction": dir_, "option_type": opt_type, "recommended_strike": top["strike"],
            "strike_selection_score": top["score"], "liquidity_status": liquidity,
            "reason": [f"CALC: best strike fails liquidity/score gate (score {top['score']})"] + reasons + top["why"],
            "metrics": {"pcr": pcr, "oi_support": oi_support, "oi_resistance": oi_resistance, "max_pain": mp},
        })

    margin = (top["score"] - scored[1]["score"]) if len(scored) > 1 else top["score"]
    confidence = _round(min(100, 40 + margin), 1)

    return base("TRADE", {
        "direction": dir_,
        "option_type": opt_type,
        "recommended_strike": top["strike"],
        "strike_selection_score": top["score"],
        "liquidity_status": liquidity,
        "confidence": confidence,
        "oi_evidence": {
            "pcr": pcr, "oi_support": oi_support, "oi_resistance": oi_resistance, "max_pain": mp,
            "total_call_oi": tot_call_oi, "total_put_oi": tot_put_oi,
            "selected_oi": top["leg"]["oi"], "selected_oi_change": top["leg"]["oiChg"],
            "selected_volume": top["leg"]["vol"], "selected_iv": top["leg"]["iv"],
        },
        "reason": [f"INTERPRETATION: {opt_type} {top['strike']} selected by STRIKE_SELECTION_SCORE"] + reasons + top["why"],
        "metrics": {
            "atm_strike": atm["strike"], "selected_premium": top["leg"]["ltp"],
            "selected_spread_pct": top["spreadPct"],
            "candidates": [{"strike": s["strike"], "score": s["score"]} for s in scored],
        },
    })
