"""
SMART_INDEX_SCALPER — the orchestration layer (spec sections 16, 17, 34).

Pipeline per configured index:
    market_context  (shared, cached)
        -> MathematicalConfluenceEngine.evaluate()
        -> oi_matrix
        -> eligibility filters
        -> INDEX_SELECTION_SCORE component scores
    -> rank eligible indices, pick #1 / #2 / #3, explain why #1 won.

RESEARCH / PAPER-ANALYSIS ONLY. No order path. No paper position is opened here
(that is slice 3 — option selection + profile filter). This layer only ranks
and emits the candidate signal.
"""
from __future__ import annotations

from ..mathematical_confluence import MathematicalConfluenceEngine
from ..mathematical_confluence.context import market_context
from ..mathematical_confluence.oi_confluence import oi_matrix as _oi_matrix
from . import eligibility as _elig
from . import option_selector as _optsel
from . import selection_score as _ss
from .profiles import get_profile
from .universe import index_meta, resolve_universe

ENGINE_NAME = "SMART_INDEX_SCALPER"


class SmartIndexScalper:
    def __init__(self, *, engine: MathematicalConfluenceEngine | None = None,
                 filters: dict | None = None, selection_weights: dict | None = None,
                 profile: str | None = None):
        self.engine = engine or MathematicalConfluenceEngine()
        self.filters = filters
        self.selection_weights = selection_weights
        self.profile = get_profile(profile)

    # ------------------------------------------------------------------ scan
    def scan(self, symbols=None, *, use_cache: bool = True) -> dict:
        universe = resolve_universe(symbols)
        raw = []
        for sym in universe:
            meta = index_meta(sym)
            ctx = market_context(sym, use_cache=use_cache)
            pd = ctx.get("prev_day") or {}
            out = self.engine.evaluate(
                instrument=sym, timestamp="",
                prev_day=pd, today_open=ctx.get("today_open"),
                current_price=ctx.get("spot"),
                day_high=ctx.get("day_high"), day_low=ctx.get("day_low"),
                current_volume=ctx.get("current_volume"), avg_volume=ctx.get("avg_volume"),
                bars=ctx.get("bars"), chain=ctx.get("chain"),
                mom_3m=ctx.get("mom_3m"))
            oim = _oi_matrix(ctx.get("chain") or [], ctx.get("spot")) if ctx.get("chain") and ctx.get("spot") \
                else {"status": "DATA_INSUFFICIENT"}
            elig = _elig.evaluate_eligibility(ctx=ctx, engine_out=out, oi_matrix=oim,
                                              filters=self.filters)
            raw.append({"sym": sym, "meta": meta, "ctx": ctx, "engine": out,
                        "oi_matrix": oim, "eligibility": elig})

        # normalise liquidity (ATM OI) across the scan for the selection score
        atm_ois = []
        for r in raw:
            row = _atm_row(r["ctx"])
            r["_atm_oi"] = ((row or {}).get("ce_oi") or 0) + ((row or {}).get("pe_oi") or 0)
            atm_ois.append(r["_atm_oi"])
        lo, hi = (min(atm_ois), max(atm_ois)) if atm_ois else (0, 1)
        rng = (hi - lo) or 1e-9

        results = []
        for r in raw:
            comp = _ss.component_scores(ctx=r["ctx"], engine_out=r["engine"],
                                        oi_matrix=r["oi_matrix"],
                                        liquidity_norm=(r["_atm_oi"] - lo) / rng)
            sel = _ss.index_selection_score(comp, self.selection_weights)
            out = r["engine"]

            # SLICE 3 — pick the CE/PE contract for a directional, eligible setup
            selected_option = None
            if r["eligibility"]["eligible"] and out.get("direction") in ("CE", "PE") \
                    and out.get("signal_type") in ("BUY_CE", "BUY_PE"):
                tgt = out.get("target_1")
                spot = r["ctx"].get("spot")
                move = abs(tgt - spot) if (tgt is not None and spot is not None) else None
                selected_option = _optsel.select(
                    direction=out["direction"], spot=spot,
                    chain=r["ctx"].get("chain") or [],
                    atm=r["ctx"].get("atm"),
                    strike_step=float(r["meta"].get("strike_step", 50.0)),
                    expected_move_pts=move,
                    allowed_option_distance=int(self.profile.get("allowed_option_distance", 2)))

            results.append({
                "index": r["sym"],
                "status": out.get("status"),
                "eligible": r["eligibility"]["eligible"],
                "eligibility": r["eligibility"],
                "spot": r["ctx"].get("spot"),
                "market_regime": out.get("market_regime"),
                "direction": out.get("direction"),
                "signal_type": out.get("signal_type"),
                "confidence": out.get("confidence"),
                "confluence_score": out.get("confluence_score"),
                "risk_reward": out.get("risk_reward"),
                "components": comp,
                "score": sel["index_selection_score"],
                "score_breakdown": sel["breakdown"],
                "nearest_support": (out.get("nearest_support") or {}).get("center"),
                "nearest_resistance": (out.get("nearest_resistance") or {}).get("center"),
                "reason_codes": out.get("reason_codes"),
                "no_trade_reason": out.get("no_trade_reason"),
                "data_quality": r["ctx"].get("data_quality"),
                "missing": out.get("missing"),
                "selected_option": selected_option,
            })

        eligible = sorted([x for x in results if x["eligible"]],
                          key=lambda x: x["score"], reverse=True)
        ineligible = sorted([x for x in results if not x["eligible"]],
                            key=lambda x: x["score"], reverse=True)

        top = eligible[:3]
        return {
            "engine": ENGINE_NAME,
            "universe": universe,
            "ranked": eligible,
            "not_eligible": [{"index": x["index"], "failed": x["eligibility"]["failed"],
                             "status": x["status"], "score": x["score"],
                             "missing": x.get("missing")} for x in ineligible],
            "selection": {
                "primary": top[0] if len(top) >= 1 else None,
                "alternative_2": top[1] if len(top) >= 2 else None,
                "alternative_3": top[2] if len(top) >= 3 else None,
                "why_primary": _ss.explain_winner(eligible),
            },
            "profile": self.profile,
            "calibration": "UNCALIBRATED — selection weights are defaults, no backtest (section 26). "
                           "RESEARCH ONLY; no paper position opened by this layer.",
        }

    def signal_for(self, symbol: str, *, use_cache: bool = True) -> dict:
        """Full candidate signal for one index (the engine output + eligibility +
        selection score, no ranking)."""
        s = self.scan([symbol], use_cache=use_cache)
        return (s["ranked"] + [x for x in [s["selection"].get("primary")] if x]
                or s["not_eligible"] or [{"index": symbol.upper(), "status": "NO_RESULT"}])[0]


def _atm_row(ctx):
    chain = ctx.get("chain") or []
    atm = ctx.get("atm") or ctx.get("spot")
    if not chain or atm is None:
        return None
    return min(chain, key=lambda r: abs((r.get("strike") or 1e18) - atm))
