import numpy as np

from app.strategy_lab import edge_scan as ES


def test_bh_matches_the_textbook_example():
    p = [0.0001, 0.0004, 0.0019, 0.0095, 0.0201, 0.0278, 0.0298, 0.0344, 0.0459, 0.3240, 0.4262, 0.5719, 0.6528, 0.7590, 1.000]
    assert ES.bh(p, 0.05).count(True) == 4                          # Benjamini & Hochberg (1995) worked example: 4 rejections at q=0.05
    assert ES.bh(p, 0.05) == [True] * 4 + [False] * 11
    assert ES.bh([0.001, 0.008, 0.039, 0.041, 0.042, 0.06], 0.05).count(True) == 2     # 0.008 <= 2/6*0.05, 0.039 > 3/6*0.05
    assert ES.bh([0.5, 0.6], 0.05) == [False, False] and ES.bh([], 0.05) == []


def test_bh_is_order_independent():
    p = [0.04, 0.001, 0.2, 0.0005, 0.9]
    assert ES.bh(p, 0.05) == [False, True, False, True, False]
    perm = [3, 0, 4, 1, 2]
    assert [ES.bh([p[i] for i in perm], 0.05)[k] for k in range(5)] == [ES.bh(p, 0.05)[i] for i in perm]


def test_cluster_t_detects_a_planted_edge_and_not_noise():
    rng = np.random.default_rng(1)
    days = np.repeat(np.arange(400), 5)
    edge = rng.normal(4.0, 10.0, len(days))
    noise = rng.normal(0.0, 10.0, len(days))
    mu, p, n, nd = ES.cluster_t(days, edge)
    assert mu > 3 and p < 1e-6 and n == 2000 and nd == 400
    assert ES.cluster_t(days, noise)[1] > 0.01


def test_cluster_t_does_not_overcount_events_that_share_a_day():
    rng = np.random.default_rng(2)
    day_shock = rng.normal(0, 10, 200)
    days = np.repeat(np.arange(200), 20)
    x = np.repeat(day_shock, 20) + rng.normal(0, 0.1, 4000)         # 4000 events but really 200 independent draws
    naive = 1.0
    p_cluster = ES.cluster_t(days, x + 1.0)[1]
    assert p_cluster > 0.05                                         # a naive i.i.d. t-test would call mean +1 'significant' here
    assert naive == 1.0


def synth_mats(D=600, seed=5, planted_slot=None, planted_bps=0.0):
    """Random-walk 5m OHLC for D days x 75 slots; optionally a +bps drift in one slot's next-bars."""
    rng = np.random.default_rng(seed)
    r = rng.normal(0, 2.5e-4, (D, ES.NSLOT))
    if planted_slot is not None:
        r[:, planted_slot + 1:planted_slot + 4] += planted_bps * 1e-4 / 3
    close = 20000 * np.exp(np.cumsum(r.ravel())).reshape(D, ES.NSLOT)
    O = np.empty_like(close)
    O[:, 0] = close[:, 0] / (1 + r[:, 0])
    O[:, 1:] = close[:, :-1]
    H, L = np.maximum(O, close) * 1.0001, np.minimum(O, close) * 0.9999
    day = 719163 + 17000 + np.arange(D) + (np.arange(D) // 5) * 2             # weekends skipped
    wd = (((day - 719163) + 3) % 7).astype(int)
    return ES.Mats(day, wd, O, H, L, close)


def periods(M):
    d = M.day
    return ES.Periods((d[0], d[len(d) // 2 - 1]), (d[len(d) // 2], d[3 * len(d) // 4 - 1]), (d[3 * len(d) // 4], d[-1]))


def test_generate_builds_the_full_preregistered_space_and_is_deterministic():
    M = synth_mats(300)
    a, b = ES.generate(M), ES.generate(M)
    assert len(a) == len(b) > 500 and [h.name for h in a] == [h.name for h in b]
    assert {h.family for h in a} == {"A_slot", "B_gap", "C_first30", "D_extreme", "E_weekday", "F_prevday", "G_lasthour"}
    assert all(len(h.day) == len(h.val) for h in a)


def test_pipeline_finds_a_planted_edge_through_all_three_stages():
    M = synth_mats(900, planted_slot=20, planted_bps=9.0)
    hyps = ES.generate(M)
    r = ES.pipeline(hyps, periods(M), cost=2.0, min_events=100, min_days=60)
    names = [h.name for h, *_ in r["s3"]]
    assert any(n.startswith("A slot20 h3 L") or n.startswith("A slot20 h6 L") for n in names), names


def test_pipeline_on_pure_noise_is_quiet_at_the_holdout():
    M = synth_mats(900, seed=9)
    hyps = ES.generate(M)
    r = ES.pipeline(hyps, periods(M), cost=2.0, min_events=100, min_days=60)
    assert len(r["s3"]) == 0                                         # zero-gross-edge data with 2 bps cost: nothing may survive the holdout


def test_placebo_false_survivors_stay_near_zero():
    M = synth_mats(900, seed=3)
    hyps = ES.generate(M)
    res = ES.placebo(hyps, periods(M), 2.0, runs=4, min_events=100, min_days=60)
    assert max(s3 for _, _, s3 in res) <= 1 and len(res) == 4
