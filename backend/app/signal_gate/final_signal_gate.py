"""
High-confidence single-signal gate. Sits AFTER decide_from_context() (the
existing raw strategy) and does not replace or duplicate any of its scoring
-- it reuses what's already computed: sr_confirmation (app.sr_dynamic,
already wired in scalp_strategy.py's opt-in SR gate), chain_bias (the
existing option-chain gate), state_classifier's own component_scores
(trend/momentum/volume/oi/volatility), and the existing EV/RR figures.

Decision states: APPROVED, WAIT, REJECT, DUPLICATE, COOLDOWN. Only
APPROVED may ever be published (Telegram / live signal UI) -- callers must
treat every other state as "no signal."

"High confidence" is a QUALITY score (0-100, transparent weighted
components), never a win-probability, unless a real calibration curve
backs it (app.backtest.calibration, already the case for
`calibration_status == "fitted"` with >= MIN_CALIBRATION_SAMPLES real
resolved outcomes). Otherwise `historical_confidence` reports the literal
string "UNCALIBRATED" -- never an invented percentage.

Real subscribers act on these Telegram signals with real money. This
module ships with every gate defaulting to its most conservative setting
(SR/volume/OI confirmation required, one-active-signal-per-symbol,
threshold 80 pending its own out-of-sample validation) and is wired into
the runner disabled by default -- enabling it for live subscriber-facing
output is a separate decision, not made here.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

APPROVED, WAIT, REJECT, DUPLICATE, COOLDOWN = "APPROVED", "WAIT", "REJECT", "DUPLICATE", "COOLDOWN"

# Starting points only -- section 11 of the spec explicitly warns against
# assuming these are optimal. See scripts/signal_gate_threshold_sweep.py for
# the walk-forward comparison this repo now has; nothing here auto-applies
# that sweep's outcome.
MIN_SIGNAL_SCORE = 80.0
MIN_RR = 1.5
MIN_CALIBRATION_SAMPLES = 40
SIGNAL_COOLDOWN_SEC = 600.0          # 10 min, mid-point of the spec's 5-15 min default

WEIGHTS = {"sr": 0.25, "trend": 0.20, "momentum": 0.15, "volume": 0.15, "oi": 0.15, "risk_reward": 0.10}


@dataclass
class FinalSignalDecision:
    state: str
    reason: str
    confidence_score: float | None = None
    components: dict = field(default_factory=dict)
    historical_confidence: str = "UNCALIBRATED"
    fingerprint: str | None = None


def signal_fingerprint(*, symbol: str, direction: str, instrument: str | None, strike, expiry,
                       sr_zone: tuple | None, signal_type: str) -> str:
    zone = f"{round(sr_zone[0], 2)}-{round(sr_zone[1], 2)}" if sr_zone else "na"
    return "|".join(str(x) for x in (
        (symbol or "").upper(), (direction or "").upper(), (instrument or "").upper(),
        strike, expiry, zone, (signal_type or "").upper()))


# fingerprint -> last-approved epoch (dedup/cooldown). Module-level, mirrors
# app.sr_dynamic.live_state's registry pattern.
_LAST_APPROVED: dict[str, float] = {}

# symbol -> {"direction","fingerprint","opened_ts"} for the ONE-ACTIVE-SIGNAL
# policy. A caller resolves it via mark_resolved() when the trade closes
# (runner.py wires this to the existing trade-close path) -- this module
# never guesses when a trade ended.
_ACTIVE_SIGNAL: dict[str, dict] = {}


def mark_active(symbol: str, direction: str, fingerprint: str, *, now: float | None = None) -> None:
    _ACTIVE_SIGNAL[symbol.upper()] = {"direction": direction, "fingerprint": fingerprint,
                                      "opened_ts": now if now is not None else time.time()}


def mark_resolved(symbol: str) -> None:
    _ACTIVE_SIGNAL.pop(symbol.upper(), None)


def get_active_signal(symbol: str) -> dict | None:
    return _ACTIVE_SIGNAL.get(symbol.upper())


def _trend_score(component_scores: dict) -> float | None:
    parts = [component_scores.get(k) for k in ("htf", "vwap") if component_scores.get(k) is not None]
    return round(100.0 * sum(parts) / len(parts), 1) if parts else None


def _rr_score(rr: float | None) -> float | None:
    if rr is None:
        return None
    return round(max(0.0, min(100.0, (rr / 2.0) * 100.0)), 1)


def _oi_score(chain_bias: dict | None, component_scores: dict) -> float | None:
    """Prefer the existing chain-bias gate's read (already a point-in-time
    ATM-window PCR/OI-writing verdict) when available; fall back to
    state_classifier's own `oi` component. Never fabricates a value when
    both are absent."""
    if chain_bias and chain_bias.get("verdict") not in (None, "INSUFFICIENT"):
        return {"CONFIRM": 90.0, "CONTRADICT": 15.0, "NEUTRAL": 55.0}.get(chain_bias["verdict"])
    oi = component_scores.get("oi")
    return round(oi * 100.0, 1) if oi is not None else None


def historical_confidence_label(decision: dict) -> str:
    if decision.get("calibration_status") != "fitted":
        return "UNCALIBRATED"
    n = decision.get("calibration_samples") or 0
    if n < MIN_CALIBRATION_SAMPLES:
        return "UNCALIBRATED"
    p = decision.get("probability")
    if p is None:
        return "UNCALIBRATED"
    return f"{round(p * 100.0)}% (sample size: {n} trades)"


def evaluate_final_signal(decision: dict, *, sr_confirmation: dict | None = None,
                          chain_bias: dict | None = None,
                          min_signal_score: float = MIN_SIGNAL_SCORE, min_rr: float = MIN_RR,
                          cooldown_sec: float = SIGNAL_COOLDOWN_SEC, now: float | None = None,
                          require_sr_confirmation: bool = True,
                          require_volume_confirmation: bool = False,
                          require_oi_confirmation: bool = False,
                          one_active_signal: bool = True,
                          allow_opposite_while_active: bool = False,
                          active_fingerprints: set | None = None) -> FinalSignalDecision:
    """`active_fingerprints`: fingerprints of currently OPEN/ACTIVE trades for
    the exact-same-setup dedup check. `one_active_signal`/`allow_opposite_
    while_active` govern the coarser per-SYMBOL active-signal policy backed
    by mark_active()/mark_resolved()/get_active_signal() above."""
    now = now if now is not None else time.time()
    symbol = (decision.get("symbol") or "").upper()
    direction = decision.get("direction") or ""

    if decision.get("decision") not in ("BUY_CE", "BUY_PE"):
        return FinalSignalDecision(state=REJECT, reason="no raw directional setup from the existing strategy")

    sr = sr_confirmation or {"verdict": "INSUFFICIENT"}
    if require_sr_confirmation:
        if sr["verdict"] == "CONTRADICT":
            return FinalSignalDecision(state=REJECT, reason=f"SR contradicts the setup ({sr.get('reason')})")
        if sr["verdict"] == "INSUFFICIENT":
            return FinalSignalDecision(state=WAIT, reason="SR state insufficient to confirm or contradict yet")

    rr = decision.get("rr")
    if rr is not None and rr < min_rr:
        return FinalSignalDecision(state=REJECT, reason=f"risk/reward {rr} below minimum {min_rr}")

    comp = decision.get("component_scores") or {}
    sr_component = sr.get("sr_score")
    trend_component = _trend_score(comp)
    momentum_component = round(comp["momentum"] * 100.0, 1) if comp.get("momentum") is not None else None
    volume_component = round(comp["volume"] * 100.0, 1) if comp.get("volume") is not None else None
    oi_component = _oi_score(chain_bias, comp)
    rr_component = _rr_score(rr)

    if require_volume_confirmation and volume_component is None:
        return FinalSignalDecision(state=WAIT, reason="volume confirmation required but no real volume data")
    if require_oi_confirmation and oi_component is None:
        return FinalSignalDecision(state=WAIT, reason="OI confirmation required but no real OI/chain data")

    components = {"sr": sr_component, "trend": trend_component, "momentum": momentum_component,
                 "volume": volume_component, "oi": oi_component, "risk_reward": rr_component}
    available = {k: v for k, v in components.items() if v is not None}
    if not available:
        return FinalSignalDecision(state=WAIT, reason="no scoreable components available yet")
    denom = sum(WEIGHTS[k] for k in available)
    confidence_score = round(sum(WEIGHTS[k] * v for k, v in available.items()) / denom, 1)

    fp = signal_fingerprint(symbol=symbol, direction=direction, instrument=decision.get("decision"),
                            strike=decision.get("strike"), expiry=decision.get("expiry"),
                            sr_zone=(sr_component and (decision.get("support"), decision.get("resistance"))),
                            signal_type=decision.get("signal_type") or "")

    if confidence_score < min_signal_score:
        return FinalSignalDecision(state=WAIT, reason=f"confidence {confidence_score} < min {min_signal_score}",
                                   confidence_score=confidence_score, components=components,
                                   historical_confidence=historical_confidence_label(decision), fingerprint=fp)

    if active_fingerprints and fp in active_fingerprints:
        return FinalSignalDecision(state=DUPLICATE, reason="an identical setup already has an active position",
                                   confidence_score=confidence_score, components=components,
                                   historical_confidence=historical_confidence_label(decision), fingerprint=fp)

    if one_active_signal:
        active = get_active_signal(symbol)
        if active is not None:
            if active["direction"] == direction:
                return FinalSignalDecision(state=DUPLICATE,
                                           reason=f"{symbol} already has an active {direction} signal",
                                           confidence_score=confidence_score, components=components,
                                           historical_confidence=historical_confidence_label(decision), fingerprint=fp)
            if not allow_opposite_while_active:
                return FinalSignalDecision(
                    state=WAIT,
                    reason=f"{symbol} has an active {active['direction']} signal -- wait for it to resolve",
                    confidence_score=confidence_score, components=components,
                    historical_confidence=historical_confidence_label(decision), fingerprint=fp)

    last = _LAST_APPROVED.get(fp)
    if last is not None and now - last < cooldown_sec:
        return FinalSignalDecision(state=COOLDOWN,
                                   reason=f"same setup approved {round(now - last)}s ago (cooldown {cooldown_sec}s)",
                                   confidence_score=confidence_score, components=components,
                                   historical_confidence=historical_confidence_label(decision), fingerprint=fp)

    _LAST_APPROVED[fp] = now
    if one_active_signal:
        mark_active(symbol, direction, fp, now=now)
    return FinalSignalDecision(state=APPROVED, reason="all mandatory confirmations passed",
                               confidence_score=confidence_score, components=components,
                               historical_confidence=historical_confidence_label(decision), fingerprint=fp)
