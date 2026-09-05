"""
Shared open-interest-derived math used by more than one engine.

An architecture-review sweep for duplicated technical-analysis math found
ATR/VWAP/Gann/S-R were largely false positives (single shared implementation,
already imported everywhere -- see signal_engine._atr/_vwap and
mathematical_confluence/levels.py's own "reuses ... so there is one source
of truth" comment). max_pain was the one genuine case: the textbook formula
(for each candidate settle strike, sum ITM payout to option holders across
every strike weighted by OI; the strike that MINIMIZES this total is "max
pain") was independently re-derived, byte-for-byte identical including the
first-encountered-wins tie-break, in three places:
  - engines/oi_options_engine.py's run_oi_options_engine() (nested closure)
  - autoscalp/runner.py._chain_oi_quality()
  - engines/turning_point_engine.py._oi_metrics()

Verified equal before extracting; nothing about the surrounding PCR /
coverage / eligibility logic in any of those three callers changed -- only
the max_pain loop itself moved here.
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple


def max_pain_strike(rows: Iterable[Tuple[float, float, float]]) -> Optional[float]:
    """`rows`: iterable of (strike, ce_oi, pe_oi) tuples (OI already resolved
    to a float -- callers should turn a missing/None OI into 0.0 first, as
    all three original call sites did). Returns the strike that minimizes
    total option-holder payout (i.e. maximizes writer profit), or None if
    `rows` is empty. Ties are broken by the FIRST candidate strike
    encountered in `rows` order, matching all three original implementations.
    """
    rows = list(rows)
    best_k: Optional[float] = None
    best_pain = float("inf")
    for k, _ce, _pe in rows:
        pain = sum(max(0.0, k - ki) * ce + max(0.0, ki - k) * pe for ki, ce, pe in rows)
        if pain < best_pain:
            best_pain, best_k = pain, k
    return best_k
