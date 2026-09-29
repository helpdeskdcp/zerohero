"""
Shared contract for every SM-x detector (spec Section 2): "smart money" is
never directly observable -- every detector here INFERS it from order flow
+ price structure, and must say so. No magic pattern dictionary; every
threshold is a named config param (spec C3), and every detector is
independently testable and independently removable if it adds nothing
(spec Section 6's per-detector contribution table).

Every detector exposes exactly one function:
    detect(df, i, cfg) -> SMResult
using only df.iloc[:i+1] (bar i's own close and earlier) -- the SAME
no-look-ahead discipline as strategy/_common.py's context columns. A
detector that peeked at df.iloc[i+1:] would corrupt every setup gated on it;
test_smartmoney_no_lookahead.py checks this the same way
test_no_lookahead.py already does for setups 1-4.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SMResult:
    present: bool
    direction: str | None  # "bull" | "bear" | None
    strength: float        # 0..1, detector-specific magnitude
    confidence: float      # 0..1, detector-specific certainty
    is_proxy: bool         # True: this is an OHLCV-derived approximation, not a real observation
    detail: dict | None = None  # detector-specific extra fields (e.g. the gap's price bounds)

    @staticmethod
    def absent(is_proxy: bool) -> "SMResult":
        return SMResult(present=False, direction=None, strength=0.0, confidence=0.0, is_proxy=is_proxy)
