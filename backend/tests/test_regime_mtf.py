"""P3 regime detector + MTF alignment."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines.regime_mtf import detect_regime, mtf_alignment


def bars(closes, wick=0.3, vol=1000):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"t": f"2026-08-27T{9 + (15 + i * 5) // 60:02d}:{(15 + i * 5) % 60:02d}:00",
                    "o": round(o, 2), "h": round(max(o, c) + wick, 2),
                    "l": round(min(o, c) - wick, 2), "c": round(c, 2), "v": vol})
        prev = c
    return out


def _uptrend(n=60, slope=0.8):
    return [100 + i * slope + (0.3 if i % 2 else -0.3) for i in range(n)]


def _downtrend(n=60, slope=0.8):
    return [200 - i * slope + (0.3 if i % 2 else -0.3) for i in range(n)]


def _range(n=60, mid=102.5, amp=0.25):
    return [mid + amp * math.sin(i * 0.55) + (0.03 if i % 2 else -0.03) for i in range(n)]


def test_trending_up_detected():
    r = detect_regime({"5m": bars(_uptrend()), "15m": bars(_uptrend(30, 2.4))})
    assert r["regime"] == "TRENDING_UP" and r["trend"] == "UP" and r["adx"] >= 25


def test_trending_down_detected():
    r = detect_regime({"5m": bars(_downtrend())})
    assert r["regime"] == "TRENDING_DOWN" and r["trend"] == "DOWN"


def test_range_detected():
    r = detect_regime({"5m": bars(_range(), wick=0.05)})
    assert r["regime"] in ("RANGE", "LOW_VOLATILITY")
    assert r["adx"] < 25


def test_high_volatility_overlay():
    # long calm stretch then a sustained violent expansion tail
    closes = [100 + (0.15 if i % 2 else -0.15) for i in range(40)]
    closes += [100, 111, 92, 113, 90, 115, 88, 116, 87, 118, 86, 120, 85, 122]
    r = detect_regime({"5m": bars(closes, wick=1.5)})
    assert r["vol_state"] == "HIGH"
    assert r["regime"] in ("HIGH_VOLATILITY", "BREAKOUT_REGIME", "UNSTABLE", "REVERSAL_REGIME")


def test_unstable_on_chop():
    closes = [100 + (3.5 if i % 2 else -3.5) for i in range(50)]   # violent alternation
    r = detect_regime({"5m": bars(closes)})
    assert r["regime"] in ("UNSTABLE", "REVERSAL_REGIME", "HIGH_VOLATILITY")


def test_insufficient_data_unstable():
    r = detect_regime({"5m": bars([100, 101, 102, 103, 104])})
    assert r["regime"] == "UNSTABLE" and r["confidence"] == 0.0


def test_regime_deterministic():
    d = {"5m": bars(_uptrend()), "15m": bars(_uptrend(30, 2.4))}
    assert detect_regime(d) == detect_regime(d)


def test_mtf_alignment_bullish_stack():
    up = _uptrend
    d = {"1m": bars(up(80, 0.3)), "3m": bars(up(60, 0.6)), "5m": bars(up(50, 1.0)),
         "15m": bars(up(30, 2.5)), "30m": bars(up(20, 4.0))}
    a = mtf_alignment(d)
    assert a["direction"] == "BULLISH" and a["alignment"] > 40
    assert not a["conflict"]
    assert all(a["per_tf"][tf]["dir"] == 1 for tf in ("5m", "15m", "30m"))


def test_mtf_alignment_bearish_stack():
    dn = _downtrend
    d = {"1m": bars(dn(80, 0.3)), "3m": bars(dn(60, 0.6)), "5m": bars(dn(50, 1.0)),
         "15m": bars(dn(30, 2.5)), "30m": bars(dn(20, 4.0))}
    a = mtf_alignment(d)
    assert a["direction"] == "BEARISH" and a["alignment"] < -40


def test_mtf_conflict_damped():
    # HTF strongly up, fast TFs sharply down -> conflict, alignment damped
    d = {"1m": bars(_downtrend(80, 1.2)), "3m": bars(_downtrend(60, 1.4)),
         "5m": bars(_uptrend(50, 0.4)), "15m": bars(_uptrend(30, 3.0)),
         "30m": bars(_uptrend(20, 4.5))}
    a = mtf_alignment(d)
    assert a["conflict"] is True
    assert abs(a["alignment"]) < 45          # damped vs a clean stack


def test_mtf_neutral_on_range():
    d = {tf: bars(_range(n)) for tf, n in (("1m", 80), ("3m", 60), ("5m", 50), ("15m", 30), ("30m", 20))}
    a = mtf_alignment(d)
    assert a["direction"] == "NONE"
