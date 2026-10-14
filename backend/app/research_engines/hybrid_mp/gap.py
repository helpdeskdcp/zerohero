"""Opening-gap logic. `pine` reproduces the script exactly; `session_aware` is the corrected, separately documented calculation.

Pine: on the first bar of a new calendar day  priorDayClose := close[1]  and  todayOpen := open.  close[1] is the previous CHART bar, which is the
previous session's final close only if the chart holds contiguous regular-session bars. It is wrong when the previous bar is a pre/post-market
bar, when the previous session's last candle is missing, or on the first bar of the data (no close[1]).
session_aware: prior close = close of the last IN-SESSION bar of the previous trading date present in the data; open = first in-session bar of
today. A prior session whose last in-session bar is earlier than `session_end - tolerance` is flagged `incomplete_prior` (no silent guess).
"""
from __future__ import annotations

import numpy as np

from ..smart_bos.data import ist_minutes

NAN = float("nan")


def gap_text(gap_pct: float) -> str:
    if gap_pct != gap_pct:
        return "GAP N/A"
    return f"GAP UP {gap_pct:.2f}%" if gap_pct > 0.10 else f"GAP DOWN {gap_pct:.2f}%" if gap_pct < -0.10 else "FLAT OPEN"


def opening_gap_pine(chart: dict) -> dict:
    """per bar: gap_pct as the script computes it (carried through the day). Uses every bar in `chart`, in session or not."""
    t, o, c = chart["t"], chart["o"], chart["c"]
    days, _ = ist_minutes(t)
    n = len(t)
    out = np.full(n, NAN)
    prior, today_open = NAN, NAN
    for i in range(n):
        if i == 0 or days[i] != days[i - 1]:
            prior, today_open = (c[i - 1] if i > 0 else NAN), o[i]
        out[i] = (today_open - prior) / prior * 100.0 if prior == prior and prior != 0 and today_open == today_open else NAN
    return dict(gap_pct=out)


def opening_gap_session_aware(chart: dict, session: tuple[int, int] = (555, 930), tolerance_min: int = 5) -> dict:
    """per bar: corrected gap_pct (NaN until the day's first in-session bar) and `incomplete_prior` flag."""
    t, o, c = chart["t"], chart["o"], chart["c"]
    days, mod = ist_minutes(t)
    n = len(t)
    ins = (mod >= session[0]) & (mod < session[1])
    out, flag = np.full(n, NAN), np.zeros(n, bool)
    last_close = {}            # trading date -> (last in-session close, its minute-of-day)
    first_open = {}
    for i in range(n):
        if ins[i]:
            d = int(days[i])
            first_open.setdefault(d, o[i])
            last_close[d] = (c[i], int(mod[i]))
    dates = sorted(last_close)
    prev_of = {d: (dates[k - 1] if k else None) for k, d in enumerate(dates)}
    for i in range(n):
        d = int(days[i])
        if d not in first_open or not ins[i]:
            continue
        p = prev_of.get(d)
        if p is None:
            continue
        pc, pm = last_close[p]
        out[i] = (first_open[d] - pc) / pc * 100.0
        flag[i] = pm < session[1] - 1 - tolerance_min
    return dict(gap_pct=out, incomplete_prior=flag)
