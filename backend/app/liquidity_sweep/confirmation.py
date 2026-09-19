"""
3-minute-style confirmation -- section 5: CHoCH or BOS, plus at least one of
CISD / FVG / Order Block. Named "3-minute" in the brief, but see
backtest.py's own note: Kaggle's real NIFTY/BankNifty dataset is 5-minute
only (no 1-minute bars to build 3m candles from without fabricating data --
section 14 explicitly forbids that), so the execution timeframe actually
used against that dataset is 5m, clearly labelled as a documented
substitution, never a silent one. This module itself is timeframe-agnostic
-- it operates on whatever closed bars it's given.

Definitions used (standard SMC terminology, no invented variant):
  BOS (Break of Structure)   -- a CLOSE beyond the most recent swing point
                                 in the direction the last 2+ same-kind
                                 swings already agree on (continuation).
  CHoCH (Change of Character)-- a CLOSE beyond the most recent swing point
                                 AGAINST that prevailing direction (the
                                 first break of the established structure --
                                 a potential reversal signal).
  CISD (Change in State of Delivery) -- the first candle whose body color
                                 opposes N-1 preceding candles of the other
                                 color (delivery flips from selling to
                                 buying or vice versa).
  FVG (Fair Value Gap)       -- a 3-candle imbalance: bar[i-2].high <
                                 bar[i].low (bullish gap) or bar[i-2].low >
                                 bar[i].high (bearish gap).
  Order Block                -- the last opposite-colored candle
                                 immediately before an impulsive same-
                                 direction move (defined here as the next
                                 candle's range >= `impulse_atr_mult` x ATR).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from . import structure


def _color(bar) -> str:
    return "GREEN" if bar["c"] >= bar["o"] else "RED"


def structure_break(bars: list[dict], pts: list[structure.SwingPoint]) -> dict:
    """CHoCH or BOS on the CLOSE of the last bar, relative to the most
    recent CONFIRMED swing point of each kind and the prevailing structure
    direction from swing_structure_labels()."""
    labels = structure.swing_structure_labels(pts)
    if len(labels) < 2:
        return {"type": "NONE", "direction": None, "reason": "insufficient swing history"}
    last_close = bars[-1]["c"]

    # prevailing direction: the label of the most recent swing of EACH kind
    last_h = next((l for l in reversed(labels) if l["kind"] == "H"), None)
    last_l = next((l for l in reversed(labels) if l["kind"] == "L"), None)
    uptrend = bool(last_h and last_h["label"] in ("HH",)) or bool(last_l and last_l["label"] in ("HL",))
    downtrend = bool(last_h and last_h["label"] in ("LH",)) or bool(last_l and last_l["label"] in ("LL",))

    if last_h and last_close > last_h["price"]:
        return ({"type": "BOS", "direction": "BULLISH", "level": last_h["price"]} if uptrend and not downtrend
                else {"type": "CHOCH", "direction": "BULLISH", "level": last_h["price"]})
    if last_l and last_close < last_l["price"]:
        return ({"type": "BOS", "direction": "BEARISH", "level": last_l["price"]} if downtrend and not uptrend
                else {"type": "CHOCH", "direction": "BEARISH", "level": last_l["price"]})
    return {"type": "NONE", "direction": None, "reason": "close did not break the most recent swing"}


def cisd(bars: list[dict], *, lookback: int = 4) -> dict:
    """First candle of one color after >=2 of the opposite color immediately
    preceding it, within the last `lookback` bars."""
    if len(bars) < 3:
        return {"confirmed": False}
    tail = bars[-lookback:] if len(bars) >= lookback else bars
    colors = [_color(b) for b in tail]
    last = colors[-1]
    prior = colors[:-1]
    if len(prior) >= 2 and all(c != last for c in prior[-2:]):
        return {"confirmed": True, "direction": "BULLISH" if last == "GREEN" else "BEARISH"}
    return {"confirmed": False}


def fvg(bars: list[dict]) -> dict:
    """3-candle imbalance ending at the last bar (bars[-3], bars[-2], bars[-1])."""
    if len(bars) < 3:
        return {"confirmed": False}
    a, _, c = bars[-3], bars[-2], bars[-1]
    if a["h"] < c["l"]:
        return {"confirmed": True, "direction": "BULLISH", "gap_low": a["h"], "gap_high": c["l"]}
    if a["l"] > c["h"]:
        return {"confirmed": True, "direction": "BEARISH", "gap_low": c["h"], "gap_high": a["l"]}
    return {"confirmed": False}


def order_block(bars: list[dict], *, atr: float | None = None, impulse_atr_mult: float = 1.2) -> dict:
    """The second-to-last bar, if it's the opposite color from the last bar
    AND the last bar's range is an impulsive move (>= impulse_atr_mult x ATR,
    when ATR is available; without ATR, any opposite-color-then-larger-range
    bar counts, documented as a looser fallback)."""
    if len(bars) < 2:
        return {"confirmed": False}
    ob_bar, impulse_bar = bars[-2], bars[-1]
    if _color(ob_bar) == _color(impulse_bar):
        return {"confirmed": False}
    impulse_range = impulse_bar["h"] - impulse_bar["l"]
    ob_range = ob_bar["h"] - ob_bar["l"]
    is_impulsive = (impulse_range >= impulse_atr_mult * atr) if atr else (impulse_range > ob_range)
    if not is_impulsive:
        return {"confirmed": False}
    direction = "BULLISH" if _color(impulse_bar) == "GREEN" else "BEARISH"
    return {"confirmed": True, "direction": direction,
            "ob_high": ob_bar["h"], "ob_low": ob_bar["l"]}


@dataclass
class Confirmation:
    structure_type: str          # "CHOCH" | "BOS" | "NONE"
    structure_direction: str | None
    cisd_confirmed: bool
    fvg_confirmed: bool
    order_block_confirmed: bool
    secondary_confirmed: bool     # at least one of CISD/FVG/OB
    candle_closed: bool           # always True here -- bars are closed bars by contract

    def to_dict(self) -> dict:
        return asdict(self)


def evaluate(bars: list[dict], pts: list[structure.SwingPoint], *, atr: float | None = None) -> Confirmation:
    sb = structure_break(bars, pts)
    c = cisd(bars)
    f = fvg(bars)
    ob = order_block(bars, atr=atr)
    secondary = bool(c["confirmed"] or f["confirmed"] or ob["confirmed"])
    return Confirmation(
        structure_type=sb["type"], structure_direction=sb.get("direction"),
        cisd_confirmed=bool(c["confirmed"]), fvg_confirmed=bool(f["confirmed"]),
        order_block_confirmed=bool(ob["confirmed"]), secondary_confirmed=secondary,
        candle_closed=True)
