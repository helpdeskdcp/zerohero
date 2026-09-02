"""Canonical schema for the Greeks Engine's derived records."""
from __future__ import annotations

from enum import Enum


class Quality(str, Enum):
    VALID = "VALID"        # fresh broker Greeks + OI for a good coverage of the band
    STALE = "STALE"        # Greeks older than the freshness threshold
    PARTIAL = "PARTIAL"    # some band strikes lack a broker Greek or an OI value
    INVALID = "INVALID"    # aggregate could not be computed from the valid inputs
    NO_DATA = "NO_DATA"    # no broker Greeks at all for this underlying+expiry


# The per-Greek fields we derive exposure for. Rho is NOT here — AngelOne's
# optionGreek endpoint does not provide it, so it is never derived.
GREEKS = ("delta", "gamma", "theta", "vega")

# `greek_exposure` — one derived snapshot per (underlying, expiry, as_of_ts).
EXPOSURE_COLS = (
    "computed_ts", "as_of_ts", "underlying", "expiry", "session_date_ist",
    "underlying_price", "underlying_price_src",
    "n_pairs_used", "n_pairs_expected", "n_pairs_missing", "coverage_pct",
    "stale_sec", "quality",
    "ce_oi_total", "pe_oi_total", "pcr_oi",
    "ce_delta_exp", "pe_delta_exp", "net_delta_exp", "diff_delta_exp",
    "ce_gamma_exp", "pe_gamma_exp", "net_gamma_exp", "diff_gamma_exp",
    "ce_theta_exp", "pe_theta_exp", "net_theta_exp", "diff_theta_exp",
    "ce_vega_exp", "pe_vega_exp", "net_vega_exp", "diff_vega_exp",
    "oi_weighted_iv", "vega_weighted_iv",
    "gamma_conc_strike", "gamma_conc_pct", "gamma_herfindahl",
    "per_strike_json", "source", "run_id",
)

# `greek_engine_runs` — provenance / audit for every engine pass.
RUN_COLS = (
    "started_ts", "ended_ts", "mode", "underlying", "expiries_json",
    "snapshots_written", "quality_json", "errors_json", "notes",
)

SOURCE = "DERIVED_FROM_ANGELONE_OPTION_GREEK"
