"""
Map the live engine's persisted decision (one `live_market_snapshots` row) onto
the 16 requested HCS evidence modules. Each module -> {status, value, note}.

status:  OK          -- computed from a real value
         PARTIAL     -- a weaker proxy is available
         UNOBSERVABLE -- no data exists for this in the environment (never faked)
         NA          -- not applicable to this decision (e.g. NO_TRADE)

Nothing here recomputes indicators from later data. A field with no real value
stays None with a reason.
"""
from __future__ import annotations

import json
import math

_BULLISH = {"SUPPORT_REVERSAL", "RESISTANCE_BREAKOUT"}
_BEARISH = {"SUPPORT_BREAKDOWN", "RESISTANCE_REVERSAL"}


def _f(x):
    try:
        v = float(x)
        return v if v == v and abs(v) != math.inf else None
    except (TypeError, ValueError):
        return None


def _chain(snap):
    try:
        c = json.loads(snap.get("chain_json") or "[]")
        return c if isinstance(c, list) else []
    except (ValueError, TypeError):
        return []


def _atm_leg(chain, atm, side):
    if not chain or atm is None:
        return {}
    near = min(chain, key=lambda r: abs((_f(r.get("strike")) or 1e18) - atm))
    return (near.get("ce") if side == "CE" else near.get("pe")) or {}


def _oi_dominance(chain):
    """total CE OI vs PE OI across the captured chain -> (pcr, tilt, dOI tilt)."""
    ce = sum(_f((r.get("ce") or {}).get("oi")) or 0 for r in chain)
    pe = sum(_f((r.get("pe") or {}).get("oi")) or 0 for r in chain)
    dce = sum(_f((r.get("ce") or {}).get("oi_chg")) or 0 for r in chain)
    dpe = sum(_f((r.get("pe") or {}).get("oi_chg")) or 0 for r in chain)
    pcr = (pe / ce) if ce > 0 else None
    return {
        "ce_oi": ce, "pe_oi": pe, "pcr": round(pcr, 3) if pcr else None,
        "d_ce_oi": dce, "d_pe_oi": dpe,
        # PE writing (dpe>0) = support building = bullish; CE writing = bearish
        "oi_bias": ("BULLISH" if (dpe - dce) > 0 else "BEARISH" if (dce - dpe) > 0 else "FLAT"),
    }


def assemble(snap: dict) -> dict:
    """snap = one live_market_snapshots row (dict). Returns {module_key: {...}}."""
    st = str(snap.get("signal_type") or "NONE")
    direction = "BULLISH" if st in _BULLISH else "BEARISH" if st in _BEARISH else "NONE"
    chain = _chain(snap)
    atm = _f(snap.get("atm"))
    atr = _f(snap.get("atr"))
    ltp = _f(snap.get("index_ltp"))
    vw = _f(snap.get("vwap"))
    dq = _f(snap.get("data_quality_score"))
    oi = _oi_dominance(chain) if chain else {}

    E: dict = {}

    def mod(key, status, value=None, note="", contributes=True):
        E[key] = {"status": status, "value": value, "note": note, "contributes": contributes}

    # 1. abnormal spike  -- state engine's ATR/range component (component_scores not
    #    persisted per NO_TRADE cycle; use raw vs signal score gap + atr as proxy)
    ss, sig = _f(snap.get("state_score")), _f(snap.get("signal_score"))
    mod("abnormal_spike", "PARTIAL",
        value={"state_score": ss, "atr": atr},
        note="state-classifier range/ATR component (h1h7 abnormal-spike not on this shadow path)")
    # 2. spike reaction / rejection
    mod("spike_reaction", "PARTIAL", value=st,
        note="signal_type encodes reversal/continuation at the anchor")
    # 3. breakout + retest
    mod("breakout_retest", "OK" if st in ("RESISTANCE_BREAKOUT", "SUPPORT_BREAKDOWN") else "NA",
        value=st, note="retest quality folded into signal_score by state_classifier")
    # 4. VWAP + EMA structure
    vwap_ok = None
    if vw is not None and ltp is not None:
        vwap_ok = (ltp >= vw) if direction == "BULLISH" else (ltp <= vw) if direction == "BEARISH" else None
    mod("vwap_ema", "OK" if snap.get("vwap_status") else "PARTIAL",
        value={"vwap_status": snap.get("vwap_status"), "ltp_vs_vwap_agrees": vwap_ok},
        note=str(snap.get("vwap_reason") or snap.get("vwap_status") or ""))
    # 5. momentum
    mom = _f(snap.get("momentum"))
    mom_ok = None if mom is None else ((mom > 0) if direction == "BULLISH"
                                       else (mom < 0) if direction == "BEARISH" else None)
    mod("momentum", "PARTIAL",
        value={"roc_pct": mom, "agrees_direction": mom_ok},
        note="ROC + state momentum component; no MACD on this path")
    # 6. relative volume
    mod("relative_volume", "PARTIAL" if snap.get("data_quality") != "invalid_volume" else "UNOBSERVABLE",
        value={"data_quality": snap.get("data_quality")},
        note=("bar volume present" if snap.get("data_quality") != "invalid_volume"
              else "invalid_volume flagged for this symbol/cycle"))
    # 7. OI buildup / CE-PE dominance
    if oi:
        agree = None
        if direction in ("BULLISH", "BEARISH"):
            agree = (oi["oi_bias"] == direction) or (oi["oi_bias"] == "FLAT")
        mod("oi_structure", "OK", value={**oi, "agrees_direction": agree},
            note=f"chain OI: PCR {oi.get('pcr')}, bias {oi.get('oi_bias')}")
    else:
        mod("oi_structure", "UNOBSERVABLE", note="no chain_json on this snapshot")
    # 8. dynamic S/R
    sup, res = _f(snap.get("support")), _f(snap.get("resistance"))
    d_sup = (ltp - sup) / atr if (ltp is not None and sup is not None and atr) else None
    d_res = (res - ltp) / atr if (ltp is not None and res is not None and atr) else None
    mod("support_resistance", "OK" if (sup or res) else "PARTIAL",
        value={"support": sup, "resistance": res, "dist_support_atr": _r(d_sup),
               "dist_resistance_atr": _r(d_res),
               "support_strength": _f(snap.get("support_strength")),
               "resistance_strength": _f(snap.get("resistance_strength"))})
    # 9. regime
    mod("regime", "OK", value={"regime": snap.get("regime")},
        note="regime_mtf.detect_regime")
    # 10. MTF alignment
    mtf = _f(snap.get("mtf_alignment"))
    mod("mtf_alignment", "OK" if mtf is not None else "PARTIAL",
        value={"mtf_alignment": mtf},
        note="regime_mtf.mtf_alignment (1/3/5/15m); opposing-HTF gate already in decide_from_context")
    # 11. liquidity / spread
    leg = _atm_leg(chain, atm, "CE" if direction == "BULLISH" else "PE") if chain else {}
    bid, ask = _f(leg.get("bid")), _f(leg.get("ask"))
    spr_pct = ((ask - bid) / ((ask + bid) / 2) * 100) if (bid and ask and (ask + bid) > 0) else None
    mod("liquidity_spread", "OK" if spr_pct is not None else "PARTIAL",
        value={"atm_opt_bid": bid, "atm_opt_ask": ask, "spread_pct": _r(spr_pct),
               "data_quality_score": dq},
        note="ATM option bid/ask from the captured chain (underlying L2 not available)")
    # 12. exhaustion
    mod("exhaustion", "PARTIAL",
        value={"regime": snap.get("regime"), "atr": atr},
        note="false_risk (divergence/sweep) folded into signal_score; no dedicated RSI-extreme read on this path")
    # 13. fake breakout
    mod("fake_breakout", "OK", value={"no_trade_reason_class": snap.get("no_trade_reason_class")},
        note="state_classifier false_risk -> LIKELY_FALSE forces NONE upstream")
    # 14. absorption
    mod("absorption", "UNOBSERVABLE",
        note="needs aggressor-classified L2 (none in this environment -- ORDERFLOW_STAGE9_L2_RESEARCH.md)",
        contributes=False)
    # 15. momentum acceleration
    mod("momentum_acceleration", "UNOBSERVABLE",
        note="ROC 2nd derivative not surfaced by any engine on this path", contributes=False)
    # 16. setup memory -- filled by memory.py
    mod("setup_memory", "NA", note="see memory.similarity_score()")

    return {"modules": E, "direction": direction, "signal_type": st,
            "oi": oi, "atm_option_spread_pct": _r(spr_pct)}


def _r(x, n=3):
    return round(x, n) if isinstance(x, (int, float)) else None
