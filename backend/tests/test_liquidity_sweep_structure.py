"""
app/liquidity_sweep/structure.py -- PDH/PDL, swings, equal levels, HTF bias.
Pure functions, no I/O.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep import structure


def _bar(t, o, h, l, c, v=1000):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _session(day, n=20, base=100.0):
    """A flat-ish session with a small oscillation so swings can form."""
    out = []
    for i in range(n):
        wobble = (i % 4) - 1.5
        out.append(_bar(f"2026-09-{day:02d}T{9 + i // 12:02d}:{(i % 12) * 5:02d}:00Z",
                        base + wobble, base + wobble + 1, base + wobble - 1, base + wobble))
    return out


def test_swings_detects_a_clean_fractal_high_and_low():
    bars = [_bar(f"t{i}", 10, 10, 10, 10) for i in range(3)]
    bars[1] = _bar("t1", 10, 15, 5, 10)   # bar 1 is both the highest high and lowest low of the window
    pts = structure.swings(bars, left=1, right=1)
    kinds = {p.kind for p in pts}
    assert kinds == {"H", "L"}
    assert all(p.index == 1 for p in pts)


def test_swings_never_confirms_the_last_right_bars():
    bars = [_bar(f"t{i}", 10, 10 + (5 if i == 8 else 0), 10, 10) for i in range(10)]
    pts = structure.swings(bars, left=2, right=2)
    assert all(p.index <= 7 for p in pts)   # index 8/9 can't be confirmed yet (right=2 needs 2 bars after)


def test_swing_structure_labels_hh_hl_lh_ll():
    from app.liquidity_sweep.structure import SwingPoint
    pts = [SwingPoint(0, 100, "L", "t0"), SwingPoint(1, 110, "H", "t1"),
           SwingPoint(2, 105, "L", "t2"), SwingPoint(3, 115, "H", "t3")]
    labels = structure.swing_structure_labels(pts)
    assert labels[2]["label"] == "HL"   # 105 > prior low 100
    assert labels[3]["label"] == "HH"   # 115 > prior high 110


def test_equal_levels_clusters_close_swing_highs():
    from app.liquidity_sweep.structure import SwingPoint
    pts = [SwingPoint(0, 24100.0, "H", "t0"), SwingPoint(5, 24105.0, "H", "t1"),
           SwingPoint(10, 23800.0, "L", "t2")]
    eq = structure.equal_levels(pts, tolerance_pct=0.05)
    assert len(eq) == 1
    assert eq[0].kind == "EQUAL_HIGH"
    assert eq[0].touches == 2


def test_equal_levels_requires_at_least_two_touches():
    from app.liquidity_sweep.structure import SwingPoint
    pts = [SwingPoint(0, 24100.0, "H", "t0"), SwingPoint(5, 25000.0, "H", "t1")]
    assert structure.equal_levels(pts) == []


def test_pdh_pdl_only_uses_sessions_strictly_before_as_of():
    bars = _session(1, base=100.0) + _session(2, base=110.0) + _session(3, base=120.0)
    pdh = structure.pdh_pdl(bars, as_of_session="2026-09-03")
    assert pdh.status == "OK"
    assert pdh.prev_session_date == "2026-09-02"
    assert abs(pdh.prev_high - 112.5) < 0.01
    assert abs(pdh.prev_low - 107.5) < 0.01


def test_pdh_pdl_never_looks_at_the_current_or_future_session():
    bars = _session(1, base=100.0) + _session(2, base=110.0)
    pdh_day1 = structure.pdh_pdl(bars[:20], as_of_session="2026-09-01")   # only session 1's own bars
    assert pdh_day1.status == "NO_PRIOR_SESSION"


def test_htf_bias_bullish_when_price_well_above_ema():
    rising = [_bar(f"t{i}", 100 + i, 100 + i + 1, 100 + i - 1, 100 + i) for i in range(40)]
    out = structure.htf_bias({"15m": rising, "1d": rising})
    assert out["bias"] == structure.BULLISH


def test_htf_bias_range_with_insufficient_history():
    out = structure.htf_bias({"15m": [_bar("t0", 100, 101, 99, 100)]})
    assert out["bias"] == structure.RANGE
    assert "insufficient history" in out["note"]
