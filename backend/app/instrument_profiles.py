"""
Phase F -- Instrument Profile & Calibration Engine.

Formalizes the per-symbol overrides that already existed informally as
`app.autoscalp.runner.DEFAULT_CONFIG["symbol_profiles"]` into a governed
registry where every parameter carries a source, a rationale, and a
validation status -- instead of being an unexplained magic number sitting
in a config dict.

Deliberately additive, not a rewrite: `build_symbol_profiles_config()`
reproduces the EXACT dict shape runner.py has always consumed, so wiring
it in is a single-source-of-truth change with byte-identical behavior
(verified in tests/test_instrument_profiles.py), not a strategy change.
No signal-generation math, threshold, or weight was altered by this module.

Every value here traces to one of:
  VERIFIED    -- confirmed against a real, independent source (e.g. the
                 AngelOne instrument master, a validated cost profile).
  DERIVED     -- calculated/calibrated from a stated, real, cited sample
                 of live closed trades (sample size given).
  DEFAULT     -- the common base config, used because no instrument-
                 specific evidence justified deviating from it yet.
  UNVALIDATED -- a value exists but the evidence behind it is known-thin,
                 known-contaminated, or otherwise not yet trustworthy.
  UNKNOWN     -- no source at all; only ever used as a last-resort fallback.

See ZEROHERO_PHASE_F_INSTRUMENT_PROFILES.md for the full writeup.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ParamStatus(str, Enum):
    VERIFIED = "VERIFIED"
    DERIVED = "DERIVED"
    DEFAULT = "DEFAULT"
    UNVALIDATED = "UNVALIDATED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Param:
    """One governed parameter value. `value` is what the strategy actually
    uses; everything else is the audit trail for why it's trusted (or not)."""
    value: object
    status: ParamStatus
    source: str
    rationale: str

    def to_dict(self) -> dict:
        return {"value": self.value, "status": self.status.value,
                "source": self.source, "rationale": self.rationale}


# ---------------------------------------------------------------- base defaults
# The common-core values every instrument falls back to. These are the exact
# defaults already hardcoded in scalp_strategy.py::_plan_from_leg /
# option_engine.py::ev_gate -- restated here as governed Params, not new
# numbers. NIFTY runs on these UNCHANGED (see its profile below).
_BASE = {
    "sl_atr": Param(1.1, ParamStatus.DERIVED,
                    "app/engines/scalp_strategy.py::_plan_from_leg default",
                    "P6 backtest-harness-validated base geometry (app/backtest/runner.py); "
                    "NIFTY's live win-rate has historically been the best of the watchlist "
                    "on this exact configuration -- see runner.py DEFAULT_CONFIG comment "
                    "'NIFTY is DELIBERATELY absent -> it runs on the P6-validated defaults'."),
    "t1_atr": Param(1.7, ParamStatus.DERIVED,
                    "app/engines/scalp_strategy.py::_plan_from_leg default",
                    "Same P6-validated base geometry as sl_atr."),
    "t2_atr": Param(2.6, ParamStatus.DERIVED,
                    "app/engines/scalp_strategy.py::_plan_from_leg default",
                    "Same P6-validated base geometry as sl_atr."),
    "trail_atr": Param(0.9, ParamStatus.DERIVED,
                       "app/engines/scalp_strategy.py::_plan_from_leg default",
                       "Same P6-validated base geometry as sl_atr."),
    "max_hold_sec": Param(1500, ParamStatus.DEFAULT,
                         "app/autoscalp/runner.py base strategy config",
                         "Common scalp time-stop; no instrument-specific evidence "
                         "justified a different value for the base case."),
    "min_ev_r": Param(0.12, ParamStatus.DEFAULT,
                      "app/engines/option_engine.py::ev_gate default", "Common EV floor."),
    "rr_min": Param(1.3, ParamStatus.DEFAULT,
                    "app/engines/option_engine.py::ev_gate default", "Common RR floor."),
    "est_cost_r": Param(0.0, ParamStatus.DEFAULT,
                        "app/engines/scalp_strategy.py cost haircut default",
                        "NIFTY weeklies are the tightest-spread instrument on the "
                        "watchlist; 0 is the documented starting assumption, not a "
                        "validated real-cost measurement (see cost_model_status)."),
}


@dataclass
class InstrumentProfile:
    symbol: str
    exchange: str
    segment: str
    lot_size: Param
    params: dict            # name -> Param, e.g. "sl_atr" -> Param(...)
    cost_model_status: str  # "OK" | "UNCALIBRATED" -- from institutional_edge.costs
    validation_status: ParamStatus   # overall rollup for this profile
    sample_note: str        # human-readable evidence summary

    def get(self, name: str):
        p = self.params.get(name)
        return p.value if p is not None else _BASE[name].value

    def to_dict(self) -> dict:
        merged = {k: (self.params.get(k) or v).to_dict() for k, v in _BASE.items()}
        return {
            "symbol": self.symbol, "exchange": self.exchange, "segment": self.segment,
            "lot_size": self.lot_size.to_dict(),
            "params": merged,
            "cost_model_status": self.cost_model_status,
            "validation_status": self.validation_status.value,
            "sample_note": self.sample_note,
        }


def _cost_status(underlying: str) -> str:
    from .institutional_edge import costs as _costs
    est = _costs.estimate_cost("MCX" if underlying in ("NATURALGAS", "CRUDEOIL") else "",
                               f"{underlying}_OPTION")
    return est.status


# ---------------------------------------------------------------- registry
# Every value below traces to app/autoscalp/runner.py::DEFAULT_CONFIG's
# existing symbol_profiles / comments -- restated as governed Params, not
# invented. Lot sizes are the exchange-published contract sizes (verified
# against the captured AngelOne instrument master where noted).
REGISTRY: dict[str, InstrumentProfile] = {
    "NIFTY": InstrumentProfile(
        symbol="NIFTY", exchange="NSE", segment="NIFTY_OPTION",
        lot_size=Param(65, ParamStatus.VERIFIED,
                       "app.instruments.master_rows() (live AngelOne instrument master)",
                       "Read directly from captured instrument master, 2026-09-19."),
        params={},   # no overrides -- runs on _BASE exactly, by deliberate design
        cost_model_status=_cost_status("NIFTY"),
        validation_status=ParamStatus.DEFAULT,
        sample_note=("32 real closed AUTOSCALP paper trades (2026-08-31..09-18): "
                     "win_rate=0.469, expectancy=-1.4016 pts (gross, UNCALIBRATED cost). "
                     "n=32 clears the EV-gate empirical-sample floor (30) but the "
                     "underlying ATR/EV thresholds themselves were never re-tuned from "
                     "this sample -- see ZEROHERO_TRADING_EDGE_VALIDATION_2026-09-19.md."),
    ),
    "BANKNIFTY": InstrumentProfile(
        symbol="BANKNIFTY", exchange="NSE", segment="BANKNIFTY_OPTION",
        lot_size=Param(30, ParamStatus.VERIFIED,
                       "app.instruments.master_rows() (live AngelOne instrument master)",
                       "Read directly from captured instrument master, 2026-09-19."),
        params={},   # no overrides -- explicitly noted in runner.py as "too thin a
                     # sample to calibrate individually yet" when last reviewed (n=6)
        cost_model_status=_cost_status("BANKNIFTY"),
        validation_status=ParamStatus.UNVALIDATED,
        sample_note=("35 real closed AUTOSCALP paper trades (2026-08-31..09-18): "
                     "win_rate=0.571, expectancy=+1.2371 pts (gross, UNCALIBRATED cost) "
                     "-- the only symbol with positive gross expectancy in the current "
                     "sample. NOT tuned from this: n=35 is thin, no cost model exists to "
                     "confirm it survives realistic costs, and the Phase E adversarial "
                     "review found no basis yet to distinguish this from noise. Runs on "
                     "_BASE defaults, same as NIFTY, pending more evidence."),
    ),
    "SENSEX": InstrumentProfile(
        symbol="SENSEX", exchange="BSE", segment="SENSEX_OPTION",
        lot_size=Param(20, ParamStatus.VERIFIED,
                       "app.instruments.master_rows() (live AngelOne instrument master)",
                       "Read directly from captured instrument master, 2026-09-19."),
        params={},
        cost_model_status=_cost_status("SENSEX"),
        validation_status=ParamStatus.UNVALIDATED,
        sample_note=("15 real closed AUTOSCALP paper trades, ALL contaminated -- see "
                     "app.autoscalp.trade_contamination. Every row is a TIME_NODATA "
                     "sweep (entry==exit_price) from an exchange-routing bug fixed in "
                     "commits 8023607 and a910e1c (2026-09-18). No valid post-fix "
                     "SENSEX trade exists yet. This sample must NOT be used for "
                     "performance or calibration conclusions."),
    ),
    "NATURALGAS": InstrumentProfile(
        symbol="NATURALGAS", exchange="MCX", segment="NATURALGAS_OPTION",
        lot_size=Param(1250, ParamStatus.VERIFIED,
                       "app/institutional_edge/costs.py KNOWN_COST_PROFILES "
                       "(validated against a real AngelOne contract note, 2026-09-11)",
                       "Cross-checked against the real, validated cost profile."),
        params={
            "max_hold_sec": Param(1800, ParamStatus.DERIVED,
                                  "app/autoscalp/runner.py symbol_profiles.NATURALGAS",
                                  "Calibrated 2026-09-04 from 74 real closed AUTOSCALP "
                                  "trades (2026-08-31..09-04): winners' MFE ran to a "
                                  "median 3.1x ATR while the base config exited earlier."),
            "min_ev_r": Param(0.15, ParamStatus.DERIVED,
                              "app/autoscalp/runner.py symbol_profiles.NATURALGAS",
                              "Same 2026-09-04/74-trade calibration; MCX commodities "
                              "trend longer, given a stricter EV bar to compensate."),
            "rr_min": Param(1.4, ParamStatus.DERIVED,
                            "app/autoscalp/runner.py symbol_profiles.NATURALGAS",
                            "Same 2026-09-04/74-trade calibration."),
            "est_cost_r": Param(0.10, ParamStatus.DERIVED,
                               "app/autoscalp/runner.py symbol_profiles.NATURALGAS",
                               "~0.1R round-trip approximation for the NG option spread, "
                               "pre-trade heuristic only -- distinct from the validated "
                               "real per-lot cost model used in realized net P&L."),
            "trail_atr": Param(1.6, ParamStatus.DERIVED,
                              "app/autoscalp/runner.py symbol_profiles.NATURALGAS",
                              "Loosened from the base 0.9 -- TRAIL exits were realising "
                              "only ~1.3 pts avg vs winners' available median 3.1x-ATR "
                              "move; 1.6 still sits under that median (keeps real "
                              "protection) but stops choking a trade mid-run."),
        },
        cost_model_status=_cost_status("NATURALGAS"),
        validation_status=ParamStatus.DERIVED,
        sample_note=("57 real closed AUTOSCALP paper trades (2026-08-31..09-18): "
                     "win_rate=0.386, expectancy=-0.0289 pts gross; CALIBRATED cost "
                     "model (Rs113.50/lot round-trip) -> net=-6.83 pts total. Roughly "
                     "flat gross, negative net -- not a validated edge."),
    ),
    "CRUDEOIL": InstrumentProfile(
        symbol="CRUDEOIL", exchange="MCX", segment="CRUDEOIL_OPTION",
        lot_size=Param(100, ParamStatus.VERIFIED,
                       "app/institutional_edge/costs.py KNOWN_COST_PROFILES "
                       "(validated against a real AngelOne contract note, 2026-09-11)",
                       "Cross-checked against the real, validated cost profile."),
        params={
            "max_hold_sec": Param(2400, ParamStatus.DERIVED,
                                  "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                                  "Calibrated 2026-09-04 from 74 real closed AUTOSCALP "
                                  "trades: winners' MFE ran to a median 4.2x ATR."),
            "sl_atr": Param(1.2, ParamStatus.DERIVED,
                           "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                           "Same 2026-09-04/74-trade calibration; MAE matched the base "
                           "SL almost exactly, widened slightly with the target."),
            "t1_atr": Param(1.9, ParamStatus.DERIVED,
                           "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                           "Same 2026-09-04/74-trade calibration."),
            "min_ev_r": Param(0.15, ParamStatus.DERIVED,
                              "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                              "Same 2026-09-04/74-trade calibration."),
            "rr_min": Param(1.4, ParamStatus.DERIVED,
                            "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                            "Same 2026-09-04/74-trade calibration."),
            "est_cost_r": Param(0.10, ParamStatus.DERIVED,
                               "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                               "Pre-trade heuristic only, distinct from the validated "
                               "real per-lot cost model used in realized net P&L."),
            "trail_atr": Param(1.6, ParamStatus.DERIVED,
                              "app/autoscalp/runner.py symbol_profiles.CRUDEOIL",
                              "Same rationale as NATURALGAS's trail_atr widening."),
        },
        cost_model_status=_cost_status("CRUDEOIL"),
        validation_status=ParamStatus.DERIVED,
        sample_note=("40 real closed AUTOSCALP paper trades (2026-08-31..09-18): "
                     "win_rate=0.450, expectancy=-3.1575 pts gross; CALIBRATED cost "
                     "model (Rs184.50/lot round-trip) -> net=-200.10 pts total. "
                     "Negative both gross and net -- not a validated edge."),
    ),
}

_COMMON_FALLBACK_NOTE = ("Unknown symbol -- no instrument profile exists. Running on "
                         "unmodified _BASE common-core defaults as the safe fallback.")


def get_instrument_profile(symbol: str) -> InstrumentProfile:
    """Deterministic, pure lookup -- no I/O, no DB reads, no time-dependence.
    Unknown symbols get the common-core defaults verbatim (status UNKNOWN),
    never a guessed or interpolated value."""
    sym = str(symbol or "").upper()
    prof = REGISTRY.get(sym)
    if prof is not None:
        return prof
    return InstrumentProfile(
        symbol=sym, exchange="NSE", segment=f"{sym}_OPTION",
        lot_size=Param(None, ParamStatus.UNKNOWN, "none", "no instrument master lookup performed"),
        params={}, cost_model_status="UNCALIBRATED",
        validation_status=ParamStatus.UNKNOWN, sample_note=_COMMON_FALLBACK_NOTE,
    )


def build_symbol_profiles_config() -> dict:
    """Reconstruct the exact `symbol_profiles` dict shape
    app.autoscalp.runner.DEFAULT_CONFIG has always used, generated FROM this
    governed registry instead of a second, independent hardcoded copy.
    Symbols with no overrides (NIFTY/BANKNIFTY/SENSEX) are correctly absent
    from the output, matching existing behavior exactly (verified in
    tests/test_instrument_profiles.py::test_build_symbol_profiles_config_matches_existing_runner_config)."""
    out: dict = {}
    for sym, prof in REGISTRY.items():
        if not prof.params:
            continue
        entry: dict = {}
        ev_overrides = {}
        for name, p in prof.params.items():
            if name in ("min_ev_r", "rr_min"):
                ev_overrides[name] = p.value
            else:
                entry[name] = p.value
        if ev_overrides:
            entry["ev"] = ev_overrides
        out[sym] = entry
    return out
