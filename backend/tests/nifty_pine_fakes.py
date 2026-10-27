"""Synthetic NIFTY 5-minute candle builders + a thin runner wrapper for tests/test_nifty_expiry_pine.py (PineTS engine; NOT TradingView)."""
import math, os
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "research", "nifty_expiry_pine", "nifty_expiry_intelligence.pine")
nan = float("nan")


def day_start(i, base="2026-03-02"):
    """i-th business day (Mon-Fri) from `base` at 09:15 IST - strictly increasing, no duplicates."""
    d = datetime.fromisoformat(base + "T09:15:00+05:30"); n = 0
    while n < i:
        d += timedelta(days=1)
        if d.weekday() < 5: n += 1
    return d


def ts(d, k): return int((d + timedelta(minutes=5 * k)).timestamp() * 1000)


def bar(o, h, l, c): return (o, max(h, o, c), min(l, o, c), c)


def flat_day(base, rng=100.0):
    """75 bars: range exactly `rng`, open == close == base, so TR == rng and no gap into the next day."""
    out = []; lo, hi = base - rng / 2, base + rng / 2
    pts = [base] + [hi, lo] * 2 + [base]
    path = []
    for a, b in zip(pts, pts[1:]): path += [a + (b - a) * j / 15 for j in range(15)]
    path = path[:74] + [base]
    prev = base
    for k, p in enumerate(path[:75]):
        out.append(bar(prev, max(prev, p), min(prev, p), p)); prev = p
    # force the day extremes exactly
    o, h, l, c = out[15]; out[15] = (o, hi, l, c)
    o, h, l, c = out[45]; out[45] = (o, h, lo, c)
    return out


def rows_from_days(days):
    """days: list of (day_index, [75 bars])  -> candle dicts for the runner"""
    rows = []
    for di, bars in days:
        d = day_start(di)
        for k, (o, h, l, c) in enumerate(bars):
            rows.append({"time": ts(d, k), "open": o, "high": h, "low": l, "close": c, "volume": 0})
    return rows


def warmup(n=8, base=24000.0): return [(i, flat_day(base)) for i in range(n)]


def first_candles(open_px, kind):
    """kind: 'bear' strong bearish, 'bull' strong bullish, 'doji'"""
    if kind == "bear": return bar(open_px, open_px + 2, open_px - 20, open_px - 18)
    if kind == "bull": return bar(open_px, open_px + 20, open_px - 2, open_px + 18)
    return bar(open_px, open_px + 10, open_px - 10, open_px + 1)


def drift(start, n, step, wiggle=0.5):
    out, p = [], start
    for k in range(n):
        q = p + step; out.append(bar(p, max(p, q) + wiggle, min(p, q) - wiggle, q)); p = q
    return out


def run(rows, inputs=None, bars_tf="5"):
    from app.smart_chart import runner
    r = runner.execute({"op": "run", "source": open(SRC, encoding="utf-8").read(), "candles": rows, "tf": bars_tf, "symbol": "NIFTY", "inputs": inputs or {}, "props": {}}, "pytest", timeout=300, count=False)
    assert r.get("ok"), r.get("diagnostics")
    return r


def plots(res):
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
    import pine_nifty_harness as H
    return H.unpack({p["title"]: [nan if v is None else float(v) for v in p["values"]] for p in res["plots"]})


def walk(pts, nb, start=None):
    """polyline of closes: nb bars per segment, tiny wicks. start=None -> pts[0] is the first price."""
    out, p = [], (pts[0] if start is None else start)
    for q in (pts[1:] if start is None else pts):
        for k in range(1, nb + 1):
            c = p + (q - p) * k / nb; o = p + (q - p) * (k - 1) / nb
            out.append(bar(o, max(o, c) + 0.3, min(o, c) - 0.3, c))
        p = q
    return out


def pad(bars, n=75):
    bars = list(bars)
    while len(bars) < n: bars.append(bar(bars[-1][3], bars[-1][3] + 0.3, bars[-1][3] - 0.3, bars[-1][3] - 0.05))
    return bars[:n]


def mirror(bars, k=48000.0): return [(k - o, k - l, k - h, k - c) for (o, h, l, c) in bars]


def stress_day(respect=True):
    """previous-day low (support) = 23950 -> zone [23930, 23970]. Test from above -> displacement break -> rejection from below."""
    b = [bar(24000, 24001, 23999, 24000.2)]
    for k in range(1, 20): b.append(bar(24000 + (k % 2) * 0.4, 24001, 23999, 24000 - (k % 2) * 0.4))
    seg = walk([24000, 23976], 8); b += seg; p = seg[-1][3]
    b.append(bar(p, p + 0.3, 23968.0 if respect else 23972.5, 23973.0))
    b += walk([23973, 23995], 5, start=23973); b += walk([23995, 23984], 3, start=23995)
    b.append(bar(23984, 23985, 23927, 23928))          # displacement through the zone
    b.append(bar(23928, 23933, 23921, 23924))          # rejection from below  (bar index 46)
    b += walk([23924, 23900], 6, start=23924)
    return pad(b)
