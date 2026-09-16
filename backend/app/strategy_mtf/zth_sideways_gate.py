"""
The sideways/ZTH special case: in a genuinely RANGE-bound market, allow the
ZTH (Zero-to-Hero) sub-strategy ONLY when EVERY one of these holds --
otherwise NO_TRADE, exactly per the approved gate list:
  - regime is sideways/range (ADX below threshold)
  - a real FVG/imbalance exists
  - a sudden expansion candle just occurred
  - a direction is confirmed (by the LTF cascade's own read, even if it
    didn't clear the normal aggregate threshold -- that's the whole point
    of this being a SEPARATE, narrower exception path, not a backdoor)
  - volume/momentum supports the move
  - risk/reward is acceptable
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..strategy.base_strategy import IndicatorSnapshot
from .fvg_candle import ImbalanceConfirmation
from .mtf_config import MTFConfig


@dataclass
class ZTHGateResult:
    allowed: bool
    direction: str | None
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _is_sideways(ind: IndicatorSnapshot, cfg: MTFConfig) -> bool:
    return ind.adx is not None and ind.adx < cfg.sideways_adx_max


def _is_expansion_candle(ind: IndicatorSnapshot, cfg: MTFConfig) -> bool:
    if ind.atr in (None, 0) or ind.n < 1:
        return False
    last_range = ind.highs[-1] - ind.lows[-1]
    return last_range > cfg.zth_expansion_atr_mult * ind.atr


def evaluate(*, ind: IndicatorSnapshot, imbalance: ImbalanceConfirmation, candidate_direction: str | None,
            momentum_ok: bool, risk_reward: float | None, cfg: MTFConfig | None = None,
            min_rr: float = 1.5) -> ZTHGateResult:
    cfg = cfg or MTFConfig()

    if not _is_sideways(ind, cfg):
        return ZTHGateResult(allowed=False, direction=None, reason="regime is not sideways/range")
    if not imbalance.fvg_confirmed:
        return ZTHGateResult(allowed=False, direction=None, reason="no real FVG/imbalance detected")
    if not _is_expansion_candle(ind, cfg):
        return ZTHGateResult(allowed=False, direction=None, reason="no sudden expansion candle")
    if candidate_direction is None:
        return ZTHGateResult(allowed=False, direction=None, reason="no confirmed direction")
    if imbalance.fvg_direction is not None and imbalance.fvg_direction != candidate_direction:
        return ZTHGateResult(allowed=False, direction=None,
                             reason=f"imbalance direction {imbalance.fvg_direction} disagrees with "
                                   f"candidate direction {candidate_direction}")
    if not momentum_ok:
        return ZTHGateResult(allowed=False, direction=None, reason="volume/momentum does not support the move")
    if risk_reward is None or risk_reward < min_rr:
        return ZTHGateResult(allowed=False, direction=None,
                             reason=f"risk/reward {risk_reward} below the minimum {min_rr}")

    return ZTHGateResult(allowed=True, direction=candidate_direction,
                         reason="sideways market, real imbalance + expansion candle, direction confirmed, "
                               "momentum and R:R acceptable")
