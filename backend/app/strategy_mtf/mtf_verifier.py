"""
MTFVerifier -- the top-level orchestrator. Ties the whole approved pipeline
together:

  aggregate (Monthly..5m, weighted, monthly-ceiling)
      -> prev-day-level contradiction check
      -> FVG/inside-candle confirmation boost (never overrides)
      -> entry-quality filter (5-SMA entry, anti-chase, min target, pullback)
      -> SL/target plan (noise-clearing SL, staged R-multiple targets)
      -> sideways/ZTH special case (only reached if the normal path says NO_TRADE)
      -> FINAL VALIDATED SIGNAL -> Telegram (existing dispatcher, not a new one)

RAW SIGNAL != FINAL SIGNAL: nothing here treats the aggregate's CE/PE call
as tradeable on its own -- every downstream gate can still veto it down to
NO_TRADE, and only a signal that survives ALL of them is ever dispatched.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .. import telegram_dispatcher
from ..strategy.base_strategy import MarketFeatures, build_indicator_snapshot
from ..strategy.config import StrategyConfig
from .entry_quality import evaluate as evaluate_entry_quality
from .fvg_candle import evaluate as evaluate_fvg
from .mtf_aggregator import AggregateResult, compute_aggregate
from .mtf_config import MTFConfig
from .prev_day_levels import detect as detect_prev_day_level
from .target_stop import TargetStopPlan, build_plan
from .zth_sideways_gate import evaluate as evaluate_zth


@dataclass
class MTFSignal:
    symbol: str
    timestamp: str
    decision: str                  # "BUY_CE" | "BUY_PE" | "NO_TRADE"
    status: str
    aggregate_score: float
    monthly_bias: str
    per_timeframe: dict
    prev_day_event: str
    fvg_confirmed: bool
    entry_quality_passed: bool
    entry: float | None
    stop_loss: float | None
    targets: dict | None
    reason: str
    is_zth_signal: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def verify(symbol: str, bars_5m: list[dict], *, as_of_ts: str, session: str,
          mtf_cfg: MTFConfig | None = None, strategy_cfg: StrategyConfig | None = None,
          send_telegram: bool = False) -> MTFSignal:
    mtf_cfg = mtf_cfg or MTFConfig()
    strategy_cfg = strategy_cfg or StrategyConfig(sma_fast=mtf_cfg.sma_fast, sma_slow=mtf_cfg.sma_slow,
                                                  slope_lookback=mtf_cfg.slope_lookback)

    agg: AggregateResult = compute_aggregate(bars_5m, as_of_ts=as_of_ts, cfg=mtf_cfg, strategy_cfg=strategy_cfg)
    ind = build_indicator_snapshot(MarketFeatures(bars=bars_5m), strategy_cfg)
    level_event = detect_prev_day_level(bars_5m, as_of_session=session, atr=(ind.atr if ind else None), cfg=mtf_cfg)

    def _final(decision, status, reason, *, entry=None, stop_loss=None, targets=None,
              entry_quality_passed=False, is_zth=False) -> MTFSignal:
        return MTFSignal(
            symbol=symbol, timestamp=as_of_ts, decision=decision, status=status,
            aggregate_score=agg.aggregate_score, monthly_bias=agg.monthly_bias,
            per_timeframe=agg.per_timeframe, prev_day_event=level_event.event,
            fvg_confirmed=False, entry_quality_passed=entry_quality_passed,
            entry=entry, stop_loss=stop_loss, targets=targets, reason=reason, is_zth_signal=is_zth,
        )

    if agg.direction == "NO_TRADE":
        if ind is not None:
            candidate_dir = "BULLISH" if agg.bull_score >= agg.bear_score else "BEARISH"
            imbalance = evaluate_fvg(bars_5m, candidate_direction=candidate_dir)
            momentum_ok = (ind.rsi or 50) > 50 if candidate_dir == "BULLISH" else (ind.rsi or 50) < 50
            eq = evaluate_entry_quality(candidate_dir, ind, cfg=mtf_cfg)
            plan: TargetStopPlan | None = None
            rr = None
            if ind.atr:
                structure = level_event.level if level_event.level is not None else (
                    min(ind.lows[-20:]) if candidate_dir == "BULLISH" else max(ind.highs[-20:]))
                plan = build_plan(ind.close, candidate_dir, structure, bars_5m, ind.atr, cfg=mtf_cfg)
                rr = plan.r_unit and (plan.targets[mtf_cfg.r_multiple_stages[1]] - plan.entry) / plan.r_unit
            zth = evaluate_zth(ind=ind, imbalance=imbalance, candidate_direction=candidate_dir,
                               momentum_ok=momentum_ok, risk_reward=abs(rr) if rr else None, cfg=mtf_cfg)
            if zth.allowed and eq.passed:
                decision = "BUY_CE" if candidate_dir == "BULLISH" else "BUY_PE"
                sig = _final(decision, "ZTH_SIDEWAYS_SIGNAL", zth.reason,
                            entry=plan.entry if plan else None, stop_loss=plan.stop_loss if plan else None,
                            targets=plan.targets if plan else None, entry_quality_passed=True, is_zth=True)
                sig.fvg_confirmed = imbalance.fvg_confirmed
                if send_telegram:
                    _send(sig)
                return sig
        return _final("NO_TRADE", agg.status, agg.reason)

    candidate_dir = "BULLISH" if agg.direction == "CE" else "BEARISH"

    # -- prev-day-level contradiction check --
    contradicting = {"BULLISH": ("BREAKOUT_DOWN", "REVERSAL_DOWN", "REJECTION_UP", "FAILED_BREAKOUT_UP"),
                     "BEARISH": ("BREAKOUT_UP", "REVERSAL_UP", "REJECTION_DOWN", "FAILED_BREAKOUT_DOWN")}
    if level_event.event in contradicting[candidate_dir]:
        return _final("NO_TRADE", "PREV_DAY_LEVEL_CONFLICT",
                      f"aggregate says {agg.direction} but prev-day level shows {level_event.event}: "
                      f"{level_event.reason}")

    if ind is None:
        return _final("NO_TRADE", "INSUFFICIENT_DATA", "insufficient 5m history for entry evaluation")

    imbalance = evaluate_fvg(bars_5m, candidate_direction=candidate_dir)
    eq = evaluate_entry_quality(candidate_dir, ind, cfg=mtf_cfg)
    if not eq.passed:
        sig = _final("NO_TRADE", "ENTRY_QUALITY_FAILED", "; ".join(eq.reasons) or "entry quality filter failed",
                    entry_quality_passed=False)
        sig.fvg_confirmed = imbalance.fvg_confirmed
        return sig

    if not ind.atr:
        return _final("NO_TRADE", "NO_VOLATILITY_DATA", "cannot size SL/targets without ATR",
                      entry_quality_passed=True)

    structure = level_event.level if level_event.level is not None else (
        min(ind.lows[-20:]) if candidate_dir == "BULLISH" else max(ind.highs[-20:]))
    plan = build_plan(ind.close, candidate_dir, structure, bars_5m, ind.atr, cfg=mtf_cfg)

    if not eq.target_size_ok:
        return _final("NO_TRADE", "TARGET_TOO_SMALL", "1R target below the configured minimum size",
                      entry_quality_passed=True)

    decision = "BUY_CE" if candidate_dir == "BULLISH" else "BUY_PE"
    sig = _final(decision, agg.status, f"{agg.reason}; entry quality passed; "
                f"{'FVG confirms' if imbalance.fvg_confirmed else 'no FVG'}",
                entry=plan.entry, stop_loss=plan.stop_loss, targets=plan.targets, entry_quality_passed=True)
    sig.fvg_confirmed = imbalance.fvg_confirmed
    if send_telegram:
        _send(sig)
    return sig


def _format_telegram(sig: MTFSignal) -> str:
    lines = [
        f"MTF CASCADE {'(ZTH) ' if sig.is_zth_signal else ''}{sig.decision}",
        f"Symbol: {sig.symbol}",
        f"Aggregate score: {sig.aggregate_score} | Monthly bias: {sig.monthly_bias}",
        f"Status: {sig.status}",
    ]
    if sig.entry is not None:
        lines.append(f"Entry: {sig.entry} | SL: {sig.stop_loss}")
        if sig.targets:
            t = sig.targets
            lines.append("Targets: " + ", ".join(f"{k}R={v}" for k, v in sorted(t.items())))
    lines.append(f"Reason: {sig.reason}")
    return "\n".join(lines)


def _send(sig: MTFSignal) -> dict:
    direction = "BULLISH" if sig.decision == "BUY_CE" else "BEARISH"
    return telegram_dispatcher.dispatch(source_engine="MTF_CASCADE", underlying=sig.symbol,
                                        direction=direction, text=_format_telegram(sig))
