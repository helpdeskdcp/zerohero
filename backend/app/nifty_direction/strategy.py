"""15-minute trend confirmation + 5-minute entry engine. Pure functions on CLOSED candles; every decline to signal carries a reason code."""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict

from . import indicators as I


@dataclass
class StrategyConfig:
    ema_fast: int = 9
    ema_slow: int = 21
    atr_n: int = 14
    min_bars_15: int = 30
    min_bars_5: int = 30
    max_bar_age_15_sec: int = 15 * 60 * 2 + 60       # newest closed 15m bar may be at most ~2 bars old
    max_bar_age_5_sec: int = 5 * 60 * 2 + 60
    neutral_band_atr: float = 0.15                    # |ema_fast-ema_slow| < band*ATR -> NEUTRAL
    rel_vol_min: float = 1.0                          # applied only when volume exists
    retest_tol: float = 0.0015
    cooldown_sec: int = 30 * 60                       # same-direction duplicate suppression
    sl_atr: float = 1.5
    def to_dict(self): return asdict(self)


def trend15(c15: list[dict], now: float, cfg: StrategyConfig | None = None) -> dict:
    """state: BULLISH / BEARISH / NEUTRAL / STALE / INVALID (INVALID also covers too-little history)."""
    cfg = cfg or StrategyConfig()
    bad = I.validate_candles(c15, 900)
    if bad: return {"state": "INVALID", "reason": bad}
    c15 = I.closed_only(c15, now, 900)
    if len(c15) < cfg.min_bars_15: return {"state": "INVALID", "reason": "INSUFFICIENT_BARS", "have": len(c15), "need": cfg.min_bars_15}
    if now - (c15[-1]["t"] + 900) > cfg.max_bar_age_15_sec: return {"state": "STALE", "reason": "LAST_BAR_OLD", "last_bar_end": c15[-1]["t"] + 900}
    cl = [c["c"] for c in c15]; ef, es, a = I.ema(cl, cfg.ema_fast), I.ema(cl, cfg.ema_slow), I.atr(c15, cfg.atr_n)
    if a is None or a <= 0: return {"state": "INVALID", "reason": "NO_ATR"}
    st = I.structure(c15); gap = ef - es; px = cl[-1]
    if abs(gap) < cfg.neutral_band_atr * a: state = "NEUTRAL"
    elif gap > 0 and px > es and st != "LH_LL": state = "BULLISH"
    elif gap < 0 and px < es and st != "HH_HL": state = "BEARISH"
    else: state = "NEUTRAL"
    return {"state": state, "ema_fast": ef, "ema_slow": es, "atr": a, "neutral_band": cfg.neutral_band_atr * a, "structure": st, "close": px, "last_bar_t": c15[-1]["t"]}


def entry5(c5: list[dict], trend: dict, now: float, cfg: StrategyConfig | None = None) -> dict:
    """Entry only WITH the 15m trend: BULLISH -> long on breakout/retest of resistance or opening-range high; BEARISH -> mirror. Anything else = NO_SIGNAL + reason."""
    cfg = cfg or StrategyConfig()
    if trend.get("state") not in ("BULLISH", "BEARISH"): return {"signal": None, "reason": f"TREND_{trend.get('state')}"}
    bad = I.validate_candles(c5, 300)
    if bad: return {"signal": None, "reason": f"INVALID_5M_{bad}"}
    c5 = I.closed_only(c5, now, 300)
    if len(c5) < cfg.min_bars_5: return {"signal": None, "reason": "INSUFFICIENT_5M_BARS"}
    if now - (c5[-1]["t"] + 300) > cfg.max_bar_age_5_sec: return {"signal": None, "reason": "STALE_5M"}
    up = trend["state"] == "BULLISH"; a = I.atr(c5, cfg.atr_n)
    if a is None or a <= 0: return {"signal": None, "reason": "NO_ATR_5M"}
    prior = c5[:-1]                                                       # levels must exist BEFORE the bar that breaks them
    sw = I.swings(prior[-60:], 2); lv = sw["highs"] if up else sw["lows"]; px0 = c5[-1]["c"]
    near = sorted({v for _, v in lv if abs(v - px0) <= 2 * a}, key=lambda v: abs(v - px0))[:3]
    orr = I.opening_range(c5, 15)
    cands = ([("OR_HIGH" if up else "OR_LOW", orr["high"] if up else orr["low"])] if orr else []) + [("SWING_RES" if up else "SWING_SUP", v) for v in near]
    rv = I.rel_volume(c5); vw = I.vwap(c5); px = c5[-1]["c"]
    for cand in cands:
        if not cand or cand[1] is None: continue
        ev = I.break_events(c5, cand[1], "UP" if up else "DOWN", cfg.retest_tol)["event"]
        if ev in ("BREAKOUT", "BREAKDOWN", "RETEST"):
            if rv is not None and rv < cfg.rel_vol_min: return {"signal": None, "reason": "LOW_REL_VOLUME", "rel_volume": rv}
            if vw is not None and ((up and px < vw) or (not up and px > vw)): return {"signal": None, "reason": "WRONG_SIDE_OF_VWAP"}
            sl = px - cfg.sl_atr * a if up else px + cfg.sl_atr * a
            return {"signal": "LONG" if up else "SHORT", "reason": f"{ev}_{cand[0]}", "level": cand[1], "price": px, "invalidation": sl, "atr": a, "rel_volume": rv, "vwap": vw, "bar_t": c5[-1]["t"]}
    return {"signal": None, "reason": "NO_SETUP"}


class Deduper:
    """Suppress a repeat of the same direction within the cooldown, or on the same bar; an opposite signal resets."""
    def __init__(self, cooldown_sec: int): self.cd = cooldown_sec; self.last: dict | None = None

    def allow(self, side: str, bar_t: float, now: float) -> bool:
        l = self.last
        if l and l["bar_t"] == bar_t: return False
        if l and l["side"] == side and now - l["now"] < self.cd: return False
        self.last = {"side": side, "bar_t": bar_t, "now": now}; return True


def invalidated(position_side: str, invalidation: float, last_close: float, trend_state: str) -> str | None:
    """Explicit invalidation: close through the stop level, or the 15m trend turning against the idea."""
    if position_side == "LONG" and last_close <= invalidation: return "STOP_LEVEL"
    if position_side == "SHORT" and last_close >= invalidation: return "STOP_LEVEL"
    if (position_side == "LONG" and trend_state == "BEARISH") or (position_side == "SHORT" and trend_state == "BULLISH"): return "TREND_FLIPPED"
    return None
