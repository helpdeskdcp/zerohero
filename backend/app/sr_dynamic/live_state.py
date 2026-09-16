"""
Live-wiring spec section 2-3: per-symbol live SR state, built entirely from
the existing pipeline's own reused pieces:
  - zone discovery + touch/rejection/breakout/retest/flip: app.sr_dynamic.engine.compute_dynamic_sr
    (which itself reuses app.engines.sr_engine._swings/_cluster/_atr)
  - visit-based (not bar-overlap) current touch/rejection: app.sr_dynamic.visits
No new SR algorithm, no new zone construction -- this module only selects
the nearest support/resistance from the zones compute_dynamic_sr already
returns, and derives the CURRENT (as-of-latest-bar) state fields the live
signal pipeline needs, which compute_dynamic_sr's cumulative-history zone
list doesn't itself expose (it reports lifetime counts, not "right now").

Known scope boundary: `support_breakdown`/`resistance_breakout` reflect
whichever zone is CURRENTLY classified support/resistance (price-relative,
via `_nearest`). Once a zone's breakout succeeds and price moves fully
past it, that zone's `level` ends up on the other side of price and it
becomes the nearest zone of the OPPOSITE type on the next call -- a
resistance-to-support (or support-to-resistance) flip, which the
underlying zone dict already reports via its own `flip_status` field
(app.sr_dynamic.breakout), just not yet surfaced as a distinct LiveSRState
field in this v1.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict

from ..engines.sr_engine import _atr, _bars
from .engine import compute_dynamic_sr, DEFAULT_CONFIG
from .visits import latest_touch_state

MODEL_VERSION = "sr-live-v1"


@dataclass
class LiveSRState:
    symbol: str
    timestamp: str
    spot: float
    nearest_support: float | None = None
    nearest_resistance: float | None = None
    support_distance: float | None = None
    resistance_distance: float | None = None
    support_zone_low: float | None = None
    support_zone_high: float | None = None
    resistance_zone_low: float | None = None
    resistance_zone_high: float | None = None
    support_touch: bool = False
    resistance_touch: bool = False
    support_rejection: bool = False
    resistance_rejection: bool = False
    support_breakdown: bool = False
    resistance_breakout: bool = False
    breakout_confirmed: bool = False
    breakdown_confirmed: bool = False
    support_retest_status: str = "NONE"
    resistance_retest_status: str = "NONE"
    sr_confidence: float = 0.0
    sr_reaction_score: float = 0.0
    zone_strength: float = 0.0
    zone_touch_count: int = 0
    zone_rejection_count: int = 0
    state: str = "NO_DATA"
    model_version: str = MODEL_VERSION

    def to_dict(self):
        return asdict(self)


def _nearest(zones: list[dict], price: float, side: str) -> dict | None:
    cands = [z for z in zones if (z["level"] < price if side == "support" else z["level"] > price)]
    if not cands:
        return None
    return min(cands, key=lambda z: abs(z["level"] - price))


def compute_live_sr_state(symbol: str, bars_by_tf: dict, *, primary_tf: str = "5m",
                          config: dict | None = None) -> LiveSRState | None:
    """Pure function -- does not touch any registry or global state. Returns
    None (never a fabricated zero-value state) if there isn't enough real
    data to say anything."""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    bars = bars_by_tf.get(primary_tf) or []
    H, L, C, V, n = _bars(bars)
    if n < 15:
        return None
    atr = _atr(H, L, C, n, min(14, n - 1))
    if not atr or atr <= 0:
        return None
    price = C[-1]

    zones = compute_dynamic_sr(bars_by_tf, primary_tf=primary_tf, config=config)
    if not zones:
        return LiveSRState(symbol=symbol, timestamp=_now_iso(), spot=round(price, 4), state="NO_ZONES")

    sup = _nearest(zones, price, "support")
    res = _nearest(zones, price, "resistance")

    st = LiveSRState(symbol=symbol, timestamp=_now_iso(), spot=round(price, 4))

    conf_bars = cfg.get("rejection_confirmation_bars", 2)

    if sup:
        st.nearest_support = sup["level"]
        st.support_distance = round(price - sup["level"], 4)
        st.support_zone_low, st.support_zone_high = sup["zone_low"], sup["zone_high"]
        ts = latest_touch_state(sup["zone_low"], sup["zone_high"], H, L, C, atr, confirmation_bars=conf_bars)
        st.support_touch = ts.touching
        st.support_rejection = ts.rejected
        st.support_breakdown = sup["breakout_status"] == "CONFIRMED" and sup["breakout_direction"] == "DOWN"
        st.breakdown_confirmed = st.support_breakdown and sup["retest_status"] == "SUCCESSFUL"
        st.support_retest_status = sup["retest_status"]

    if res:
        st.nearest_resistance = res["level"]
        st.resistance_distance = round(res["level"] - price, 4)
        st.resistance_zone_low, st.resistance_zone_high = res["zone_low"], res["zone_high"]
        tr = latest_touch_state(res["zone_low"], res["zone_high"], H, L, C, atr, confirmation_bars=conf_bars)
        st.resistance_touch = tr.touching
        st.resistance_rejection = tr.rejected
        st.resistance_breakout = res["breakout_status"] == "CONFIRMED" and res["breakout_direction"] == "UP"
        st.breakout_confirmed = st.resistance_breakout and res["retest_status"] == "SUCCESSFUL"
        st.resistance_retest_status = res["retest_status"]

    active = None
    if sup and st.support_touch:
        active = sup
    elif res and st.resistance_touch:
        active = res
    elif sup and res:
        active = sup if abs(st.support_distance) <= abs(st.resistance_distance) else res
    else:
        active = sup or res

    if active:
        st.zone_strength = active["strength"]
        st.zone_touch_count = active["touches"]
        st.zone_rejection_count = active["rejections"]
        st.sr_confidence = active["strength"]
        st.sr_reaction_score = round(100.0 * (active["components"].get("touch_rejection")
                                              if active["components"].get("touch_rejection") is not None
                                              else active["strength"] / 100.0), 1)

    st.state = _label_state(st)
    return st


def _label_state(st: LiveSRState) -> str:
    if st.support_rejection:
        return "SUPPORT_REJECTED"
    if st.resistance_rejection:
        return "RESISTANCE_REJECTED"
    if st.breakout_confirmed:
        return "BREAKOUT_CONFIRMED"
    if st.breakdown_confirmed:
        return "BREAKDOWN_CONFIRMED"
    if st.resistance_breakout:
        return "BREAKOUT_PENDING_RETEST"
    if st.support_breakdown:
        return "BREAKDOWN_PENDING_RETEST"
    if st.support_touch:
        return "AT_SUPPORT"
    if st.resistance_touch:
        return "AT_RESISTANCE"
    if st.support_distance is not None and st.resistance_distance is not None:
        if st.resistance_distance > st.support_distance:
            return "BULLISH_ROOM"
        if st.support_distance > st.resistance_distance:
            return "BEARISH_ROOM"
        return "NEUTRAL_ROOM"
    return "INSUFFICIENT_ZONES"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Section 18 (observability): a tiny in-memory "latest state per symbol"
# registry so /api/sr/status can read what the live decision cycle last saw,
# without the API route needing its own subscription or recompute path.
# ---------------------------------------------------------------------------
_LATEST: dict[str, dict] = {}


def refresh_and_store(symbol: str, bars_by_tf: dict, *, primary_tf: str = "5m",
                      config: dict | None = None) -> LiveSRState | None:
    st = compute_live_sr_state(symbol, bars_by_tf, primary_tf=primary_tf, config=config)
    if st is not None:
        _LATEST[symbol.upper()] = st.to_dict()
    return st


def get_latest(symbol: str) -> dict | None:
    return _LATEST.get(symbol.upper())


def get_all_latest() -> dict:
    return dict(_LATEST)
