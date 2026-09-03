"""
PHASE 8 / PHASE 9 — build the immutable per-trade entry-feature snapshot and the
per-trade ground-truth outcome record.

Everything is read straight from the live decision context (`sig` from
decide_from_context + the option `chain` that produced it). Nothing is
recomputed from later data. A field with no real value stays None and its
reason is recorded in `missing_reasons` — never 0, never a guess.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

# indicators the state-classifier / regime engines compute internally from the
# candle series but do not return as standalone values.
_DERIVED_NOT_SURFACED = ("ema", "sma", "rsi", "macd", "adx", "bollinger", "price_action")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _leg_for(chain, strike, side):
    """The CE/PE leg dict at `strike` from the chain that produced this signal."""
    if strike is None:
        return {}
    key = "ce" if str(side).upper() == "CE" else "pe"
    best, bestd = None, 1e18
    for r in chain or []:
        try:
            d = abs(float(r.get("strike")) - float(strike))
        except (TypeError, ValueError):
            continue
        if d < bestd:
            best, bestd = r, d
    if best is None or bestd > 1e-3:
        return {}
    return best.get(key) or {}


def build_entry_features(*, sig: dict, chain: list, sym: str, market: str,
                         trade_id: str, signal_id: str, underlying_ltp,
                         oi_quality: dict | None, data_quality: dict | None) -> dict:
    sig = sig or {}
    side = str(sig.get("decision") or "").split("_")[-1]           # CE | PE
    leg = _leg_for(chain, sig.get("strike"), side)
    oiq = oi_quality or {}
    dq = data_quality or {}

    atr = sig.get("atr")
    atr_pct = None
    try:
        if atr is not None and underlying_ltp:
            atr_pct = round(float(atr) / float(underlying_ltp) * 100.0, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        atr_pct = None

    row = {
        "trade_id": trade_id, "signal_id": signal_id,
        "captured_ts": _now_iso(), "session_date_ist": _now_iso()[:10],
        "market": market, "underlying": sym.upper(),
        "expiry": sig.get("expiry"), "strike": sig.get("strike"), "option_type": side,
        "tradingsymbol": sig.get("tradingsymbol"), "symboltoken": str(sig.get("token") or "") or None,

        "underlying_ltp": underlying_ltp,
        "option_ltp": leg.get("ltp") if leg.get("ltp") is not None else sig.get("entry"),
        "entry_price": sig.get("entry"),

        "vwap": sig.get("vwap"), "vwap_status": sig.get("vwap_status"),
        "atr": atr, "atr_pct": atr_pct,
        "momentum": sig.get("momentum"), "state_score": sig.get("state_score"),
        "signal_score": sig.get("signal_score"),
        "probability": sig.get("probability"), "confidence": sig.get("confidence"),
        "regime": sig.get("regime"), "signal_type": sig.get("signal_type"),
        "support": sig.get("support"), "resistance": sig.get("resistance"),
        "support_strength": sig.get("support_strength"),
        "resistance_strength": sig.get("resistance_strength"),
        "mtf_alignment": sig.get("mtf_alignment"), "false_risk": sig.get("false_risk"),
        "gex_flip": sig.get("gex_flip"), "gex_pin": sig.get("gex_pin"),
        "gex_regime_sign": sig.get("gex_regime_sign"), "gex_sigma": sig.get("gex_sigma"),

        # OI family (selected leg + chain rollup)
        "oi": leg.get("oi"),
        "oi_change": leg.get("oi_chg") if leg.get("oi_chg") is not None else leg.get("oi_change"),
        "pcr": oiq.get("pcr"), "max_pain": oiq.get("max_pain"),
        "oi_status": leg.get("oi_status"), "pcr_quality": oiq.get("quality_status"),
        "oi_coverage": oiq.get("coverage_ratio"),

        # greeks (selected leg) — real broker values or None for MCX
        "iv": leg.get("iv"), "delta": leg.get("delta"), "gamma": leg.get("gamma"),
        "theta": leg.get("theta"), "vega": leg.get("vega"),
        "greeks_source": leg.get("greeks_source"),
        "volume": leg.get("vol_delta") if leg.get("vol_delta") is not None else leg.get("volume"),

        # plan
        "planned_sl": sig.get("stop_loss"), "planned_t1": sig.get("target_1"),
        "planned_t2": sig.get("target_2"), "planned_t3": None,
        "planned_rr": sig.get("rr"), "planned_ev": sig.get("ev"),
        "trailing_stop": sig.get("trailing_stop"), "max_hold_sec": sig.get("max_hold_sec"),

        "data_quality": json.dumps(dq.get("groups") or {}, separators=(",", ":"))[:4000],
        "data_quality_score": dq.get("score"),
        "calibration_id": sig.get("calib_version"),
        "component_scores": json.dumps(sig.get("component_scores") or {}, separators=(",", ":"))[:2000],
        "model_version": sig.get("model_version"),
    }
    for k in _DERIVED_NOT_SURFACED:
        row[k] = None

    # record why anything is null
    missing = {}
    for k in _DERIVED_NOT_SURFACED:
        missing[k] = "DERIVED_NOT_SURFACED (state/regime engines compute internally)"
    row["planned_t3"] = None
    missing["planned_t3"] = "NOT_IMPLEMENTED (T3 target — PHASE 11)"
    if row["delta"] is None:
        missing["greeks"] = ("BROKER_UNSUPPORTED" if leg.get("greeks_source") == "UNAVAILABLE"
                             else "leg greeks missing")
    if row["oi"] is None:
        missing["oi"] = leg.get("oi_reason") or leg.get("oi_status") or "leg OI missing"
    if row["pcr"] is None:
        missing["pcr"] = oiq.get("quality_status") or "no chain"
    if row["calibration_id"] is None:
        missing["calibration_id"] = "calibration still on prior (no fitted curve yet)"
    row["missing_reasons"] = json.dumps(missing, separators=(",", ":"))[:3000]
    return row


def build_exit_outcome(*, updated: dict, entry_feat: dict | None) -> dict:
    """PHASE 9 — ground-truth record from the closed paper trade + its immutable
    entry snapshot. Timing is derived from opened_ts/closed_ts only."""
    u = updated or {}
    ef = entry_feat or {}

    def _pt(x):
        try:
            return datetime.fromisoformat(str(x).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    o_ts, c_ts = _pt(u.get("opened_ts")), _pt(u.get("closed_ts"))
    held = round((c_ts - o_ts).total_seconds()) if (o_ts and c_ts) else None

    entry = u.get("entry") or ef.get("entry_price")
    exitp = u.get("exit_price")
    t1 = u.get("target_1") or ef.get("planned_t1")
    sl0 = ef.get("planned_sl") or u.get("stop_loss")
    pts = (exitp - entry) if (exitp is not None and entry is not None) else None
    risk = abs(entry - sl0) if (entry is not None and sl0 is not None) else None
    reason = str(u.get("exit_reason") or "").upper()

    return {
        "trade_id": u.get("trade_id"), "signal_id": u.get("signal_id"),
        "closed_ts": u.get("closed_ts") or _now_iso(), "opened_ts": u.get("opened_ts"),
        "underlying": u.get("underlying"), "option_type": u.get("option_type"),
        "strategy": u.get("strategy"),
        "entry_price": entry, "exit_price": exitp, "exit_reason": u.get("exit_reason"),
        "mfe": u.get("mfe"), "mae": u.get("mae"), "mae_before_mfe": None,
        "time_to_mfe_peak_sec": None, "time_to_exit_sec": held,
        "time_to_t1_sec": held if reason == "TARGET" else None,
        "time_to_t2_sec": None,
        "time_to_sl_sec": held if reason in ("STOP", "TRAIL") else None,
        "realized_points": round(pts, 4) if pts is not None else None,
        "realized_pnl": u.get("pnl"),
        "r_multiple": round(pts / risk, 4) if (pts is not None and risk) else None,
        "outcome": u.get("result"),
        "t1_before_sl": 1 if reason == "TARGET" else (0 if reason in ("STOP", "TRAIL") else None),
        "t2_before_sl": None,
        "sl_before_target": 1 if reason == "STOP" else (0 if reason == "TARGET" else None),
        "reversal_after_entry": 1 if (u.get("mae") and risk and u["mae"] >= risk) else 0,
        "time_expiry": 1 if reason in ("TIME", "TIME_NODATA") else 0,
    }
