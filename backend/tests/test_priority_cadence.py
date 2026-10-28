"""The priority capture keeps a fixed cadence (steps START every interval) instead of interval-after-each-step, and reports its own step / cadence timings."""
import time

from app.histcap import priority as P


def test_sleep_is_the_remainder_of_the_interval_with_a_floor():
    assert P.next_sleep(0.0, 3.0) == 3.0 and P.next_sleep(1.5, 3.0) == 1.5 and P.next_sleep(2.9, 3.0) == 0.25
    assert P.next_sleep(10.0, 3.0) == 0.25 and P.next_sleep(2.0, 3.0, floor=0.5) == 1.0                  # a slow step is followed by a short pause, never a further full interval


def test_cadence_with_a_slow_fetch_is_the_interval_not_interval_plus_fetch():
    """Simulated clock (no real sleeping: deterministic on a loaded machine)."""
    interval, fetch = 3.0, 1.8; t = 0.0; starts = []
    for _ in range(6):
        starts.append(t); t += fetch; t += P.next_sleep(fetch, interval)
    assert [round(b - a, 6) for a, b in zip(starts, starts[1:])] == [interval] * 5                      # old behaviour: interval + fetch = 4.8 s per cycle
    t = 0.0; starts = []
    for step in (1.0, 12.0, 1.0, 1.0):                                                                   # one slow step: a short pause, then back on the grid
        starts.append(t); t += step; t += P.next_sleep(step, interval)
    assert [round(b - a, 2) for a, b in zip(starts, starts[1:])] == [3.0, 12.25, 3.0]


def test_status_reports_step_and_cadence_percentiles(tmp_path):
    c = P.PriorityCapture(store=object(), sdk_provider=lambda: None, interval=3, path=str(tmp_path / "w.json"))
    c._step_s.extend([1.0, 2.0, 3.0, 9.0]); c._starts.extend([0.0, 3.0, 6.0, 9.5])
    st = c.status(); assert st["step_s"]["max"] == 9.0 and st["step_s"]["n"] == 4 and st["cadence_s"]["n"] == 3 and st["cadence_s"]["max"] == 3.5
