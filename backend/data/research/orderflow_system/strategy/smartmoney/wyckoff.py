"""
SM-4 -- Wyckoff Phase Classifier (primary Mode C workhorse). is_proxy=False
for the structural parts (range detection, spring/UTAD are just
liquidity_sweep events, BOS is just structure_shift) -- this is a composite
built entirely on SM-7 and SM-8's real price-structure outputs, not a
separate pattern-matching black box.

DISCLOSED SIMPLIFICATION (spec Section 2's "be honest, no mysticism"): full
Wyckoff schematics (Phase A-E, multiple springs/tests, SOS/LPS) are far
richer than this. What's implemented is the load-bearing skeleton the
rulebook's setups actually need: a trading range, a spring (liquidity sweep
of the range lows) => accumulation, an upthrust/UTAD (sweep of the range
highs) => distribution, and a confirming BOS in the spring/UTAD's own
direction => markup/markdown. No "Phase A/B/C/D/E" labels are claimed here
because this doesn't track the multi-test schematic those labels imply.

phase: "accumulation" | "distribution" | "markup" | "markdown" | "unknown"
"""
from __future__ import annotations

from . import liquidity_sweep, structure_shift
from .base import SMResult


def detect(df, i: int, cfg: dict) -> SMResult:
    range_lb = cfg["wyckoff_range_lookback"]
    max_height = cfg["wyckoff_range_max_height_pct"]
    min_bars = cfg["wyckoff_min_range_bars"]
    if i < range_lb or "range_height_pct" not in df.columns:
        return SMResult.absent(is_proxy=False)

    in_range_window = df["range_height_pct"].iloc[max(0, i - min_bars + 1):i + 1]
    if not (in_range_window <= max_height).all():
        return SMResult.absent(is_proxy=False)  # no sustained trading range as of bar i

    # look back within the range window for the most recent spring/UTAD,
    # then check for a confirming BOS at or after that bar, up to and
    # including bar i.
    start = max(0, i - range_lb)
    last_spring_i, last_utad_i = None, None
    for j in range(start, i + 1):
        sw = liquidity_sweep.detect(df, j, cfg)
        if sw.present and sw.detail["event"] == "swept_lows":
            last_spring_i = j
        elif sw.present and sw.detail["event"] == "swept_highs":
            last_utad_i = j

    def _confirmed_bos(from_i: int, direction: str) -> bool:
        for k in range(from_i + 1, i + 1):
            ss = structure_shift.detect(df, k, cfg)
            if ss.present and ss.direction == direction and ss.detail["event"] == "BOS":
                return True
        return False

    if last_spring_i is not None and (last_utad_i is None or last_spring_i > last_utad_i):
        if _confirmed_bos(last_spring_i, "bull"):
            return SMResult(present=True, direction="bull", strength=1.0, confidence=0.8, is_proxy=False,
                            detail={"phase": "markup", "spring_at": last_spring_i})
        return SMResult(present=True, direction="bull", strength=0.6, confidence=0.5, is_proxy=False,
                        detail={"phase": "accumulation", "spring_at": last_spring_i})
    if last_utad_i is not None:
        if _confirmed_bos(last_utad_i, "bear"):
            return SMResult(present=True, direction="bear", strength=1.0, confidence=0.8, is_proxy=False,
                            detail={"phase": "markdown", "utad_at": last_utad_i})
        return SMResult(present=True, direction="bear", strength=0.6, confidence=0.5, is_proxy=False,
                        detail={"phase": "distribution", "utad_at": last_utad_i})
    return SMResult.absent(is_proxy=False)
