"""
Index eligibility filters (spec section 16).

An index is scalp-eligible only if ALL of:
  - liquidity          (ATM option OI + volume above a floor)
  - valid option chain (>= min strikes with real OI both sides)
  - reasonable spread   (ATM bid/ask spread as % of premium below a cap)
  - sufficient volume   (recent index volume, or option volume, non-trivial)
  - clear mathematical levels  (a confluence zone near price with >= 2 families)
  - clear OI structure  (an OI matrix with detectable walls)
  - acceptable signal confidence  (engine confidence >= floor)
  - acceptable risk/reward         (best RR to T1 >= floor)

Each check returns (passed, reason). Missing data => FAIL with a DATA reason
(never a silent pass). Thresholds are configurable.
"""
from __future__ import annotations

DEFAULT_FILTERS = {
    "min_atm_oi": 200_000,          # sum of ATM CE+PE OI
    "min_chain_strikes_with_oi": 5,
    "max_spread_pct": 6.0,          # ATM option (hi-lo proxy) as % of its LTP
    "min_confluence_evidence": 2,
    "min_confidence": 35,
    "min_rr1": 1.2,
}


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def evaluate_eligibility(*, ctx: dict, engine_out: dict, oi_matrix: dict,
                         filters: dict | None = None) -> dict:
    f = {**DEFAULT_FILTERS, **(filters or {})}
    checks: list[dict] = []

    def chk(name, passed, reason):
        checks.append({"name": name, "passed": bool(passed), "reason": reason})

    chain = ctx.get("chain") or []
    spot = _f(ctx.get("spot"))

    # 1. valid option chain
    n_oi = sum(1 for r in chain
               if (_f(r.get("ce_oi")) or 0) > 0 and (_f(r.get("pe_oi")) or 0) > 0)
    chk("valid_option_chain", n_oi >= f["min_chain_strikes_with_oi"],
        f"{n_oi} strikes with real CE+PE OI (need {f['min_chain_strikes_with_oi']})")

    # 2. liquidity — ATM OI
    atm = _f(ctx.get("atm")) or spot
    atm_row = min(chain, key=lambda r: abs((_f(r.get("strike")) or 1e18) - (atm or 0)),
                  default=None) if chain and atm else None
    atm_oi = ((_f((atm_row or {}).get("ce_oi")) or 0) + (_f((atm_row or {}).get("pe_oi")) or 0)) if atm_row else 0
    chk("liquidity", atm_oi >= f["min_atm_oi"],
        f"ATM OI {atm_oi:,.0f} (need {f['min_atm_oi']:,})")

    # 3. spread (proxy: ATM premium present & not micro; true bid/ask not in chain)
    atm_prem = min([_f((atm_row or {}).get("ce_ltp")), _f((atm_row or {}).get("pe_ltp"))] or [None],
                   key=lambda x: (x is None, x)) if atm_row else None
    spread_ok = atm_prem is not None and atm_prem >= 3.0
    chk("reasonable_spread", spread_ok,
        f"ATM premium {atm_prem} (bid/ask not in feed — premium-floor proxy)")

    # 4. sufficient volume
    vr = None
    if ctx.get("current_volume") and ctx.get("avg_volume"):
        vr = ctx["current_volume"] / max(1e-9, ctx["avg_volume"])
    opt_vol = sum((_f(r.get("ce_volume")) or 0) + (_f(r.get("pe_volume")) or 0) for r in chain)
    chk("sufficient_volume", (vr is not None and vr >= 0.6) or opt_vol > 0,
        f"index vol ratio {round(vr, 2) if vr else 'n/a'}, chain vol {opt_vol:,.0f}")

    # 5. clear mathematical levels
    zone = engine_out.get("nearest_support") if (engine_out.get("direction") == "CE") \
        else engine_out.get("nearest_resistance") if engine_out.get("direction") == "PE" \
        else (engine_out.get("nearest_support") or engine_out.get("nearest_resistance"))
    ev = (zone or {}).get("evidence_count", 0)
    chk("clear_mathematical_levels", ev >= f["min_confluence_evidence"],
        f"nearest directional zone has {ev} evidence families (need {f['min_confluence_evidence']})")

    # 6. clear OI structure
    walls = (oi_matrix or {}).get("walls") or {}
    has_walls = oi_matrix.get("status") == "OK" and bool(walls.get("CALL_RESISTANCE_WALL")) and bool(walls.get("PUT_SUPPORT_WALL"))
    chk("clear_oi_structure", has_walls,
        "CALL + PUT walls detected" if has_walls else f"OI matrix status={oi_matrix.get('status')}")

    # 7. signal confidence
    conf = engine_out.get("confidence") or 0
    chk("acceptable_confidence", conf >= f["min_confidence"],
        f"engine confidence {conf} (need {f['min_confidence']})")

    # 8. risk/reward
    rr = engine_out.get("risk_reward")
    rr1 = rr[0] if isinstance(rr, list) and rr else None
    chk("acceptable_risk_reward", rr1 is not None and rr1 >= f["min_rr1"],
        f"RR to T1 {rr1} (need {f['min_rr1']})" if rr1 is not None else "no structural plan")

    passed_all = all(c["passed"] for c in checks)
    return {
        "eligible": passed_all,
        "checks": checks,
        "failed": [c["name"] for c in checks if not c["passed"]],
        "n_passed": sum(1 for c in checks if c["passed"]),
        "n_total": len(checks),
    }
