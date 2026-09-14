"""
app/liquidity_sweep/backtest.py -- split/accuracy/dedup logic and the
_walk_raw_signals causal walk itself. Uses synthetic bars, never the real
Kaggle DB (kept out of the test suite so tests don't depend on an external
data file that may be absent/changed) -- the real-data run is a manual,
reported backtest, not a unit test fixture.
"""
import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.liquidity_sweep.backtest import (  # noqa: E402
    Stage1Sample, _accuracy, _split_chronological, _walk_raw_signals,
)


def _sample(i, outcome="WIN"):
    return Stage1Sample(index=i, timestamp=f"t{i}", direction="BULLISH", setup_score=60.0,
                        regime="BULLISH", signal_type="LOWER_SWEEP", outcome=outcome)


def test_split_chronological_never_shuffles_and_respects_fractions():
    samples = [_sample(i) for i in range(100)]
    train, val, oos = _split_chronological(samples, train_frac=0.5, val_frac=0.25)
    assert len(train) == 50 and len(val) == 25 and len(oos) == 25
    assert [s.index for s in train] == list(range(0, 50))
    assert [s.index for s in val] == list(range(50, 75))
    assert [s.index for s in oos] == list(range(75, 100))


def test_accuracy_excludes_timeouts_from_the_graded_denominator():
    samples = [_sample(0, "WIN"), _sample(1, "WIN"), _sample(2, "LOSS"), _sample(3, "TIMEOUT")]
    out = _accuracy(samples)
    assert out["n"] == 4
    assert out["n_graded"] == 3
    assert out["directional_accuracy"] == round(2 / 3, 4)


def test_accuracy_reports_none_when_everything_timed_out():
    out = _accuracy([_sample(0, "TIMEOUT"), _sample(1, "TIMEOUT")])
    assert out["directional_accuracy"] is None


def _bar(i, o, h, l, c, v=1000):
    base = datetime.datetime(2026, 1, 1, 9, 15)
    t = (base + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}


def _bullish_event_bars(offset):
    """The same hand-verified pattern used in test_liquidity_sweep_engine.py,
    shifted to start at `offset`."""
    bars = [_bar(offset + i, 100.0, 100.4, 99.7, 100.1, 1500) for i in range(10)]
    pattern = [100, 101, 99.6, 100.5, 102.8, 101.5, 99.55, 100.2, 102.9, 101.0,
              100.3, 99.7, 101.8, 100.9, 99.6, 100.4]
    off = offset + len(bars)
    bars += [_bar(off + i, p, p + 0.6, p - 0.6, p + 0.1, 2000) for i, p in enumerate(pattern)]
    off = offset + len(bars)
    bars += [_bar(off + i, 100.3, 100.9, 99.8, 100.3, 2000) for i in range(6)]
    n0 = offset + len(bars)
    bars.append(_bar(n0, 100.2, 100.5, 99.8, 100.0, 2000))
    bars.append(_bar(n0 + 1, 100.0, 100.3, 97.0, 97.5, 4000))
    bars.append(_bar(n0 + 2, 97.5, 108.0, 97.3, 107.5, 9000))
    bars.append(_bar(n0 + 3, 107.5, 120.0, 107.0, 118.0, 3000))   # continues up -> a real WIN outcome
    return bars


def test_walk_raw_signals_finds_a_real_hand_verified_signal():
    bars = _bullish_event_bars(0)
    bars += [_bar(len(bars) + i, 100.0, 100.3, 99.8, 100.1) for i in range(30)]   # padding for window/horizon
    samples = _walk_raw_signals(bars, window=32, horizon=3)
    assert len(samples) >= 1
    assert samples[0].direction == "BULLISH"


def test_walk_raw_signals_deduplicates_a_single_event_via_cooldown():
    """Without the cooldown fix, the same underlying sweep re-triggers on
    consecutive bars (the real bug this backtest module's own pilot run
    caught) -- this test locks in that it stays fixed."""
    bars = _bullish_event_bars(0)
    bars += [_bar(len(bars) + i, 107.0, 108.0, 106.5, 107.5) for i in range(30)]
    samples_no_cooldown = _walk_raw_signals(bars, window=32, horizon=3, cooldown_bars=1)
    samples_cooldown = _walk_raw_signals(bars, window=32, horizon=3, cooldown_bars=10)
    assert len(samples_cooldown) <= len(samples_no_cooldown)


def test_walk_raw_signals_never_produces_a_timestamp_from_beyond_its_own_window():
    bars = _bullish_event_bars(0)
    bars += [_bar(len(bars) + i, 100.0, 100.3, 99.8, 100.1) for i in range(30)]
    samples = _walk_raw_signals(bars, window=32, horizon=3)
    for s in samples:
        assert s.index < len(bars)
