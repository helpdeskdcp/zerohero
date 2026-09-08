"""
Next-candle label: UP / DOWN / INSIDE.

This is the ONLY module that reads bar i+1, and it is used only when BUILDING a
training dataset -- never at inference / in the causal feature path.

INSIDE  : next.high <= cur.high AND next.low >= cur.low          (contained range)
UP      : not inside AND next.close - cur.close >=  d*atr
DOWN    : not inside AND next.close - cur.close <= -d*atr
else    : INSIDE   (a real but non-committal move -> treat as no directional edge)

`d` = config.label_dir_min_atr ; atr over config.atr_window CLOSED bars <= i.
"""
from __future__ import annotations

from .config import merged
from .formulas import atr

CLASSES = ("UP", "DOWN", "INSIDE")
CLASS_IDX = {c: i for i, c in enumerate(CLASSES)}


def next_candle_label(bars: list[dict], i: int, cfg: dict | None = None) -> str | None:
    """bars oldest..newest. Label for the transition bars[i] -> bars[i+1].
    None when i+1 is out of range or ATR is undefined."""
    c = cfg or merged()
    if i + 1 >= len(bars) or i < 1:
        return None
    cur, nxt = bars[i], bars[i + 1]
    a = atr(bars[max(0, i - c["atr_window"]): i + 1], c["atr_window"], c)
    if a is None or a <= c["eps"]:
        return None
    if nxt["h"] <= cur["h"] + c["eps"] and nxt["l"] >= cur["l"] - c["eps"]:
        return "INSIDE"
    dc = nxt["c"] - cur["c"]
    thr = c["label_dir_min_atr"] * a
    if dc >= thr:
        return "UP"
    if dc <= -thr:
        return "DOWN"
    return "INSIDE"


def label_distribution(labels: list[str]) -> dict:
    n = len(labels) or 1
    return {cl: round(labels.count(cl) / n, 4) for cl in CLASSES} | {"n": len(labels)}
