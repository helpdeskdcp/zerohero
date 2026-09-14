"""
Setup score -- one explainable 0-100 number feeding the probability
calibration model (app.backtest.calibration expects a 0-100 score).

Same design choice as app.structural_break.break_score and
app.institutional_edge.edge_score, reused here rather than invented fresh:
a plain, equally-weighted COUNT of independent, named evidence checks
normalized to 0-100, not a hand-picked weighted formula. This score is
advisory input to calibration ONLY -- it never bypasses the MANDATORY gates
(sweep confirmed, structure break present, secondary confirmation present,
2R achievable, confirmation candle closed), which are checked separately
in engine.py and can never be overridden by a high score, per the brief's
own "do not allow an indicator score to override a mandatory rule."
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

_CHECKS = (
    "structure_aligned", "secondary_confirmation", "htf_aligned",
    "vwap_aligned", "ema_aligned", "rsi_not_extreme", "trending_adx", "volume_above_average",
)


@dataclass
class SetupScore:
    score_0_100: float
    max_checks: int
    passed_checks: int
    reasons: list

    def to_dict(self) -> dict:
        return asdict(self)


def compute(*, direction: str, structure_direction: str | None, secondary_confirmed: bool,
            htf_bias: str, indicators_snapshot: dict, avg_volume: float | None = None,
            sweep_bar_volume: float | None = None) -> SetupScore:
    """`direction`: "BULLISH" | "BEARISH" -- the candidate direction from
    the sweep. All checks are simple, named, and independently inspectable."""
    reasons = []
    passed = 0

    if structure_direction == direction:
        passed += 1; reasons.append("STRUCTURE_BREAK_ALIGNED")

    if secondary_confirmed:
        passed += 1; reasons.append("SECONDARY_CONFIRMATION_PRESENT")

    if htf_bias == direction:
        passed += 1; reasons.append("HTF_BIAS_ALIGNED")

    ind = indicators_snapshot or {}
    above_vwap = ind.get("above_vwap")
    if above_vwap is not None and ((direction == "BULLISH") == bool(above_vwap)):
        passed += 1; reasons.append("VWAP_ALIGNED")

    above_ema = ind.get("above_ema20")
    if above_ema is not None and ((direction == "BULLISH") == bool(above_ema)):
        passed += 1; reasons.append("EMA20_ALIGNED")

    rsi = ind.get("rsi14")
    if rsi is not None:
        # "not extreme against direction": bullish candidate should not be
        # deeply overbought already (>80), bearish should not be deeply
        # oversold already (<20) -- plain, documented round thresholds
        if (direction == "BULLISH" and rsi < 80) or (direction == "BEARISH" and rsi > 20):
            passed += 1; reasons.append("RSI_NOT_EXTREME")

    adx = ind.get("adx")
    if adx is not None and adx >= 20:
        passed += 1; reasons.append("ADX_TRENDING")

    if avg_volume and sweep_bar_volume and sweep_bar_volume > avg_volume:
        passed += 1; reasons.append("VOLUME_ABOVE_AVERAGE")

    score = round(100.0 * passed / len(_CHECKS), 1)
    return SetupScore(score_0_100=score, max_checks=len(_CHECKS), passed_checks=passed, reasons=reasons)
