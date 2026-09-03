"""
Smart CE/PE option selector (spec section 24 / old §15) — slice 3/6.

Given a confirmed direction from MATHEMATICAL_CONFLUENCE_ENGINE_V1 plus the live
option chain, pick the single best liquid contract on the wanted side. Reuses
`engines.option_engine.analyse_leg` + `select_option` (spec §47/§52 — no
duplicate scoring). Adds OI / dOI / premium-momentum / spread / theta / IV /
ATM-distance context and a deterministic, explainable `selection_score`.

Deterministic: same chain + direction + profile -> same pick. No look-ahead.
"""
from __future__ import annotations

from ..engines.option_engine import analyse_leg, select_option

# selection_score weighting (0..1 each; configurable). NOT calibrated.
_W = {
    "leg_quality": 0.30,      # option_engine.analyse_leg quality_score
    "liquidity": 0.20,        # OI + volume at the strike
    "translation": 0.15,      # |delta|*move + 0.5*gamma*move^2 (premium responsiveness)
    "premium_momentum": 0.10, # recent premium change in the trade's favour
    "atm_distance": 0.10,     # closer to ATM (within the profile band) is better
    "spread": 0.10,           # tighter (proxy) spread is better
    "theta": 0.05,            # lower theta drag is better
}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _leg_from_chain_row(row: dict, side: str) -> dict:
    pre = "ce_" if side == "CE" else "pe_"
    return {
        "strike": _f(row.get("strike")),
        "ltp": _f(row.get(pre + "ltp")),
        "oi": _f(row.get(pre + "oi")),
        "oi_change": _f(row.get(pre + "oi_change")),
        "vol_delta": _f(row.get(pre + "volume")),
        "chg_pct": _f(row.get(pre + "ltp_change_pct")),   # optional (from a snapshot delta)
        "delta": _f(row.get(pre + "delta")),
        "gamma": _f(row.get(pre + "gamma")),
        "theta": _f(row.get(pre + "theta")),
        "vega": _f(row.get(pre + "vega")),
        "iv": _f(row.get(pre + "iv")),
        "token": row.get(pre + "token"),
        "tradingsymbol": row.get(pre + "tradingsymbol"),
        "expiry": row.get("expiry"),
    }


def select(*, direction: str, spot: float, chain: list[dict], atm: float | None = None,
           strike_step: float = 50.0, expected_move_pts: float | None = None,
           allowed_option_distance: int = 2, premium_min: float = 3.0,
           premium_max: float = 100000.0, weights: dict | None = None) -> dict:
    """direction: 'CE' | 'PE' | 'NONE'. Returns the selected contract + full
    candidate breakdown + reasons, or a NO_SELECTION dict with the reason."""
    w = {**_W, **(weights or {})}
    side = str(direction).upper()
    if side not in ("CE", "PE"):
        return {"status": "NO_SELECTION", "reason": f"direction={direction} (need CE or PE)"}
    if not chain or spot is None:
        return {"status": "DATA_INSUFFICIENT",
                "missing": ["option_chain" if not chain else "spot"]}

    atm = _f(atm) or (min((_f(r.get("strike")) for r in chain if _f(r.get("strike"))),
                          key=lambda k: abs(k - spot)) if chain else spot)
    move = _f(expected_move_pts) or max(spot * 0.002, strike_step)     # fallback: ~0.2% / one step

    # candidate strikes within the profile's ATM band, on the wanted side
    band = allowed_option_distance * strike_step + 1e-6
    legs = []
    for r in chain:
        k = _f(r.get("strike"))
        if k is None or abs(k - atm) > band:
            continue
        leg = _leg_from_chain_row(r, side)
        if leg["ltp"] is None or leg["ltp"] < premium_min or leg["ltp"] > premium_max:
            continue
        a = analyse_leg({}, leg, opt_type=side, index_move_pts=move, chain=chain,
                        config={"premium_min": premium_min, "premium_max": premium_max},
                        light=True)
        legs.append({"row": r, "leg": leg, "analysis": a})

    if not legs:
        return {"status": "NO_SELECTION",
                "reason": f"no liquid {side} contract within {allowed_option_distance} "
                          f"strike(s) of ATM with premium in [{premium_min}, {premium_max}]"}

    # reuse option_engine.select_option for the quality + ATM-proximity pick,
    # then re-rank with the richer selection_score.
    picked = select_option([x["analysis"] for x in legs], "BUY_CE" if side == "CE" else "BUY_PE",
                           atm=atm, config={"strike_step": strike_step})

    # normalisers across the candidate set
    ois = [x["leg"]["oi"] or 0.0 for x in legs]
    vols = [x["leg"]["vol_delta"] or 0.0 for x in legs]
    max_oi = max(ois) or 1.0
    max_vol = max(vols) or 1.0

    scored = []
    for x in legs:
        leg, a = x["leg"], x["analysis"]
        k = leg["strike"]
        # premium momentum in the trade's favour (chg_pct positive = premium rising = good for a long option)
        pm = leg.get("chg_pct")
        pm_score = 0.0 if pm is None else max(0.0, min(1.0, pm / 5.0))
        # spread proxy: 1m-ish range not in chain -> use own_trend atr / ltp if present, else neutral
        atr = _f((a.get("sr") or {}).get("atr"))
        spread_proxy = min(1.0, (atr / leg["ltp"])) if (atr and leg["ltp"]) else 0.35
        spread_score = 1.0 - spread_proxy
        theta_score = 1.0 - a.get("theta_drag", 0.3)
        atm_dist_score = max(0.0, 1.0 - abs(k - atm) / band)
        liq_score = min(1.0, 0.6 * (leg["oi"] or 0.0) / max_oi + 0.4 * (leg["vol_delta"] or 0.0) / max_vol)

        sc = (w["leg_quality"] * a["quality_score"] / 100.0
              + w["liquidity"] * liq_score
              + w["translation"] * a["translation_score"]
              + w["premium_momentum"] * pm_score
              + w["atm_distance"] * atm_dist_score
              + w["spread"] * spread_score
              + w["theta"] * theta_score)
        scored.append({
            "strike": k, "option_type": side,
            "option_ltp": leg["ltp"], "oi": leg["oi"], "oi_change": leg["oi_change"],
            "volume": leg["vol_delta"], "delta": leg["delta"], "iv": leg["iv"],
            "iv_context": a.get("iv_context"), "theta_drag": a.get("theta_drag"),
            "spread_proxy": round(spread_proxy, 3),
            "atm_distance_strikes": round(abs(k - atm) / strike_step, 2),
            "leg_quality": a["quality_score"],
            "translation_score": a["translation_score"],
            "expected_premium_move": (a.get("translation") or {}).get("expected_premium_move"),
            "greeks_available": a.get("greeks_available"),
            "selection_score": round(min(100.0, sc * 100.0), 1),
        })

    scored.sort(key=lambda c: (-c["selection_score"], c["atm_distance_strikes"]))
    best = scored[0]
    # keep option_engine's pick visible for cross-check
    best["option_engine_pick_strike"] = (picked or {}).get("strike")

    reasons = [
        f"{side} {best['strike']} — selection_score {best['selection_score']}/100",
        f"leg quality {best['leg_quality']}/100, OI {best['oi']:,.0f}"
        + (f", dOI {best['oi_change']:+,.0f}" if best['oi_change'] is not None else ""),
        f"premium ₹{best['option_ltp']}, {best['atm_distance_strikes']} strike(s) from ATM",
        f"expected premium move ≈ ₹{best['expected_premium_move']} on a {round(move)}-pt index move"
        if best.get("expected_premium_move") is not None else "premium-move estimate: greeks unavailable",
    ]
    if best.get("iv_context") in ("RICH",):
        reasons.append("caution: IV is RICH vs the chain median (premium expensive)")
    if (best.get("theta_drag") or 0) > 0.5:
        reasons.append("caution: high theta drag on this leg")

    return {
        "status": "OK",
        "selected_strike": best["strike"],
        "option_type": side,
        "option_ltp": best["option_ltp"],
        "delta": best["delta"],
        "oi": best["oi"],
        "oi_change": best["oi_change"],
        "volume": best["volume"],
        "spread": best["spread_proxy"],
        "iv": best["iv"], "iv_context": best["iv_context"],
        "selection_score": best["selection_score"],
        "atm": atm, "expected_index_move_pts": round(move, 2),
        "candidates": scored,
        "reasons": reasons,
        "deterministic": True,
        "calibration": "UNCALIBRATED — selection weights are defaults (spec §24/§26)",
    }
