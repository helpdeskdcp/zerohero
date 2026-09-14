"""
app/liquidity_sweep/model_check.py -- leakage-safe joint feature check.
Synthetic cases: (1) a real, purely-JOINT effect (XOR of two features) that
every univariate test in feature_analysis.py would miss, to prove this
module CAN catch what univariate tests can't; (2) pure noise, to prove it
does NOT falsely claim a joint effect exists when none does; (3) a
chronological-split sanity check (no shuffling).
"""
import random
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.model_check import run_leakage_safe_model_check  # noqa: E402


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


def test_pure_noise_does_not_falsely_pass_the_bar():
    random.seed(42)
    samples = []
    for _ in range(1000):
        outcome = "WIN" if random.random() < 0.5 else "LOSS"
        samples.append(_fake(
            outcome, setup_score=random.gauss(50, 10), rsi14=random.gauss(50, 10),
            adx=random.gauss(25, 5), atr14=random.gauss(20, 5),
            direction=random.choice(["BULLISH", "BEARISH"]),
            regime=random.choice(["BULLISH", "BEARISH", "RANGE", "TRANSITION"]),
        ))
    result = run_leakage_safe_model_check(samples)
    assert result["status"] == "OK"
    assert result["passes_bar"] is False


def test_a_real_joint_xor_effect_is_detected():
    """rsi14 alone and adx alone carry zero marginal information about the
    outcome -- only their XOR-like combination does. A model with real
    interaction capacity (the random forest) should catch this; a purely
    univariate test could not."""
    random.seed(7)
    samples = []
    for _ in range(1200):
        a = random.random() < 0.5
        b = random.random() < 0.5
        win = a != b   # XOR
        samples.append(_fake(
            "WIN" if win else "LOSS",
            rsi14=70.0 if a else 30.0,
            adx=35.0 if b else 15.0,
            setup_score=random.gauss(50, 10),
        ))
    result = run_leakage_safe_model_check(samples)
    assert result["status"] == "OK"
    # forest should pick up the interaction on this large, clean synthetic signal
    assert result["forest_oos_auc"] is not None and result["forest_oos_auc"] > 0.6


def test_identical_features_across_all_rows_cannot_pass_the_bar():
    """Every row has the exact same feature values regardless of outcome --
    a model literally has no column to key off of, so however the split
    lands it must not manufacture a "passing" result out of nothing."""
    samples = [_fake("WIN" if i % 2 == 0 else "LOSS") for i in range(800)]
    result = run_leakage_safe_model_check(samples)
    assert result["status"] == "OK"
    assert result["n_train"] == 400 and result["n_val"] == 200 and result["n_oos"] == 200
    assert result["passes_bar"] is False
