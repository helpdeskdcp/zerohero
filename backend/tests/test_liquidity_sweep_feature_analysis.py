"""
app/liquidity_sweep/feature_analysis.py -- Phase 1/2 statistical
discrimination. Synthetic, hand-constructed cases for each category so the
categorization RULE itself is verified, independent of any real backtest
data (the real-data run is a separate script/report, not a unit test).
"""
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.feature_analysis import (  # noqa: E402
    analyze_categorical, analyze_continuous, analyze_features, cohens_d, cramers_v_and_p,
)


def test_cohens_d_large_separation_gives_large_d():
    a = [10.0] * 30
    b = [0.0] * 30
    assert cohens_d(a, b) is None   # zero pooled variance -- degenerate, correctly None not a huge number


def test_cohens_d_realistic_separation():
    a = [5.0, 5.5, 4.5, 6.0, 5.2, 4.8, 5.1, 5.3, 4.9, 5.4]
    b = [3.0, 3.5, 2.5, 4.0, 3.2, 2.8, 3.1, 3.3, 2.9, 3.4]
    d = cohens_d(a, b)
    assert d is not None and d > 1.5   # clearly separated groups -> large effect


def test_cramers_v_perfect_association():
    win = ["A"] * 20
    loss = ["B"] * 20
    v, p = cramers_v_and_p(win, loss)
    # scipy applies Yates' continuity correction to 2x2 tables by default,
    # so a perfect association comes out just under 1.0, not exactly 1.0
    assert v >= 0.9
    assert p < 0.001


def test_cramers_v_no_association():
    win = ["A", "B"] * 20
    loss = ["A", "B"] * 20
    v, p = cramers_v_and_p(win, loss)
    assert v < 0.1


def test_analyze_continuous_strong_when_clearly_separated():
    import random
    random.seed(0)
    win = [random.gauss(70, 5) for _ in range(200)]
    loss = [random.gauss(40, 5) for _ in range(200)]
    verdict = analyze_continuous("mock_score", win, loss)
    assert verdict.category == "STRONG"
    assert abs(verdict.effect_size) >= 0.5


def test_analyze_continuous_none_when_identical_distributions():
    import random
    random.seed(1)
    win = [random.gauss(50, 10) for _ in range(300)]
    loss = [random.gauss(50, 10) for _ in range(300)]
    verdict = analyze_continuous("mock_noise", win, loss)
    assert verdict.category in ("NONE", "MISLEADING")   # same population -- should not be STRONG/WEAK
    assert abs(verdict.effect_size) < 0.2


def test_analyze_continuous_misleading_when_tiny_effect_but_huge_n():
    """A trivial 0.5-point mean shift on a huge sample can hit p<0.05 with a
    negligible Cohen's d -- must be flagged MISLEADING, never WEAK/STRONG."""
    import random
    random.seed(2)
    win = [random.gauss(50.3, 10) for _ in range(4000)]
    loss = [random.gauss(50.0, 10) for _ in range(4000)]
    verdict = analyze_continuous("mock_trivial_shift", win, loss)
    assert abs(verdict.effect_size) < 0.2
    if verdict.p_value is not None and verdict.p_value < 0.05:
        assert verdict.category == "MISLEADING"


def test_analyze_continuous_unavailable_when_mostly_missing():
    win = [None] * 90 + [50.0] * 10
    loss = [None] * 90 + [45.0] * 10
    verdict = analyze_continuous("mock_sparse", win, loss)
    assert verdict.category == "UNAVAILABLE"
    assert verdict.missing_rate > 0.3


def test_analyze_categorical_strong_when_outcomes_diverge_by_category():
    win = ["BULLISH"] * 80 + ["BEARISH"] * 20
    loss = ["BULLISH"] * 20 + ["BEARISH"] * 80
    verdict = analyze_categorical("mock_direction", win, loss)
    assert verdict.category == "STRONG"


@dataclass
class _FakeSample:
    outcome: str
    setup_score: float
    passed_checks: int
    htf_score: float
    sweep_reaction: float
    sweep_reaction_atr_ratio: float
    bars_to_reclaim: int
    rsi14: float
    macd: float
    adx: float
    atr14: float
    volume: float
    volume_ratio: float
    probability: float
    confidence: float
    rr: float
    risk_amount: float
    reward_amount: float
    direction: str
    regime: str
    structure_type: str
    cisd: bool
    fvg: bool
    order_block: bool
    sweep_kind: str
    level_source: str
    chk_structure_aligned: bool
    chk_secondary_confirmation: bool
    chk_htf_aligned: bool
    chk_vwap_aligned: bool
    chk_ema_aligned: bool
    chk_rsi_not_extreme: bool
    chk_adx_trending: bool
    chk_volume_above_average: bool
    above_vwap: bool
    above_ema20: bool
    ema20_gt_ema50: bool
    time_bucket: str
    day_of_week: str

    def to_dict(self):
        return self.__dict__


def _fake(outcome, **overrides):
    base = dict(
        setup_score=50.0, passed_checks=4, htf_score=0.0, sweep_reaction=1.0,
        sweep_reaction_atr_ratio=0.5, bars_to_reclaim=1, rsi14=50.0, macd=0.0, adx=20.0,
        atr14=10.0, volume=1000.0, volume_ratio=1.0, probability=0.5, confidence=50.0,
        rr=2.0, risk_amount=10.0, reward_amount=20.0, direction="BULLISH", regime="RANGE",
        structure_type="BOS", cisd=True, fvg=False, order_block=False, sweep_kind="LOWER_SWEEP",
        level_source="PDL", chk_structure_aligned=True, chk_secondary_confirmation=True,
        chk_htf_aligned=False, chk_vwap_aligned=True, chk_ema_aligned=True,
        chk_rsi_not_extreme=True, chk_adx_trending=False, chk_volume_above_average=None,
        above_vwap=True, above_ema20=True, ema20_gt_ema50=True, time_bucket="MID",
        day_of_week="Monday",
    )
    base.update(overrides)
    return _FakeSample(outcome=outcome, **base)


def test_analyze_features_end_to_end_runs_and_excludes_timeouts():
    samples = [_fake("WIN") for _ in range(30)] + [_fake("LOSS") for _ in range(30)] + [_fake("TIMEOUT") for _ in range(10)]
    report = analyze_features(samples)
    assert report["n_total_signals"] == 70
    assert report["n_win"] == 30 and report["n_loss"] == 30 and report["n_timeout"] == 10
    assert "setup_score" in report["features"]
    assert "direction" in report["features"]
    for v in report["features"].values():
        assert v["category"] in ("STRONG", "WEAK", "REDUNDANT", "MISLEADING", "UNAVAILABLE", "NONE")


def test_analyze_features_flags_redundancy_between_correlated_features():
    import random
    random.seed(3)
    samples = []
    for _ in range(150):
        base = random.gauss(70, 5)
        samples.append(_fake("WIN", setup_score=base, confidence=base + 1.0))
    for _ in range(150):
        base = random.gauss(30, 5)
        samples.append(_fake("LOSS", setup_score=base, confidence=base + 1.0))
    report = analyze_features(samples)
    pairs = report["redundancy_pairs"]
    names_in_pairs = {p["a"] for p in pairs} | {p["b"] for p in pairs}
    assert "setup_score" in names_in_pairs and "confidence" in names_in_pairs
