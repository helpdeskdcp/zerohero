"""app.reverse_engineering.replay -- INSUFFICIENT_SAMPLE gate + metrics math."""
from app.reverse_engineering import replay as rp


def _events(n, outcome="TARGET_ACHIEVED"):
    return [{"outcome": outcome, "entry": 100.0, "exit": 120.0, "mae": -1.0, "mfe": 25.0}
           for _ in range(n)]


def test_below_minimum_returns_insufficient_sample():
    out = rp.replay_engine(_events(10), lambda e: {"state": "CONFIRMED"})
    assert out["status"] == "INSUFFICIENT_SAMPLE"


def test_replay_compare_also_gated():
    out = rp.replay_compare(_events(5), {"baseline": lambda e: {"state": "NO_TRADE"}})
    assert out["status"] == "INSUFFICIENT_SAMPLE"


def test_metrics_computed_above_minimum():
    events = _events(20, "TARGET_ACHIEVED") + _events(15, "SL_HIT")
    out = rp.replay_engine(events, lambda e: {"state": "CONFIRMED"})
    assert out["status"] == "OK"
    assert out["true_positive"] == 20
    assert out["false_positive"] == 15
    assert out["precision"] == round(20 / 35, 4)


def test_no_trade_engine_scores_true_negative_on_losses():
    events = _events(20, "TARGET_ACHIEVED") + _events(15, "SL_HIT")
    out = rp.replay_engine(events, lambda e: {"state": "NO_TRADE"})
    assert out["true_negative"] == 15
    assert out["false_negative"] == 20
    assert out["precision"] is None   # no positives called at all


def test_unlabelled_outcome_not_scored_either_way():
    events = _events(30, outcome="UNKNOWN_REMARK")
    out = rp.replay_engine(events, lambda e: {"state": "CONFIRMED"})
    assert out["status"] == "OK"
    assert out["n_scored"] == 0


def test_compare_runs_each_engine_independently_above_minimum():
    events = _events(20, "TARGET_ACHIEVED") + _events(15, "SL_HIT")
    out = rp.replay_compare(events, {
        "baseline": lambda e: {"state": "NO_TRADE"},
        "confirmation": lambda e: {"state": "CONFIRMED"},
    })
    assert out["status"] == "OK"
    assert out["results"]["baseline"]["true_negative"] == 15
    assert out["results"]["confirmation"]["true_positive"] == 20
