"""
app/structural_break/drift.py -- pure statistical drift primitives.
No DB, no network, no other engine involved.
"""
import math
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.structural_break.drift import (  # noqa: E402
    CusumDetector, PageHinkleyDetector, PsiTracker, RollingKS, RollingZScore,
    fit_baseline, ks_2sample,
)

random.seed(7)


# --------------------------------------------------------------------------- #
#  CUSUM                                                                       #
# --------------------------------------------------------------------------- #
def test_cusum_stays_quiet_on_stable_noise_around_baseline():
    det = CusumDetector(baseline_mean=0.0, baseline_sigma=1.0)
    triggers = 0
    for _ in range(500):
        r = det.update(random.gauss(0.0, 1.0))
        triggers += r["triggered"]
    # a handful of spurious triggers over 500 samples is expected noise;
    # it must not be firing on every observation
    assert triggers < 15, f"CUSUM fired too often on pure noise: {triggers}/500"


def test_cusum_detects_a_sustained_mean_shift():
    det = CusumDetector(baseline_mean=0.0, baseline_sigma=1.0)
    for _ in range(100):
        det.update(random.gauss(0.0, 1.0))
    fired = False
    for _ in range(200):
        r = det.update(random.gauss(2.5, 1.0))   # a real, sustained 2.5-sigma shift
        if r["triggered"]:
            fired = True
            assert r["direction"] == "up"
    assert fired, "CUSUM never fired on a sustained 2.5-sigma mean shift"


# --------------------------------------------------------------------------- #
#  Page-Hinkley                                                                #
# --------------------------------------------------------------------------- #
def test_page_hinkley_false_alarm_rate_over_pure_noise_is_low():
    # Calibration check for the window=100/lambda_sigmas=4.0 defaults: over
    # 200 pure-noise samples (no real drift at all) with no reset, false
    # alarms should be rare. This is the reproducible simulation the
    # defaults in drift.py's docstring are based on.
    false_alarms = 0
    trials = 60
    for s in range(trials):
        random.seed(s)
        det = PageHinkleyDetector(baseline_sigma=1.0, direction="increase")
        if any(det.update(random.gauss(0.0, 1.0))["triggered"] for _ in range(200)):
            false_alarms += 1
    assert false_alarms <= 3, f"false alarm rate too high: {false_alarms}/{trials} on pure noise"


def test_page_hinkley_ignores_a_single_moderate_outlier():
    # A single ~4-sigma bad observation (a genuinely isolated bad trade, not
    # an extreme/impossible value) should not by itself cross the
    # sqrt(window)-scaled threshold -- that's the actual "isolated error vs
    # persistent drift" distinction.
    det = PageHinkleyDetector(baseline_sigma=1.0, direction="increase")
    fired = False
    for i in range(200):
        x = 4.0 if i == 100 else random.gauss(0.0, 1.0)
        r = det.update(x)
        fired = fired or r["triggered"]
    assert not fired, "Page-Hinkley must not fire on a single moderate, isolated outlier"


def test_page_hinkley_detects_persistent_drift_upward():
    det = PageHinkleyDetector(baseline_sigma=1.0, direction="increase")
    for _ in range(50):
        det.update(random.gauss(0.0, 1.0))
    fired_at = None
    for i in range(300):
        x = random.gauss(3.0, 1.0)   # persistent upward drift, not a single spike
        r = det.update(x)
        if r["triggered"] and fired_at is None:
            fired_at = i
    assert fired_at is not None, "Page-Hinkley never detected a persistent upward drift"


def test_page_hinkley_decrease_direction_detects_a_falling_metric():
    # e.g. win rate dropping -- direction="decrease" should flip the sign internally
    det = PageHinkleyDetector(baseline_sigma=0.05, direction="decrease")
    for _ in range(50):
        det.update(0.55 + random.gauss(0, 0.02))
    fired = any(det.update(0.30 + random.gauss(0, 0.02))["triggered"] for _ in range(200))
    assert fired, "Page-Hinkley (decrease) never fired on a sustained win-rate drop"


# --------------------------------------------------------------------------- #
#  KS two-sample                                                               #
# --------------------------------------------------------------------------- #
def test_ks_2sample_same_distribution_does_not_trigger():
    a = [random.gauss(0, 1) for _ in range(200)]
    b = [random.gauss(0, 1) for _ in range(200)]
    r = ks_2sample(a, b)
    assert r["status"] == "ok" and r["triggered"] is False


def test_ks_2sample_shifted_distribution_triggers():
    a = [random.gauss(0, 1) for _ in range(200)]
    b = [random.gauss(0, 3) for _ in range(200)]   # same mean, very different spread
    r = ks_2sample(a, b)
    assert r["triggered"] is True


def test_rolling_ks_warms_up_then_flags_a_shift():
    rk = RollingKS(window=100)
    rk.fit([random.gauss(0, 1) for _ in range(300)])
    warm = rk.update(0.0)
    assert warm["status"] == "warming_up"
    last = None
    for _ in range(100):
        last = rk.update(random.gauss(5, 1))   # a real shift once the window fills
    assert last["status"] == "ok"
    assert last["triggered"] is True


# --------------------------------------------------------------------------- #
#  PSI                                                                         #
# --------------------------------------------------------------------------- #
def test_psi_stable_population_stays_below_moderate():
    # Uses the validated default window (300) -- see the class docstring:
    # smaller windows (e.g. 200) have enough of their own sampling noise to
    # occasionally cross "moderate" on genuinely unchanged data.
    psi = PsiTracker(n_bins=10)
    baseline = [random.gauss(0, 1) for _ in range(2000)]
    psi.fit(baseline)
    last = None
    for _ in range(psi.window):
        last = psi.update(random.gauss(0, 1))
    assert last["status"] == "ok"
    assert last["level"] == "stable", last


def test_psi_flags_a_shifted_population_as_significant():
    psi = PsiTracker(n_bins=10)
    baseline = [random.gauss(0, 1) for _ in range(2000)]
    psi.fit(baseline)
    last = None
    for _ in range(psi.window):
        last = psi.update(random.gauss(3, 1))   # whole population moved
    assert last["triggered"] is True and last["level"] == "significant"


# --------------------------------------------------------------------------- #
#  Rolling z-score                                                             #
# --------------------------------------------------------------------------- #
def test_rolling_zscore_flags_only_beyond_threshold():
    rz = RollingZScore(baseline_mean=100.0, baseline_sigma=10.0)
    assert rz.update(105.0)["triggered"] is False
    assert rz.update(135.0)["triggered"] is True   # 3.5 sigma


def test_fit_baseline_returns_mean_sigma_n():
    b = fit_baseline([1, 2, 3, 4, 5])
    assert b["n"] == 5 and b["mean"] == 3.0 and math.isclose(b["sigma"], math.sqrt(2), rel_tol=1e-6)
