"""
Previous-day High/Low as intraday support/resistance -- breakout / failed
breakout / rejection / reversal / retest detection. A touch is never
automatically a breakout: only a close beyond the level by a real
(ATR-scaled) margin counts, exactly per the approved architecture.

Reuses `app.liquidity_sweep.structure.pdh_pdl` for the level itself
(already tested, strictly-prior-session-only) rather than recomputing it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..liquidity_sweep.structure import pdh_pdl
from .mtf_config import MTFConfig

NONE, BREAKOUT_UP, BREAKOUT_DOWN = "NONE", "BREAKOUT_UP", "BREAKOUT_DOWN"
REJECTION_UP, REJECTION_DOWN = "REJECTION_UP", "REJECTION_DOWN"
FAILED_BREAKOUT_UP, FAILED_BREAKOUT_DOWN = "FAILED_BREAKOUT_UP", "FAILED_BREAKOUT_DOWN"
REVERSAL_UP, REVERSAL_DOWN = "REVERSAL_UP", "REVERSAL_DOWN"
RETEST_UP, RETEST_DOWN = "RETEST_UP", "RETEST_DOWN"

_BULLISH_EVENTS = {BREAKOUT_UP, REVERSAL_UP, RETEST_UP}
_BEARISH_EVENTS = {BREAKOUT_DOWN, REVERSAL_DOWN, RETEST_DOWN}


@dataclass
class LevelEvent:
    event: str
    direction: str          # BULLISH | BEARISH | NEUTRAL
    level: float | None
    level_kind: str | None  # "PDH" | "PDL"
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def _confirmed_close_beyond(bar: dict, level: float, atr: float | None, cfg: MTFConfig, *, up: bool) -> bool:
    margin = cfg.breakout_confirm_atr_mult * (atr or 0.0)
    return bar["c"] > level + margin if up else bar["c"] < level - margin


def _wicked_beyond(bar: dict, level: float, *, up: bool) -> bool:
    return bar["h"] > level if up else bar["l"] < level


def detect(bars_5m: list[dict], *, as_of_session: str, atr: float | None, cfg: MTFConfig | None = None) -> LevelEvent:
    """`bars_5m`: causal bars up to and including the current, decision bar.
    Classifies the CURRENT bar's relationship to yesterday's H/L, looking
    back at most `cfg.retest_window_bars` prior bars of the CURRENT session
    to tell a fresh breakout apart from a failed one / a retest / a
    reversal of an earlier break."""
    cfg = cfg or MTFConfig()
    levels = pdh_pdl(bars_5m, as_of_session=as_of_session)
    if levels.status != "OK" or not bars_5m:
        return LevelEvent(event=NONE, direction="NEUTRAL", level=None, level_kind=None,
                          reason="no prior session levels available")

    today_bars = [b for b in bars_5m if str(b.get("t") or "")[:10] == as_of_session]
    if not today_bars:
        return LevelEvent(event=NONE, direction="NEUTRAL", level=None, level_kind=None,
                          reason="no bars yet in the current session")

    window = today_bars[-cfg.retest_window_bars:]
    current = window[-1]
    prior = window[:-1]

    # -- was there an EARLIER confirmed breakout this session? Checked BEFORE
    # the plain fresh-breakout check below, so a full reversal (broke one
    # level earlier, now confirms beyond the OPPOSITE level) is reported as
    # REVERSAL, the more informative label -- not masked as a plain fresh
    # breakout of the second level. --
    prior_break_up = next((b for b in reversed(prior) if _confirmed_close_beyond(b, levels.prev_high, atr, cfg, up=True)), None)
    prior_break_down = next((b for b in reversed(prior) if _confirmed_close_beyond(b, levels.prev_low, atr, cfg, up=False)), None)

    if prior_break_up is not None and _confirmed_close_beyond(current, levels.prev_low, atr, cfg, up=False):
        return LevelEvent(event=REVERSAL_DOWN, direction="BEARISH", level=levels.prev_high, level_kind="PDH",
                          reason="earlier breakout above PDH has fully reversed through PDL")
    if prior_break_down is not None and _confirmed_close_beyond(current, levels.prev_high, atr, cfg, up=True):
        return LevelEvent(event=REVERSAL_UP, direction="BULLISH", level=levels.prev_low, level_kind="PDL",
                          reason="earlier breakdown below PDL has fully reversed through PDH")

    # -- fresh signal on the CURRENT bar --
    if _confirmed_close_beyond(current, levels.prev_high, atr, cfg, up=True):
        return LevelEvent(event=BREAKOUT_UP, direction="BULLISH", level=levels.prev_high, level_kind="PDH",
                          reason=f"close {current['c']} confirmed beyond PDH {levels.prev_high}")
    if _confirmed_close_beyond(current, levels.prev_low, atr, cfg, up=False):
        return LevelEvent(event=BREAKOUT_DOWN, direction="BEARISH", level=levels.prev_low, level_kind="PDL",
                          reason=f"close {current['c']} confirmed beyond PDL {levels.prev_low}")

    # -- a wick beyond the level that closed back inside THIS bar = rejection --
    if _wicked_beyond(current, levels.prev_high, up=True) and current["c"] <= levels.prev_high:
        return LevelEvent(event=REJECTION_UP, direction="BEARISH", level=levels.prev_high, level_kind="PDH",
                          reason="wicked above PDH, closed back below -- rejection")
    if _wicked_beyond(current, levels.prev_low, up=False) and current["c"] >= levels.prev_low:
        return LevelEvent(event=REJECTION_DOWN, direction="BULLISH", level=levels.prev_low, level_kind="PDL",
                          reason="wicked below PDL, closed back above -- rejection")

    # (a full reversal -- current bar confirmed beyond the OPPOSITE level --
    # was already handled above and returned; anything reaching here with a
    # prior break is either a failed breakout or a retest.)
    if prior_break_up is not None:
        if current["c"] < levels.prev_high:
            return LevelEvent(event=FAILED_BREAKOUT_UP, direction="BEARISH", level=levels.prev_high, level_kind="PDH",
                              reason="earlier breakout above PDH closed back below it")
        if abs(current["c"] - levels.prev_high) <= cfg.level_touch_buffer_atr_mult * (atr or 0.0):
            return LevelEvent(event=RETEST_UP, direction="BULLISH", level=levels.prev_high, level_kind="PDH",
                              reason="pulled back to retest PDH from above after a confirmed breakout")

    if prior_break_down is not None:
        if current["c"] > levels.prev_low:
            return LevelEvent(event=FAILED_BREAKOUT_DOWN, direction="BULLISH", level=levels.prev_low, level_kind="PDL",
                              reason="earlier breakdown below PDL closed back above it")
        if abs(current["c"] - levels.prev_low) <= cfg.level_touch_buffer_atr_mult * (atr or 0.0):
            return LevelEvent(event=RETEST_DOWN, direction="BEARISH", level=levels.prev_low, level_kind="PDL",
                              reason="pulled back to retest PDL from below after a confirmed breakdown")

    return LevelEvent(event=NONE, direction="NEUTRAL", level=None, level_kind=None,
                      reason="no breakout/rejection/retest/reversal against PDH/PDL right now")
