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

For every OTHER (exchange, segment) combination -- `estimate_cost()` returns
status="UNCALIBRATED" rather than guessing, UNLESS it's an NSE index option
(NIFTY/BANKNIFTY/FINNIFTY), where `estimate_index_option_cost()` (bottom of
this file) computes a genuine formula from published, currently-verified
NSE/Angel One regulatory rates instead -- status "FORMULA_ESTIMATE", a
distinct, explicitly lower-confidence tier than "OK", never conflated with
a real observed contract note. See that function's docstring for the full
reasoning (index-option premiums span too wide a range for one flat
rupee number the way MCX's do).
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


# ---------------------------------------------------------------------------
# NSE index options (NIFTY / BANKNIFTY / FINNIFTY) -- FORMULA_ESTIMATE, never
# "OK". The MCX profiles above are flat rupee amounts because they were read
# straight off one real contract note at one observed premium -- appropriate
# there since MCX_NATURALGAS/CRUDEOIL trade in a narrow premium band. NSE
# index options don't: STT/exchange-txn/stamp-duty/SEBI-fee are all ad
# valorem (a % of premium value), and a NIFTY/BANKNIFTY premium can be Rs5 or
# Rs500 depending on strike/expiry -- forcing that into one flat number would
# either pick an arbitrary reference premium or be wrong across the range.
# So this is a genuine formula over the trade's ACTUAL entry/exit premium,
# built from PUBLISHED, verifiable NSE/Angel One regulatory rates (verified
# live 2026-09-28, sources in RegulatoryRateCard.source below) -- not from an
# observed contract note the way the MCX numbers are. Never claims "OK"
# status; a caller must not treat this the same as a contract-note-validated
# profile.
# ---------------------------------------------------------------------------
@dataclass
class RegulatoryRateCard:
    """Published NSE F&O regulatory + Angel One brokerage rates for equity
    index options, current as of the verification date below. These rates
    DO change -- e.g. Budget 2026-27 raised options STT (sell side) from
    0.10% to 0.15% of premium, effective 2026-04-01 -- re-verify against
    Angel One's live rate card (angelone.in/exchange-transaction-charges)
    before trusting this for a real capital decision."""
    brokerage_per_order: float = 20.0            # flat, Rs, per executed order (both legs)
    exchange_txn_pct: float = 0.0355299 / 100    # both buy+sell, on premium value
    stt_sell_pct: float = 0.15 / 100             # SELL side only, on premium value (post 2026-04-01)
    stamp_duty_buy_pct: float = 0.003 / 100      # BUY side only, on premium value
    sebi_turnover_pct: float = 0.0001 / 100      # both sides, on premium value (Rs10/crore)
    gst_pct: float = 0.18                        # on (brokerage + exchange_txn + sebi_fee) only --
                                                  # STT and stamp duty are themselves taxes, GST does not stack on them
    verified_on: str = "2026-09-28"
    source: str = ("Angel One official rate card (angelone.in/exchange-transaction-charges) "
                   "for brokerage/exchange-txn/SEBI-fee/stamp-duty; ICICI Direct STT FAQ for the "
                   "Budget 2026-27 options-STT revision (0.10%->0.15% sell-side, eff. 2026-04-01). "
                   "Verified via live web search, NOT a real observed contract note -- see module "
                   "docstring for why this is FORMULA_ESTIMATE, not OK/validated.")


def estimate_index_option_cost(entry_premium: float, exit_premium: float, lot_size: int, *,
                               lots: int = 1, slippage_points: float = 0.75,
                               rates: RegulatoryRateCard | None = None) -> dict:
    """Round-trip cost for one BUY-then-SELL index-option trade (the only
    path this system's paper trades take -- long CE/PE, never a naked
    short), computed from real premiums, not a flat reference number.

    `slippage_points` defaults to 0.75 -- the midpoint of the disclosed
    0.5-1.0 point/leg range this was asked to use, since this system has no
    real fill-vs-quote execution data of its own for NIFTY/BANKNIFTY yet to
    derive a better number from (same "caller's responsibility" stance as
    estimate_cost() above).

    Works for NIFTY, BANKNIFTY, FINNIFTY, or any other NSE index option --
    the regulatory rate structure is segment-wide, not index-specific; only
    lot_size/premium differ, and the caller supplies those. FINNIFTY has no
    VERIFIED lot size in app.instrument_profiles yet -- pass a real one, do
    not guess."""
    rates = rates or RegulatoryRateCard()
    entry_value = entry_premium * lot_size * lots
    exit_value = exit_premium * lot_size * lots

    brokerage = rates.brokerage_per_order * 2  # one order to open, one to close
    exchange_txn = (entry_value + exit_value) * rates.exchange_txn_pct
    stt = exit_value * rates.stt_sell_pct
    stamp_duty = entry_value * rates.stamp_duty_buy_pct
    sebi_fee = (entry_value + exit_value) * rates.sebi_turnover_pct
    gst = (brokerage + exchange_txn + sebi_fee) * rates.gst_pct
    slippage_cost = slippage_points * lot_size * lots

    total = round(brokerage + exchange_txn + stt + stamp_duty + sebi_fee + gst + slippage_cost, 2)
    denom = lot_size * lots
    return {
        "status": "FORMULA_ESTIMATE",
        "exchange": "NSE", "segment": "INDEX_OPTION",
        "entry_premium": entry_premium, "exit_premium": exit_premium,
        "lot_size": lot_size, "lots": lots,
        "brokerage": round(brokerage, 2), "exchange_txn": round(exchange_txn, 2),
        "stt": round(stt, 2), "stamp_duty": round(stamp_duty, 2), "sebi_fee": round(sebi_fee, 2),
        "gst": round(gst, 2), "slippage_points": slippage_points, "slippage_cost": round(slippage_cost, 2),
        "total_cost": total,
        "total_cost_points": round(total / denom, 4) if denom else None,
        "note": rates.source,
    }
