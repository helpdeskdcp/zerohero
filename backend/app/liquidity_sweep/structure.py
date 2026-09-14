"""
Market structure -- section 3 of the index-first brief: PDH/PDL, swings,
equal highs/lows, HTF bias.

Deliberately a fresh, minimal implementation rather than reaching into
app.engines.sr_engine's private `_swings()`/`_candidates()` -- that module
is on the LIVE autoscalp decision path (imported by scalp_strategy.py) and
this session's own established practice is to never modify or lean on
internals of a live-path file for a new research engine. The fractal
swing-detection algorithm below (3-bar left/right pivot) is the same
well-known technique sr_engine and h1h7_state both already use -- not a new
invented method, just not imported from either.

Every function takes bars ALREADY TRUNCATED to <= the evaluation timestamp
T (chronological, oldest-first, dicts with t/o/h/l/c/v keys) -- no look-ahead
guard is re-derived here, it's the caller's contract, matching every other
causal module in this codebase.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean

BULLISH, BEARISH, RANGE, TRANSITION = "BULLISH", "BEARISH", "RANGE", "TRANSITION"


def _num(x):
    try:
        v = float(x)
        return v if v == v and abs(v) != float("inf") else None
    except (TypeError, ValueError):
        return None


def _session_date(bar) -> str:
    """First 10 chars of an ISO timestamp -- the session-date grouping key
    already used throughout this codebase (e.g. scalp_signals.session_date)."""
    return str(bar.get("t") or "")[:10]


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str          # "H" | "L"
    timestamp: str


def swings(bars: list[dict], *, left: int = 2, right: int = 2) -> list[SwingPoint]:
    """3-to-N-bar fractal swing points. A swing at index i is confirmed once
    `right` bars exist after it -- so the LAST `right` bars of `bars` can
    never produce a swing yet (nothing to confirm against), exactly modeling
    what a live system would actually know at the time."""
    out = []
    n = len(bars)
    for i in range(left, n - right):
        window_h = [bars[j]["h"] for j in range(i - left, i + right + 1)]
        window_l = [bars[j]["l"] for j in range(i - left, i + right + 1)]
        if bars[i]["h"] == max(window_h) and window_h.count(bars[i]["h"]) == 1:
            out.append(SwingPoint(i, bars[i]["h"], "H", bars[i].get("t")))
        if bars[i]["l"] == min(window_l) and window_l.count(bars[i]["l"]) == 1:
            out.append(SwingPoint(i, bars[i]["l"], "L", bars[i].get("t")))
    return out


def swing_structure_labels(pts: list[SwingPoint]) -> list[dict]:
    """HH/HL/LH/LL labeling -- compares each swing to the PRIOR swing of the
    SAME kind only (the standard SMC definition: a swing high is "higher
    high" relative to the last swing high, not the last swing of any kind)."""
    out = []
    last = {"H": None, "L": None}
    for p in pts:
        prev = last[p.kind]
        if prev is None:
            label = "H" if p.kind == "H" else "L"
        elif p.kind == "H":
            label = "HH" if p.price > prev.price else ("LH" if p.price < prev.price else "EQH")
        else:
            label = "LL" if p.price < prev.price else ("HL" if p.price > prev.price else "EQL")
        out.append({"index": p.index, "price": p.price, "kind": p.kind,
                    "label": label, "timestamp": p.timestamp})
        last[p.kind] = p
    return out


@dataclass
class EqualLevel:
    kind: str            # "EQUAL_HIGH" | "EQUAL_LOW"
    level: float          # mean price of the cluster
    touches: int
    indices: list


def equal_levels(pts: list[SwingPoint], *, tolerance_pct: float = 0.05) -> list[EqualLevel]:
    """Two or more swing points of the SAME kind within `tolerance_pct`% of
    each other's price -- textbook "equal highs/equal lows" liquidity pools.
    0.05% is a plain, documented round-number default (roughly 12 points on
    a 24,000 NIFTY spot) -- not fitted to any dataset; callers backtesting a
    different tolerance should pass their own."""
    out = []
    for kind, swing_kind in (("EQUAL_HIGH", "H"), ("EQUAL_LOW", "L")):
        group = sorted([p for p in pts if p.kind == swing_kind], key=lambda p: p.price)
        i = 0
        while i < len(group):
            cluster = [group[i]]
            j = i + 1
            while j < len(group) and abs(group[j].price - cluster[0].price) <= cluster[0].price * tolerance_pct / 100:
                cluster.append(group[j])
                j += 1
            if len(cluster) >= 2:
                out.append(EqualLevel(kind=kind, level=round(mean(p.price for p in cluster), 4),
                                      touches=len(cluster), indices=[p.index for p in cluster]))
            i = j
    return out


@dataclass
class PdhPdl:
    status: str            # "OK" | "NO_PRIOR_SESSION"
    prev_high: float | None = None
    prev_low: float | None = None
    prev_close: float | None = None
    prev_session_date: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def pdh_pdl(bars: list[dict], *, as_of_session: str) -> PdhPdl:
    """Previous Day High/Low/Close relative to `as_of_session` (the CURRENT
    session date, e.g. today) -- only ever looks at sessions strictly BEFORE
    it, never at the current session's own bars, regardless of what `bars`
    otherwise contains."""
    prior = [b for b in bars if _session_date(b) < as_of_session]
    if not prior:
        return PdhPdl(status="NO_PRIOR_SESSION")
    last_session = max(_session_date(b) for b in prior)
    day_bars = [b for b in prior if _session_date(b) == last_session]
    return PdhPdl(status="OK", prev_high=max(b["h"] for b in day_bars),
                  prev_low=min(b["l"] for b in day_bars),
                  prev_close=day_bars[-1]["c"], prev_session_date=last_session)


def day_open_high_low(bars: list[dict], *, session: str) -> dict:
    """Today's own (in-progress) open/high/low, from bars of `session` only."""
    today = [b for b in bars if _session_date(b) == session]
    if not today:
        return {"status": "NO_BARS_TODAY"}
    return {"status": "OK", "day_open": today[0]["o"],
            "day_high": max(b["h"] for b in today), "day_low": min(b["l"] for b in today)}


# ---------------------------------------------------------------- HTF bias
# Kept deliberately simple and documented rather than importing
# regime_mtf.mtf_alignment (a live-path function hardcoded to 1m/3m/5m/15m/
# 30m -- this brief explicitly wants 1D/4H/1H/30m/15m, a different TF set,
# so extending that function would mean editing a live file for a shape it
# was never designed to cover). Per-TF read: close vs its own EMA(20) sign,
# weighted more heavily for higher timeframes -- the same "higher TF
# dominates" principle regime_mtf.mtf_alignment already uses, reimplemented
# for this TF set instead of bolted onto that one.
_HTF_ORDER = ("15m", "30m", "1h", "4h", "1d")
_HTF_WEIGHT = {"15m": 0.10, "30m": 0.15, "1h": 0.20, "4h": 0.25, "1d": 0.30}


def _ema(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    e = mean(values[:period])
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def _tf_bias(bars: list[dict]) -> float:
    closes = [b["c"] for b in bars if _num(b.get("c")) is not None]
    if len(closes) < 21:
        return 0.0
    e = _ema(closes[-40:] if len(closes) > 40 else closes, 20)
    if e is None or e == 0:
        return 0.0
    return max(-1.0, min(1.0, (closes[-1] - e) / e * 20))   # scaled, clamped to [-1,1]


def htf_bias(bars_by_tf: dict[str, list[dict]]) -> dict:
    """`bars_by_tf`: whichever of 15m/30m/1h/4h/1d the caller can supply
    (already resampled+truncated to <= T) -- timeframes with too little
    history to compute an EMA(20) are simply skipped, weight redistributed
    over what's available."""
    per_tf = {}
    wsum = 0.0
    signed = 0.0
    for tf in _HTF_ORDER:
        bars = bars_by_tf.get(tf) or []
        b = _tf_bias(bars)
        per_tf[tf] = round(b, 3)
        if bars and len(bars) >= 21:
            w = _HTF_WEIGHT[tf]
            signed += b * w
            wsum += w
    if wsum == 0:
        return {"bias": RANGE, "score": 0.0, "per_tf": per_tf, "note": "insufficient history on every HTF"}
    score = signed / wsum
    if score > 0.15:
        bias = BULLISH
    elif score < -0.15:
        bias = BEARISH
    elif abs(score) < 0.05:
        bias = RANGE
    else:
        bias = TRANSITION
    return {"bias": bias, "score": round(score, 3), "per_tf": per_tf}
