"""
MATHEMATICAL_CONFLUENCE_ENGINE_V1 — orchestrator.

Composes the existing codebase pieces (turning_point_engine._pivots,
oi_change.classify_oi_action) + the new modules here into ONE evidence-based
signal. Never signals on a single indicator (section, top of spec). Emits
DATA_INSUFFICIENT with the exact missing fields (section 28). No order path.
"""
from __future__ import annotations

from . import confluence as _cf
from . import levels as _lv
from . import market_position as _mp
from . import oi_confluence as _oi
from . import scoring as _sc
from . import swings as _sw

ENGINE_NAME = "MATHEMATICAL_CONFLUENCE_ENGINE_V1"
SIGNAL_TYPES = ("BUY_CE", "BUY_PE", "NO_TRADE", "WAIT",
                "BREAKOUT_WATCH", "BREAKDOWN_WATCH", "REVERSAL_WATCH")


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


class MathematicalConfluenceEngine:
    def __init__(self, *, weights: dict | None = None, swing_n: int = 2,
                 tol_pct: float = 0.0012, min_evidence_high_conf: int = 3):
        self.weights = weights
        self.swing_n = swing_n
        self.tol_pct = tol_pct
        self.min_evidence = min_evidence_high_conf

    # ---------------------------------------------------------------- main
    def evaluate(self, *, instrument: str, timestamp: str,
                 prev_day: dict, today_open, current_price,
                 day_high, day_low, current_volume=None, avg_volume=None,
                 bars: list[dict] | None = None,
                 chain: list[dict] | None = None,
                 candle_signals: list[str] | None = None,
                 breakout_state: str | None = None,
                 retest_state: str | None = None,
                 reversal_candidate: bool = False,
                 mom_3m: float | None = None) -> dict:
        # -------- data-quality gate (section 28) -------------------------
        missing = []
        pdh, pdl, pdc = _f((prev_day or {}).get("high")), _f((prev_day or {}).get("low")), _f((prev_day or {}).get("close"))
        if pdh is None:
            missing.append("previous_day_high")
        if pdl is None:
            missing.append("previous_day_low")
        if pdc is None:
            missing.append("previous_day_close")
        P = _f(current_price)
        if P is None:
            missing.append("current_price")
        if missing:
            return {"engine": ENGINE_NAME, "instrument": instrument, "timestamp": timestamp,
                    "status": "DATA_INSUFFICIENT", "missing": missing,
                    "signal_type": "NO_TRADE",
                    "reason_codes": ["DATA_INSUFFICIENT: " + ", ".join(missing)]}

        # -------- levels + confluence zones (sections 2,3,5) ------------
        sw = _sw.detect_swings(bars or [], n=self.swing_n) if bars else {}
        levels = _lv.normalized_levels(
            pdh=pdh, pdl=pdl, pdc=pdc, today_open=today_open,
            day_high=day_high, day_low=day_low,
            swing_high=sw.get("last_swing_high"), swing_low=sw.get("last_swing_low"),
            recent_swing_high=sw.get("recent_swing_high"), recent_swing_low=sw.get("recent_swing_low"),
        )
        oi_mat = _oi.oi_matrix(chain or [], P) if chain else {"status": "DATA_INSUFFICIENT"}
        if oi_mat.get("status") == "OK":
            levels += _lv.normalized_levels(pdh=pdh, pdl=pdl, oi_support=(oi_mat.get("walls") or {})
                                            .get("PUT_SUPPORT_WALL", {}).get("strike"),
                                            oi_resistance=(oi_mat.get("walls") or {})
                                            .get("CALL_RESISTANCE_WALL", {}).get("strike"),
                                            oi_walls=_oi.oi_walls_as_levels(oi_mat))
            # strip the duplicate pivot/gann that the second call re-adds
            seen = set()
            dedup = []
            for lvl in levels:
                key = (lvl["source"], lvl["value"])
                if key in seen:
                    continue
                seen.add(key)
                dedup.append(lvl)
            levels = dedup

        zones = _cf.cluster_levels(levels, P, tol_pct=self.tol_pct)
        near_sup = _cf.nearest_zone(zones, P, "support")
        near_res = _cf.nearest_zone(zones, P, "resistance")
        high_conf = _cf.high_confluence_zones(zones, min_evidence=self.min_evidence)

        # -------- market position + regime (section 12) -----------------
        prev_range = (pdh - pdl) if (pdh and pdl) else None
        day_range = (_f(day_high) - _f(day_low)) if (_f(day_high) and _f(day_low)) else None
        mp = _mp.market_position(pdh=pdh, pdl=pdl, pdc=pdc, today_open=today_open,
                                 day_high=day_high, day_low=day_low, current_price=P)
        near_any = near_sup if (near_sup and (not near_res or near_sup["distance_pts"] <= near_res["distance_pts"])) else near_res
        regime = _mp.classify_regime(mp, prev_range=prev_range, day_range=day_range,
                                     mom_3m=mom_3m, near_zone=near_any, breakout_state=breakout_state)

        # -------- direction hypothesis (structure, NOT a single signal) --
        direction = self._direction(regime["regime"], mp, near_sup, near_res, breakout_state, mom_3m)

        # -------- sub-scores (section 13) -------------------------------
        vol_ratio = (_f(current_volume) / _f(avg_volume)) if (_f(current_volume) and _f(avg_volume)) else None
        struct_zone = near_sup if direction == "CE" else near_res
        m_s, m_r = _sc.mathematical_score(struct_zone, high_conf)
        o_s, o_r = _sc.oi_score(oi_mat, direction) if direction in ("CE", "PE") else (0.0, ["no direction"])
        p_s, p_r = _sc.price_action_score(candle_signals=candle_signals or [],
                                          at_zone=bool(struct_zone and struct_zone["distance_pct"] is not None
                                                       and struct_zone["distance_pct"] < 0.25),
                                          direction=direction, reversal_candidate=reversal_candidate)
        v_s, v_r = _sc.volume_score(vol_ratio=vol_ratio, direction_aligned=direction in ("CE", "PE"))
        b_s, b_r = _sc.breakout_score(breakout_state)
        rt_s, rt_r = _sc.retest_score(retest_state)
        swst = _sw.swing_stats(bars or [], struct_zone["center"], now_index=None) if (bars and struct_zone) else {}
        swing_align = ((direction == "CE" and (sw.get("last_swing_low") or 0) and P > (sw.get("recent_swing_low") or P))
                       or (direction == "PE" and (sw.get("last_swing_high") or 0) and P < (sw.get("recent_swing_high") or P)))
        sw_s, sw_r = _sc.swing_score(swing_align=bool(swing_align),
                                     swing_touch_count=swst.get("swing_touch_count", 0),
                                     swing_rejection_count=swst.get("swing_rejection_count", 0))

        sub = {"mathematical": m_s, "oi": o_s, "price_action": p_s, "volume": v_s,
               "breakout": b_s, "retest": rt_s, "swing": sw_s}
        conf = _sc.confluence_score(sub, self.weights)

        # -------- signal type + gates --------------------------------
        sig_type, confidence, no_trade_reason = self._signal_type(
            direction, conf["confluence_score"], sub, oi_mat, struct_zone,
            breakout_state, retest_state, reversal_candidate, vol_ratio)

        plan = self._plan(direction, P, near_sup, near_res, sw, zones) if sig_type in ("BUY_CE", "BUY_PE") else None

        return {
            "engine": ENGINE_NAME, "instrument": instrument, "timestamp": timestamp,
            "status": "OK",
            "spot": P, "direction": direction, "signal_type": sig_type,
            "confidence": confidence,
            "confluence_score": conf["confluence_score"],
            "score_breakdown": conf["breakdown"],
            "market_regime": regime["regime"], "regime_confidence": regime["confidence"],
            "market_position": mp,
            "nearest_support": near_sup, "nearest_resistance": near_res,
            "confluence_zones": zones[:8],
            "high_confluence_zones": high_conf,
            "oi_matrix": oi_mat,
            "entry_zone": plan["entry_zone"] if plan else None,
            "stop_loss": plan["stop_loss"] if plan else None,
            "target_1": plan["target_1"] if plan else None,
            "target_2": plan["target_2"] if plan else None,
            "target_3": plan["target_3"] if plan else None,
            "risk_reward": plan["risk_reward"] if plan else None,
            "support_level": (near_sup or {}).get("center"),
            "resistance_level": (near_res or {}).get("center"),
            "mathematical_levels": {"pivots": _lv.classical_pivots(pdh, pdl, pdc),
                                    "gann": _lv.gann_levels(pdh, pdl)},
            "oi_evidence": o_r, "price_action_evidence": p_r, "volume_evidence": v_r,
            "swing_evidence": sw_r, "mathematical_evidence": m_r,
            "breakout_evidence": b_r, "retest_evidence": rt_r,
            "reason_codes": (m_r + o_r + p_r + v_r + b_r + rt_r + sw_r)[:14],
            "no_trade_reason": no_trade_reason,
            "calibration": "UNCALIBRATED — score weights are defaults, no backtest yet (section 26)",
        }

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _direction(regime, mp, near_sup, near_res, breakout_state, mom_3m):
        if breakout_state == "BREAKOUT_CONFIRMED":
            return "CE"
        if breakout_state == "BREAKDOWN_CONFIRMED":
            return "PE"
        if regime in ("BULLISH_EXPANSION",):
            return "CE"
        if regime in ("BEARISH_EXPANSION",):
            return "PE"
        pos = mp.get("position_in_prev_day_range")
        if near_sup and (not near_res or near_sup["distance_pts"] < near_res["distance_pts"]) and (mom_3m or 0) >= 0:
            return "CE"
        if near_res and (not near_sup or near_res["distance_pts"] < near_sup["distance_pts"]) and (mom_3m or 0) <= 0:
            return "PE"
        return "NONE"

    @staticmethod
    def _signal_type(direction, cscore, sub, oi_mat, struct_zone, bstate, rstate, reversal, vol_ratio):
        reasons = []
        if direction == "NONE" or struct_zone is None:
            return "NO_TRADE", 0, "no clear directional structure / no confluence zone near price"
        if oi_mat.get("status") != "OK":
            reasons.append("OI data insufficient")
        if vol_ratio is not None and vol_ratio < 0.8:
            reasons.append("volume below recent average")
        if struct_zone["evidence_count"] < 2:
            reasons.append("target zone has < 2 independent evidence families")
        # conflicting OI = a strong opposing wall right on the trade side
        walls = (oi_mat.get("walls") or {})
        if direction == "CE" and walls.get("CALL_RESISTANCE_WALL", {}).get("score", 0) > 70 \
                and abs(walls["CALL_RESISTANCE_WALL"]["strike"] - (struct_zone["center"] or 0)) < (struct_zone["center"] or 1) * 0.003:
            reasons.append("heavy CALL resistance wall right at the target")

        confidence = int(min(95, cscore * 0.9))
        if bstate in ("BREAKOUT_CONFIRMED", "BREAKDOWN_CONFIRMED") and rstate == "RETEST_SUCCESS" and cscore >= 60 and not reasons:
            return ("BUY_CE" if direction == "CE" else "BUY_PE"), confidence, None
        if reversal and struct_zone["evidence_count"] >= 3 and cscore >= 55 and not reasons:
            return ("BUY_CE" if direction == "CE" else "BUY_PE"), confidence, None
        if cscore >= 62 and sub["oi"] >= 8 and sub["price_action"] >= 8 and not reasons:
            return ("BUY_CE" if direction == "CE" else "BUY_PE"), confidence, None
        if bstate in ("BREAKOUT_WATCH",):
            return "BREAKOUT_WATCH", confidence, None
        if bstate in ("BREAKDOWN_WATCH",):
            return "BREAKDOWN_WATCH", confidence, None
        if reversal:
            return "REVERSAL_WATCH", confidence, None
        if cscore >= 45:
            return "WAIT", confidence, "confluence forming; waiting for OI/price-action/volume confirmation"
        return "NO_TRADE", confidence, "; ".join(reasons) or "confluence score below threshold"

    @staticmethod
    def _plan(direction, spot, near_sup, near_res, sw, zones):
        """Structural SL + targets from the next confluence levels (section 20).
        Prices are on the UNDERLYING; option-leg translation is the caller's
        job (reuse the autoscalp option selector)."""
        if direction == "CE":
            entry_ref = near_sup["center"] if near_sup else spot
            sl_base = min(x for x in [near_sup["zone_low"] if near_sup else None,
                                      sw.get("last_swing_low"), sw.get("recent_swing_low")] if x) if any(
                [near_sup, sw.get("last_swing_low")]) else spot * 0.996
            ups = sorted([z["center"] for z in zones if z["center"] > spot])
            t1, t2, t3 = (ups + [spot * 1.004, spot * 1.008, spot * 1.012])[:3]
            entry_lo, entry_hi = round(min(spot, entry_ref) * 0.999, 2), round(max(spot, entry_ref) * 1.001, 2)
            sl = round(sl_base * 0.999, 2)
            risk = max(1e-6, spot - sl)
            return {"entry_zone": [entry_lo, entry_hi], "stop_loss": sl,
                    "target_1": round(t1, 2), "target_2": round(t2, 2), "target_3": round(t3, 2),
                    "risk_reward": [round((t1 - spot) / risk, 2), round((t2 - spot) / risk, 2),
                                    round((t3 - spot) / risk, 2)]}
        entry_ref = near_res["center"] if near_res else spot
        sl_base = max(x for x in [near_res["zone_high"] if near_res else None,
                                  sw.get("last_swing_high"), sw.get("recent_swing_high")] if x) if any(
            [near_res, sw.get("last_swing_high")]) else spot * 1.004
        dns = sorted([z["center"] for z in zones if z["center"] < spot], reverse=True)
        t1, t2, t3 = (dns + [spot * 0.996, spot * 0.992, spot * 0.988])[:3]
        entry_lo, entry_hi = round(min(spot, entry_ref) * 0.999, 2), round(max(spot, entry_ref) * 1.001, 2)
        sl = round(sl_base * 1.001, 2)
        risk = max(1e-6, sl - spot)
        return {"entry_zone": [entry_lo, entry_hi], "stop_loss": sl,
                "target_1": round(t1, 2), "target_2": round(t2, 2), "target_3": round(t3, 2),
                "risk_reward": [round((spot - t1) / risk, 2), round((spot - t2) / risk, 2),
                                round((spot - t3) / risk, 2)]}
