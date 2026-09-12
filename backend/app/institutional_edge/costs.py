"""
Itemized realistic transaction cost model -- section 12's "Include realistic:
fees, brokerage, spread, slippage, execution delay."

Deliberately narrow in what it claims to know. The live decision path
(engines/option_engine.py's ev_gate) already applies a rough, config-driven
`est_cost_r` haircut (a fraction-of-risk approximation) -- this module does
NOT replace that; it is the itemized, research-grade version this layer
needs for conditional-edge/EV analysis, built for reuse by ev.py.

Only two (exchange, segment) profiles are populated with real numbers, and
both are numbers this session ALREADY validated against real AngelOne
contract notes while killing two prior strategy hypotheses (see memory
`stochrsi-supertrend-nogo.md`) -- not invented here:

  MCX_NATURALGAS_OPTION : Rs113.50/lot round-trip (lot size 1250, brokerage
                          Rs40 flat + exchange txn Rs18.76 + CTT Rs36.09
                          [0.01% sell-side value] + SEBI/stamp+GST Rs18.65)
  MCX_CRUDEOIL_OPTION   : Rs184.50/lot round-trip (lot size 100, brokerage
                          Rs40 + exchange txn Rs38.86 + CTT Rs74.74
                          [0.01% sell-side value] + SEBI+stamp Rs16.44 +
                          GST Rs14.46)

For every OTHER (exchange, segment) combination -- NSE/BSE index options
included -- this module deliberately does NOT fabricate a brokerage/STT/GST
formula it hasn't verified against a real contract note. `estimate_cost()`
returns status="UNCALIBRATED" for those rather than guessing: an itemized
"realistic cost" section that quietly includes made-up numbers for the
instruments that matter most (NIFTY/BANKNIFTY) would be worse than an
honest gap, since the whole point of this layer is trustworthy net-of-cost
numbers.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class CostProfile:
    exchange: str
    segment: str
    lot_size: int
    brokerage: float
    exchange_txn: float
    ctt_or_stt: float
    sebi_stamp: float
    gst: float
    validated_note: str

    @property
    def round_trip_total(self) -> float:
        return round(self.brokerage + self.exchange_txn + self.ctt_or_stt
                     + self.sebi_stamp + self.gst, 2)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["round_trip_total"] = self.round_trip_total
        return d


# Keyed (exchange, segment) -> CostProfile. Add a new entry ONLY after
# validating it against a real contract note or broker cost calculator for
# that specific instrument -- see this file's module docstring.
KNOWN_COST_PROFILES: dict[tuple[str, str], CostProfile] = {
    ("MCX", "NATURALGAS_OPTION"): CostProfile(
        exchange="MCX", segment="NATURALGAS_OPTION", lot_size=1250,
        brokerage=40.0, exchange_txn=18.76, ctt_or_stt=36.09,
        # source audit reported SEBI/stamp + GST as one combined Rs18.65
        # remainder rather than itemizing them separately (unlike the
        # CrudeOil profile below, where the source DID split them) --
        # sebi_stamp carries the full remainder here so the total stays
        # correct; this is a labeling gap in the source, not a guess.
        sebi_stamp=18.65, gst=0.0,
        validated_note="AngelOne real contract-note audit, 2026-09-11 (stochrsi-supertrend-nogo)"),
    ("MCX", "CRUDEOIL_OPTION"): CostProfile(
        exchange="MCX", segment="CRUDEOIL_OPTION", lot_size=100,
        brokerage=40.0, exchange_txn=38.86, ctt_or_stt=74.74,
        sebi_stamp=16.44, gst=14.46,
        validated_note="AngelOne real contract-note audit, 2026-09-11 (stochrsi-supertrend-nogo)"),
}


@dataclass
class CostEstimate:
    status: str                     # "OK" | "UNCALIBRATED"
    exchange: str
    segment: str
    lot_size: int | None            # None if UNCALIBRATED
    round_trip_cost: float | None   # rupees per lot, None if UNCALIBRATED
    slippage_points: float          # caller-supplied, points on the underlying/option
    slippage_cost: float | None     # slippage_points * lot_size, None if lot_size unknown
    total_cost: float | None        # round_trip_cost + slippage_cost, None if UNCALIBRATED
    total_cost_points: float | None  # total_cost / lot_size -- same unit as scalp_signals' `points`
    note: str

    def to_dict(self) -> dict:
        return asdict(self)


def estimate_cost(exchange: str, segment: str, *, slippage_points: float = 0.0) -> CostEstimate:
    """Round-trip cost estimate for one lot. `slippage_points`: your own
    observed/assumed slippage in price points (caller's responsibility --
    this module has no execution data of its own to derive it from)."""
    key = (str(exchange or "").upper(), str(segment or "").upper())
    profile = KNOWN_COST_PROFILES.get(key)
    if profile is None:
        return CostEstimate(
            status="UNCALIBRATED", exchange=key[0], segment=key[1], lot_size=None,
            round_trip_cost=None, slippage_points=slippage_points,
            slippage_cost=None, total_cost=None, total_cost_points=None,
            note=f"no validated cost profile for {key} -- see module docstring; "
                 "add one only after checking a real contract note")
    slippage_cost = round(slippage_points * profile.lot_size, 2)
    total = round(profile.round_trip_total + slippage_cost, 2)
    return CostEstimate(
        status="OK", exchange=profile.exchange, segment=profile.segment, lot_size=profile.lot_size,
        round_trip_cost=profile.round_trip_total, slippage_points=slippage_points,
        slippage_cost=slippage_cost, total_cost=total,
        total_cost_points=round(total / profile.lot_size, 4),
        note=profile.validated_note)


def known_profiles() -> list[dict]:
    return [p.to_dict() for p in KNOWN_COST_PROFILES.values()]
