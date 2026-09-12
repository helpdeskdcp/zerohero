"""
Regime-aware parameter registry -- section G of the spec.

"Do not assume these regimes are correct" is the operative constraint here.
This file is a REGISTRY, not a new classifier and not a new taxonomy: the
regime names below are exactly the values `app.engines.regime_mtf.detect_
regime()` already produces (mirrored here, not re-derived -- if that
detector's output values change, this list must be updated to match, and
`get_profile()` falls back safely for anything it doesn't recognise rather
than raising).

What this file explicitly does NOT do: it does not wire per-regime
thresholds into drift.py, performance_monitor.py, or break_score.py. Every
regime maps to the same DEFAULT_PROFILE (i.e. no behavioural change at all)
until someone backtests a specific override via `backtest_compare.py` and
calls `register()` deliberately. Shipping this registry pre-populated with
per-regime numbers nobody has validated would be exactly the "blindly
optimize parameters" the spec's opening constraint forbids -- so it ships
inert by construction, not as an oversight.

The one thing this file DOES make available today is `scope_key()`: a
single, consistent way to key a per-(symbol[, regime]) state-machine
instance -- break_score.py's own docstring already says "one instance per
(symbol, regime) or per (symbol)"; this just gives every caller the same
key format instead of each inventing its own.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

# Mirrors app.engines.regime_mtf.detect_regime()'s output values as of this
# writing. Deliberately a plain tuple, not imported from regime_mtf, because
# that module doesn't export one -- see module docstring.
REGIME_NAMES = (
    "TRENDING_UP", "TRENDING_DOWN", "RANGE", "HIGH_VOLATILITY",
    "LOW_VOLATILITY", "BREAKOUT_REGIME", "REVERSAL_REGIME", "UNSTABLE",
)


@dataclass
class RegimeProfile:
    """Every field is None (= "inherit the global default from drift.py /
    performance_monitor.py / break_score.py") until a backtest-validated
    override is registered. A profile with every field None is behaviourally
    identical to having no profile at all -- that's intentional, it's the
    ship-inert starting point."""
    regime: str
    validated: bool = False              # True only once backtest_compare.py has confirmed this override
    validated_ts: str | None = None
    validated_by_note: str | None = None
    watch_min_override: int | None = None
    degrading_min_override: int | None = None
    break_min_override: int | None = None
    perf_window_overrides: dict | None = None   # e.g. {"short": 15} to override DEFAULT_WINDOWS

    def to_dict(self):
        return asdict(self)


DEFAULT_PROFILE_FOR = lambda regime: RegimeProfile(regime=regime)  # noqa: E731 -- trivial, no need for a def


class RegimeProfileRegistry:
    """Holds zero or more validated per-regime overrides. Starts empty --
    every `get()` call returns an inert default profile until `register()`
    is called explicitly (never automatically) with a profile whose
    `validated=True` and a `validated_by_note` explaining what evidence
    justified it (spec I's auditability principle applies here too: a
    parameter override with no recorded "why" is exactly what this spec is
    trying to move away from)."""

    def __init__(self):
        self._profiles: dict[str, RegimeProfile] = {}

    def register(self, profile: RegimeProfile) -> None:
        if not profile.validated:
            raise ValueError(
                f"refusing to register an unvalidated profile for {profile.regime!r} -- "
                "set validated=True with a validated_by_note only after backtest_compare.py "
                "has confirmed the override, per spec G's own caution"
            )
        if not profile.validated_by_note:
            raise ValueError(f"profile for {profile.regime!r} is validated=True but has no "
                              "validated_by_note -- record what evidence justified it")
        self._profiles[profile.regime] = profile

    def get(self, regime: str | None) -> RegimeProfile:
        if regime and regime in self._profiles:
            return self._profiles[regime]
        return DEFAULT_PROFILE_FOR(regime or "UNKNOWN")

    def unregister(self, regime: str) -> None:
        self._profiles.pop(regime, None)

    def known_regimes(self) -> tuple:
        return REGIME_NAMES


_singleton: RegimeProfileRegistry | None = None


def registry() -> RegimeProfileRegistry:
    global _singleton
    if _singleton is None:
        _singleton = RegimeProfileRegistry()
    return _singleton


def scope_key(symbol: str, regime: str | None = None) -> str:
    """Consistent key for scoping a per-(symbol[, regime]) tracker instance
    (a StructuralBreakStateMachine, a PerformanceMonitor call, an
    AdaptationTracker, ...). `regime=None` scopes to the symbol alone."""
    return f"{symbol}:{regime}" if regime else symbol
