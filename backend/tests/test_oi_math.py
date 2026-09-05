"""
app/engines/oi_math.py — max_pain_strike(), extracted from three previously
independent (but byte-for-byte identical) implementations: oi_options_engine
.run_oi_options_engine's nested max_pain() closure, autoscalp/runner.py
._chain_oi_quality(), and turning_point_engine.py._oi_metrics(). This test
proves equivalence against reference re-implementations of the ORIGINAL
inline formulas (not just re-testing the extracted function against itself),
plus the standalone edge cases.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines.oi_math import max_pain_strike   # noqa: E402


def _reference_dict_form(rows):
    """Re-derivation of oi_options_engine.py's ORIGINAL nested closure,
    operating on dict rows (its actual shape) rather than tuples."""
    best_k, best_val = None, float("inf")
    for k in rows:
        pain = 0.0
        for r in rows:
            pain += max(0, k["strike"] - r["strike"]) * r["ce_oi"]
            pain += max(0, r["strike"] - k["strike"]) * r["pe_oi"]
        if pain < best_val:
            best_val, best_k = pain, k["strike"]
    return best_k


def _reference_tuple4_form(rows):
    """Re-derivation of autoscalp/runner.py._chain_oi_quality's ORIGINAL
    inline loop, operating on its actual 4-tuple shape."""
    best = float("inf")
    out = None
    for k, _, _, _ in rows:
        pain = sum(max(0.0, k - ki) * ce + max(0.0, ki - k) * pe for ki, ce, pe, _ in rows)
        if pain < best:
            best, out = pain, k
    return out


def test_matches_original_dict_shaped_formula():
    rows = [{"strike": 100, "ce_oi": 500, "pe_oi": 200},
            {"strike": 200, "ce_oi": 300, "pe_oi": 900},
            {"strike": 300, "ce_oi": 1200, "pe_oi": 150},
            {"strike": 400, "ce_oi": 50, "pe_oi": 1800}]
    expected = _reference_dict_form(rows)
    got = max_pain_strike((r["strike"], r["ce_oi"], r["pe_oi"]) for r in rows)
    assert got == expected


def test_matches_original_tuple4_shaped_formula():
    rows = [(24000, 1200.0, 300.0, True), (24050, 800.0, 900.0, True),
            (24100, 300.0, 1500.0, True), (24150, 100.0, 2200.0, True)]
    expected = _reference_tuple4_form(rows)
    got = max_pain_strike((k, ce, pe) for k, ce, pe, _ in rows)
    assert got == expected


def test_random_chains_match_both_reference_forms():
    rng = random.Random(42)
    for _ in range(30):
        n = rng.randint(3, 12)
        strikes = sorted(rng.sample(range(100, 100 + n * 50, 50), n))
        rows_dict = [{"strike": float(s), "ce_oi": float(rng.randint(0, 5000)),
                     "pe_oi": float(rng.randint(0, 5000))} for s in strikes]
        rows4 = [(r["strike"], r["ce_oi"], r["pe_oi"], True) for r in rows_dict]
        expected_a = _reference_dict_form(rows_dict)
        expected_b = _reference_tuple4_form(rows4)
        assert expected_a == expected_b, "the two ORIGINAL formulas disagreed -- test bug, not extraction bug"
        got = max_pain_strike((r["strike"], r["ce_oi"], r["pe_oi"]) for r in rows_dict)
        assert got == expected_a


def test_empty_rows_returns_none():
    assert max_pain_strike([]) is None


def test_single_strike_returns_that_strike():
    assert max_pain_strike([(24000.0, 500.0, 300.0)]) == 24000.0


def test_all_zero_oi_ties_on_first_strike():
    """Every candidate pays zero pain when all OI is zero -- first-encountered
    wins, matching every original implementation's strict `<` comparison."""
    rows = [(100.0, 0.0, 0.0), (200.0, 0.0, 0.0), (300.0, 0.0, 0.0)]
    assert max_pain_strike(rows) == 100.0


def test_symmetric_oi_picks_the_middle_strike():
    # Symmetric CE/PE OI around the middle strike -> pain is minimized there.
    rows = [(100.0, 100.0, 900.0), (200.0, 900.0, 900.0), (300.0, 900.0, 100.0)]
    assert max_pain_strike(rows) == 200.0
