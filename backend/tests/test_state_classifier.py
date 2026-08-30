"""P2 four-state classifier + false-breakout detector (integrates P1 S/R).
Uses realistic candles: small bar-to-bar moves so ATR stays small vs. the
range, and S/R levels are cleanly identifiable."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines.sr_engine import compute_sr
from app.engines.state_classifier import classify


def path(points, wick=0.3, vol=1000):
    """Turn a close-price path into overlapping OHLC candles (o = prev close)."""
    out, prev = [], points[0]
    for i, c in enumerate(points):
        o = prev
        hh = 9 + (15 + i * 5) // 60
        mm = (15 + i * 5) % 60
        out.append({"t": f"2026-08-27T{hh:02d}:{mm:02d}:00", "o": round(o, 2),
                    "h": round(max(o, c) + wick, 2), "l": round(min(o, c) - wick, 2),
                    "c": round(c, 2), "v": vol})
        prev = c
    return out


def zig(lo, hi, legs=6, step=1.5):
    """Smooth oscillation lo<->hi, `step` per bar (touches both edges cleanly)."""
    pts, cur, up = [], lo + step, True
    for _ in range(legs):
        target = hi if up else lo
        while (cur < target) if up else (cur > target):
            pts.append(round(cur, 2))
            cur += step if up else -step
        pts.append(target)
        up = not up
    return pts


def _cls(closes_or_bars, tail_bars=None, chain=None, cfg=None, tail_vols=None):
    if isinstance(closes_or_bars, list) and closes_or_bars and isinstance(closes_or_bars[0], dict):
        bars = closes_or_bars
    else:
        bars = path(closes_or_bars)
    if tail_bars:
        bars = bars + tail_bars
    sr = compute_sr({"5m": bars}, chain=chain, mode="index")
    return sr, classify({"5m": bars}, sr, chain=chain, config=cfg)


def _bar(o, h, l, c, v, i):
    hh = 9 + (15 + i * 5) // 60
    mm = (15 + i * 5) % 60
    return {"t": f"2026-08-27T{hh:02d}:{mm:02d}:00", "o": o, "h": h, "l": l, "c": c, "v": v}


def test_resistance_breakout():
    base = path(zig(100, 110), vol=1200)
    n = len(base)
    tail = [_bar(110.0, 112.3, 109.7, 112.0, 4200, n),
            _bar(112.0, 114.4, 111.6, 114.0, 3800, n + 1),
            _bar(114.0, 115.6, 113.5, 115.2, 3000, n + 2)]
    sr, r = _cls(base, tail_bars=tail)
    assert r["state"] == "RESISTANCE_BREAKOUT" and r["direction"] == "BULLISH"
    assert r["state_score"] >= 45
    assert r["false_risk"]["verdict"] in ("CLEAN", "SUSPECT")
    assert r["confirmation"]["close_beyond"] is True and r["confirmation"]["follow_through"] is True


def test_support_breakdown():
    base = path(zig(100, 110), vol=1200)
    n = len(base)
    tail = [_bar(100.0, 100.4, 97.6, 98.0, 4200, n),
            _bar(98.0, 98.3, 95.6, 96.0, 3800, n + 1),
            _bar(96.0, 96.5, 94.3, 94.8, 3000, n + 2)]
    sr, r = _cls(base, tail_bars=tail)
    assert r["state"] == "SUPPORT_BREAKDOWN" and r["direction"] == "BEARISH"
    assert r["state_score"] >= 45


def test_wick_only_false_breakout_is_rejected():
    base = path(zig(100, 110), vol=1200)
    n = len(base)
    # spikes to 114 but closes back inside at 108.5 (reclaimed), thin follow bar
    tail = [_bar(109.5, 114.2, 109.0, 108.5, 5200, n),
            _bar(108.5, 109.0, 107.0, 107.5, 1400, n + 1)]
    sr, r = _cls(base, tail_bars=tail)
    assert r["state"] != "RESISTANCE_BREAKOUT"
    brk = next((c for c in r.get("candidates", []) if c["state"] == "RESISTANCE_BREAKOUT"), None)
    assert brk is not None
    assert {"wick_only", "reclaimed"} & set(brk["false_risk"]["flags"])
    assert brk["false_risk"]["verdict"] == "LIKELY_FALSE"


def test_resistance_reversal():
    up = [round(100 + i * 1.0, 2) for i in range(20)]        # grind up to ~119
    base = path(up, vol=1100)
    n = len(base)
    tail = [_bar(119.0, 121.6, 118.7, 116.0, 4000, n),       # big upper wick, bearish close
            _bar(116.0, 116.4, 113.6, 114.0, 3200, n + 1),   # follow-through down
            _bar(114.0, 114.3, 112.6, 113.0, 2400, n + 2)]
    sr, r = _cls(base, tail_bars=tail)
    assert r["state"] == "RESISTANCE_REVERSAL" and r["direction"] == "BEARISH"
    assert r["confirmation"]["reversal_candle"] is True


def test_support_reversal():
    dn = [round(120 - i * 1.0, 2) for i in range(20)]        # grind down to ~101
    base = path(dn, vol=1100)
    n = len(base)
    tail = [_bar(101.0, 101.4, 98.4, 104.0, 4000, n),        # big lower wick, bullish close
            _bar(104.0, 106.4, 103.6, 106.0, 3200, n + 1),
            _bar(106.0, 107.4, 105.6, 107.0, 2400, n + 2)]
    sr, r = _cls(base, tail_bars=tail)
    assert r["state"] == "SUPPORT_REVERSAL" and r["direction"] == "BULLISH"
    assert r["confirmation"]["reversal_candle"] is True


def test_low_volume_breakout_flagged():
    base = path(zig(100, 110), vol=3000)
    n = len(base)
    tail = [_bar(110.0, 112.3, 109.7, 112.0, 350, n),        # thin
            _bar(112.0, 113.0, 111.5, 112.5, 300, n + 1)]
    sr, r = _cls(base, tail_bars=tail)
    brk = next((c for c in r.get("candidates", []) if c["state"] == "RESISTANCE_BREAKOUT"), None)
    assert brk and "low_volume" in brk["false_risk"]["flags"]


def test_no_sr_yields_none():
    r = classify({"5m": []}, {"status": "DATA_UNAVAILABLE"})
    assert r["state"] == "NONE" and r["direction"] == "NONE"


def test_deterministic():
    base = path(zig(100, 110))
    n = len(base)
    tail = [_bar(110.0, 112.3, 109.7, 112.0, 4200, n), _bar(112.0, 114.4, 111.6, 114.0, 3800, n + 1)]
    a = _cls(base, tail_bars=tail)[1]
    b = _cls(base, tail_bars=tail)[1]
    assert a == b


def test_score_and_components_shape():
    base = path(zig(100, 110))
    n = len(base)
    tail = [_bar(110.0, 112.3, 109.7, 112.0, 4200, n), _bar(112.0, 114.4, 111.6, 114.0, 3800, n + 1)]
    r = _cls(base, tail_bars=tail)[1]
    assert 0.0 <= r["state_score"] <= 100.0
    assert set(r["components"]) == {
        "price_action", "level_strength", "volume", "oi", "momentum", "vwap", "atr", "htf", "retest"}
    for v in r["components"].values():
        assert 0.0 <= v <= 1.0
