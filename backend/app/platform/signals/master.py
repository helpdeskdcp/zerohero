"""Master (shared) signal: CE / PE / NO_TRADE, produced once, then personalized per tenant.

This module only ADAPTS the existing validated engines (autoscalp decide_from_context output) -- it
does not generate a second signal engine. Rules enforced here:
  * data not OK (no healthy provider / stale feed / missing chain)  => NO_TRADE, with the reason
  * confidence is reported ONLY when the engine's probability is calibrated ("fitted"); otherwise None
    (a prior/uncalibrated probability is never presented as a confidence percentage)
  * no price, OI, volume or confidence value is invented
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

CE, PE, NO_TRADE = "CE", "PE", "NO_TRADE"


@dataclass(frozen=True)
class MasterSignal:
    signal_id: str
    underlying: str
    ts: float
    direction: str                       # CE | PE | NO_TRADE
    reasons: tuple = ()
    regime: str | None = None
    confidence_pct: float | None = None  # None unless calibrated
    confidence_calibrated: bool = False
    engine_probability: float | None = None
    engine_confidence_label: str | None = None
    underlying_price: float | None = None
    premium_sl_fraction: float | None = None   # (entry - stop) / entry of the ENGINE's own contract; None if unknown
    source: str = "autoscalp.decide_from_context"
    data_ok: bool = True
    blocked_reasons: tuple = ()
    extras: dict = field(default_factory=dict)

    @property
    def tradable(self) -> bool:
        return self.direction in (CE, PE) and self.data_ok


def from_autoscalp_sig(sig: dict, *, underlying: str, now: float | None = None, data_ok: bool = True,
                       data_reasons: tuple = (), signal_id: str | None = None) -> MasterSignal:
    now = time.time() if now is None else now
    decision = str(sig.get("decision") or "NO_TRADE")
    direction = {"BUY_CE": CE, "BUY_PE": PE}.get(decision, NO_TRADE)
    reasons = tuple(r.strip() for r in str(sig.get("reason") or "").split("|") if r.strip())
    sid = signal_id or ("MS-" + uuid.uuid4().hex[:12])
    if not data_ok:
        return MasterSignal(sid, underlying.upper(), now, NO_TRADE, reasons + ("DATA_NOT_OK",), sig.get("regime"),
                            data_ok=False, blocked_reasons=tuple(data_reasons) or ("DATA_NOT_OK",))
    fitted = sig.get("calibration_status") == "fitted"
    prob = sig.get("probability")
    entry, stop = sig.get("entry"), sig.get("stop_loss")
    sl_frac = None
    if direction != NO_TRADE and isinstance(entry, (int, float)) and isinstance(stop, (int, float)) and entry > 0 and 0 < stop < entry:
        sl_frac = (entry - stop) / entry
    return MasterSignal(
        sid, underlying.upper(), now, direction, reasons, sig.get("regime"),
        confidence_pct=(round(float(prob) * 100.0, 1) if (fitted and isinstance(prob, (int, float))) else None),
        confidence_calibrated=bool(fitted and isinstance(prob, (int, float))),
        engine_probability=prob if isinstance(prob, (int, float)) else None,
        engine_confidence_label=sig.get("confidence"),
        underlying_price=sig.get("index_ltp") or sig.get("underlying_price"),
        premium_sl_fraction=sl_frac,
        extras={k: sig.get(k) for k in ("signal_type", "mtf_alignment", "support", "resistance", "ev", "rr") if k in sig})
