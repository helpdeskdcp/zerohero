import copy

import numpy as np
import pytest

from app.hybrid_orderflow.policy import PolicyError, load_policy, validate
from app.strategy_lab import gate as G

PR = load_policy()["promotion"]


def mk(n, mean, sd=30.0, per_day=4, seed=1):
    rng = np.random.default_rng(seed)
    g = rng.normal(mean, sd, n)
    days = np.repeat(np.arange(n // per_day + 1), per_day)[:n]
    return g, days


def test_real_edge_passes():
    g, d = mk(1600, 6.0, sd=12.0)
    r = G.evaluate(g, d, 2.0, PR)
    assert r["verdict"] == G.PROMOTE and r["failed"] == [] and r["net_ci95_bps"][0] > 0


def test_zero_edge_is_rejected():
    g, d = mk(1600, 2.0, sd=30.0)                        # gross == cost -> net zero
    r = G.evaluate(g, d, 2.0, PR)
    assert r["verdict"] != G.PROMOTE and ("NET_EXPECTANCY" in r["failed"] or "NET_CI_LOWER_NOT_POSITIVE" in r["failed"])


def test_negative_edge_is_rejected():
    g, d = mk(1600, -3.0)
    assert G.evaluate(g, d, 2.0, PR)["verdict"] == G.REJECT


def test_small_sample_is_insufficient_not_promoted():
    g, d = mk(40, 50.0, sd=1.0)
    r = G.evaluate(g, d, 2.0, PR)
    assert r["verdict"] == G.INSUFFICIENT and r["failed"]


def test_few_days_is_insufficient():
    g = np.full(400, 20.0)
    r = G.evaluate(g, np.repeat(np.arange(5), 80), 2.0, PR)
    assert r["verdict"] == G.INSUFFICIENT and "DAYS" in r["failed"][0]


def test_edge_in_only_one_fold_fails_walk_forward():
    g, d = mk(1600, -1.0, sd=10.0)
    g[:400] += 40.0                                      # profitable only in the first quarter
    r = G.evaluate(g, d, 2.0, PR)
    assert r["verdict"] != G.PROMOTE and ("WALK_FORWARD_FOLDS" in r["failed"] or "LAST_FOLD_NEGATIVE" in r["failed"])


def test_last_fold_negative_blocks_promotion():
    g, d = mk(1600, 6.0, sd=12.0)
    g[1200:] -= 12.0                                     # edge decayed in the most recent period
    r = G.evaluate(g, d, 2.0, PR)
    assert r["verdict"] != G.PROMOTE and "LAST_FOLD_NEGATIVE" in r["failed"]


def test_cost_stress_blocks_thin_edge():
    g, d = mk(4000, 3.3, sd=6.0)                         # net +1.3 at 2 bps, negative at 1.5x cost 3 bps? 3.3-3=0.3>0 -> use larger cost
    r = G.evaluate(g, d, 2.6, PR)                        # net +0.7 (<min_net_mean) and stress 3.3-3.9 < 0
    assert r["verdict"] != G.PROMOTE and "COST_STRESS" in r["failed"]


def test_concentration_blocks_lucky_outliers():
    g, d = mk(1600, 0.0, sd=5.0)
    g[:5] += 4000.0                                      # five giant winners carry the whole result
    r = G.evaluate(g, d, 2.0, PR)
    assert r["verdict"] != G.PROMOTE and "CONCENTRATION" in r["failed"]


def test_gate_is_deterministic():
    g, d = mk(1600, 6.0, sd=12.0)
    assert G.evaluate(g, d, 2.0, PR) == G.evaluate(g, d, 2.0, PR)


def test_empty_input():
    assert G.evaluate([], [], 2.0, PR)["failed"] == ["NO_TRADES"]


@pytest.mark.parametrize("mutate,needle", [
    (lambda p: p["promotion"].__setitem__("paper_only", False), "paper_only"),
    (lambda p: p["promotion"].__setitem__("min_trades", 10), "min_trades"),
    (lambda p: p["promotion"].__setitem__("min_positive_folds", 2), "majority"),
    (lambda p: p["promotion"].__setitem__("last_fold_must_be_positive", False), "last_fold"),
    (lambda p: p["promotion"].__setitem__("cost_stress_multiplier", 0.5), "cost_stress"),
    (lambda p: p.pop("promotion"), "missing section"),
])
def test_promotion_policy_fails_closed(mutate, needle):
    p = copy.deepcopy(load_policy())
    mutate(p)
    with pytest.raises(PolicyError, match=needle):
        validate(p)
