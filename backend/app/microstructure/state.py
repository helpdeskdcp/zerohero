"""
h1h7_state event -> section 13's own state vocabulary. Pure function, no I/O.

Mapping, one h1h7_state.classify_market_state() output at a time (see that
module's own docstring for the full state machine this reads from):

  H7_TRAP / H7_LEANING_TRAP  -> REJECTION
      A structural level was broken by an abnormal spike, then a completed
      close reclaimed back through it -- the textbook definition of a
      rejection (the break failed to hold).

  H1_CONT / H1_CONT_OBSERVE  -> BREAKOUT
      The level broke and held for 2 completed closes with reaction-candle
      agreement -- genuine structural acceptance beyond the level. (The two
      h1h7 variants differ only in whether continuation TRADING is
      considered validated for that symbol -- the underlying MICROSTRUCTURE
      fact, "broke and held," is identical, so both map to the same state
      here; that trading-validation distinction stays visible in
      `source_h1h7_state` and the attached raw event for anyone who needs it.)

  H1_CONT_BLOCKED            -> FAILED_BREAKOUT
      The same structural acceptance gate matched, but there was no
      available room (available_R) to call it a clean continuation --
      broke through but couldn't sustain it.

  SPIKE_NO_LEVEL             -> ABSORPTION, or BUYING_/SELLING_PRESSURE
      An abnormal-volume spike that displaced no prior structural level.
      h1h7_state itself computes `body_fraction` but deliberately does NOT
      use it as a trading gate ("informational only" per its own docstring)
      -- that doesn't mean it's uninformative for a DIFFERENT purpose. A
      small real body despite abnormal volume (no level, no directional
      resolution) reads as absorption: volume without progress. A
      decisively directional body reads as one-sided pressure in that
      direction. BODY_FRACTION_DIRECTIONAL_MIN=0.5 is the dividing line
      (a body at least half the bar's range) -- a plain, documented,
      round-number threshold, not fitted to any dataset.

  NEUTRAL                    -> BALANCED
      No abnormal spike at all -- nothing unusual to report.

  AMBIGUOUS / H1_WEAK / anything missing/unrecognized -> UNKNOWN
      The classifier itself couldn't commit to a clean read; this layer
      does not manufacture more certainty than the source data supports.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

STATES = ("BUYING_PRESSURE", "SELLING_PRESSURE", "ABSORPTION", "REJECTION",
          "BREAKOUT", "FAILED_BREAKOUT", "BALANCED", "UNKNOWN")

BODY_FRACTION_DIRECTIONAL_MIN = 0.5

_REJECTION_STATES = ("H7_TRAP", "H7_LEANING_TRAP")
_BREAKOUT_STATES = ("H1_CONT", "H1_CONT_OBSERVE")


@dataclass
class MicrostructureState:
    state: str
    reason_codes: list
    source_h1h7_state: str | None
    evidence: dict          # the raw h1h7 event fields this decision was based on

    def to_dict(self) -> dict:
        return asdict(self)


def classify(h1h7_event: dict) -> MicrostructureState:
    """`h1h7_event`: one element of orderflow.service.h1h7_state(...)['events']
    (or h1h7_state.classify_market_state()'s own direct output)."""
    s = h1h7_event.get("state")
    evidence = {k: h1h7_event.get(k) for k in
               ("state", "spike_direction", "broken_level", "broken_level_kind",
                "reclaim_distance_ratio", "reclaimed_within_3", "disp_atr",
                "available_R", "body_fraction", "range_pctile")}

    if s in _REJECTION_STATES:
        return MicrostructureState("REJECTION", [f"H1H7_STATE_{s}"], s, evidence)

    if s in _BREAKOUT_STATES:
        return MicrostructureState("BREAKOUT", [f"H1H7_STATE_{s}"], s, evidence)

    if s == "H1_CONT_BLOCKED":
        return MicrostructureState("FAILED_BREAKOUT", ["H1H7_STATE_H1_CONT_BLOCKED"], s, evidence)

    if s == "SPIKE_NO_LEVEL":
        bf = h1h7_event.get("body_fraction")
        direction = str(h1h7_event.get("spike_direction") or "").upper()
        if bf is None or bf < BODY_FRACTION_DIRECTIONAL_MIN:
            return MicrostructureState(
                "ABSORPTION", [f"SMALL_BODY_HIGH_VOLUME_NO_LEVEL_BODY_FRACTION_{bf}"], s, evidence)
        reasons = [f"DIRECTIONAL_SPIKE_NO_LEVEL_BODY_FRACTION_{bf}"]
        if direction == "UP":
            return MicrostructureState("BUYING_PRESSURE", reasons, s, evidence)
        if direction == "DOWN":
            return MicrostructureState("SELLING_PRESSURE", reasons, s, evidence)
        return MicrostructureState("UNKNOWN", reasons + ["NO_SPIKE_DIRECTION"], s, evidence)

    if s == "NEUTRAL":
        return MicrostructureState("BALANCED", ["H1H7_STATE_NEUTRAL_NO_ABNORMAL_ACTIVITY"], s, evidence)

    return MicrostructureState("UNKNOWN", [f"H1H7_STATE_{s or 'MISSING'}_INSUFFICIENT_EVIDENCE"],
                               s, evidence)
