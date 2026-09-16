"""
Entry Quality Filter: the 5-SMA entry hard rule (same pattern as
`app.strategy.ce_strategy`/`pe_strategy`), anti-chase/LATE_ENTRY detection,
the minimum-target-size filter ("avoid 5-point signals"), and pullback
health classification (HEALTHY vs STRUCTURAL_REVERSAL).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..strategy.base_strategy import IndicatorSnapshot
from .mtf_config import MTFConfig

HEALTHY_PULLBACK, STRUCTURAL_REVERSAL, NO_PULLBACK = "HEALTHY_PULLBACK", "STRUCTURAL_REVERSAL", "NO_PULLBACK"


@dataclass
class EntryQualityResult:
    passed: bool
    sma_entry_ok: bool
    late_entry: bool
    target_size_ok: bool
    pullback_status: str
    reasons: list

    def to_dict(self) -> dict:
        return asdict(self)


def _check_5sma_entry(direction: str, ind: IndicatorSnapshot) -> bool:
    if ind.sma_fast is None or ind.close is None:
        return False
    if direction == "BULLISH":
        return ind.close > ind.sma_fast and (ind.sma_fast_slope or 0) >= 0
    return ind.close < ind.sma_fast and (ind.sma_fast_slope or 0) <= 0


def _check_late_entry(direction: str, ind: IndicatorSnapshot, cfg: MTFConfig, *, lookback: int = 6) -> bool:
    """True == this looks like chasing a move that has already run too far."""
    if ind.atr in (None, 0) or ind.n <= lookback:
        return False
    move = ind.closes[-1] - ind.closes[-1 - lookback]
    if direction == "BULLISH":
        return move > cfg.late_entry_atr_mult * ind.atr
    return -move > cfg.late_entry_atr_mult * ind.atr


def check_min_target_size(entry: float, stop_loss: float, cfg: MTFConfig) -> bool:
    return abs(entry - stop_loss) >= cfg.min_target_points


def classify_pullback(direction: str, ind: IndicatorSnapshot, cfg: MTFConfig, *, lookback: int = 20) -> str:
    """Depth of the retracement from the most recent extreme IN the trend
    direction, relative to ATR. A shallow pullback is healthy (a normal
    part of a trend); one that has already retraced more than
    `pullback_healthy_max_atr_mult` ATRs reads as a structural-reversal
    risk, not a buyable/sellable dip."""
    if ind.atr in (None, 0) or ind.n < 3:
        return NO_PULLBACK
    window_highs = ind.highs[-lookback:] if ind.n >= lookback else ind.highs
    window_lows = ind.lows[-lookback:] if ind.n >= lookback else ind.lows
    price = ind.close
    if direction == "BULLISH":
        recent_high = max(window_highs)
        retrace = recent_high - price
        if retrace <= 0:
            return NO_PULLBACK
        return HEALTHY_PULLBACK if retrace <= cfg.pullback_healthy_max_atr_mult * ind.atr else STRUCTURAL_REVERSAL
    recent_low = min(window_lows)
    retrace = price - recent_low
    if retrace <= 0:
        return NO_PULLBACK
    return HEALTHY_PULLBACK if retrace <= cfg.pullback_healthy_max_atr_mult * ind.atr else STRUCTURAL_REVERSAL


def evaluate(direction: str, ind: IndicatorSnapshot, *, entry: float | None = None,
            stop_loss: float | None = None, cfg: MTFConfig | None = None) -> EntryQualityResult:
    cfg = cfg or MTFConfig()
    reasons = []

    sma_ok = _check_5sma_entry(direction, ind)
    if not sma_ok:
        reasons.append("wrong side of 5 SMA for entry")

    late = _check_late_entry(direction, ind, cfg)
    if late:
        reasons.append("LATE_ENTRY: move already extended beyond the chase threshold")

    target_ok = True
    if entry is not None and stop_loss is not None:
        target_ok = check_min_target_size(entry, stop_loss, cfg)
        if not target_ok:
            reasons.append(f"target size below minimum ({cfg.min_target_points} points)")

    pullback = classify_pullback(direction, ind, cfg)
    if pullback == STRUCTURAL_REVERSAL:
        reasons.append("pullback depth reads as a structural reversal risk")

    passed = sma_ok and not late and target_ok and pullback != STRUCTURAL_REVERSAL
    return EntryQualityResult(passed=passed, sma_entry_ok=sma_ok, late_entry=late,
                              target_size_ok=target_ok, pullback_status=pullback, reasons=reasons)
