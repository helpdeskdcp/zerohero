from app.pa_of_lab.price_action import rejection, trend, signal_at
from app.pa_of_lab.orderflow import absorption_at, exhaustion_at


def B(o, h, l, c, **k): return dict(o=o, h=h, l=l, c=c, **k)


def test_rejection_hammer_and_shooting_star():
    assert rejection(B(100, 100.2, 98, 100.1), None) == 1
    assert rejection(B(100, 102, 99.9, 99.95), None) == -1
    assert rejection(B(100, 100.5, 99.5, 100.05), None) == 0


def test_trend_up_down_flat():
    up = [B(10 + j * .0, 10 + (j % 6) + j * .5, 8 + (j % 6) + j * .5 - 1, 9 + j * .5) for j in range(60)]
    assert trend(up, 59) in (0, 1)
    assert trend([B(1, 1, 1, 1)] * 30, 29) == 0


def test_price_action_is_causal():
    import random
    r = random.Random(1); p = 100; bars = []
    for j in range(300):
        o = p; p += r.uniform(-1, 1.2); bars.append(B(o, max(o, p) + r.random(), min(o, p) - r.random(), p, day=str(j // 75)))
    for i in range(20, 280, 7):
        a = signal_at(bars, i); b = signal_at(bars[:i + 1], i)
        assert (a is None) == (b is None) and (a is None or (a.side, a.entry, a.stop) == (b.side, b.entry, b.stop))


def _of(n=30):
    return [B(100, 100.5, 99.5, 100, buy=100, sell=100) for _ in range(n)]


def test_absorption_long():
    bars = _of(); bars.append(B(100, 100.4, 99.6, 100, buy=40, sell=300))  # heavy selling, no move
    bars.append(B(100, 101.5, 99.9, 101.4, buy=250, sell=100))
    s = absorption_at(bars, len(bars) - 1)
    assert s and s.side == 1 and s.stop == 99.6 and s.target > s.entry


def test_absorption_needs_volume_and_confirmation():
    bars = _of(); bars.append(B(100, 100.4, 99.6, 100, buy=110, sell=100)); bars.append(B(100, 101, 99.9, 100.9, buy=250, sell=100))
    assert absorption_at(bars, len(bars) - 1) is None


def test_exhaustion_short():
    bars = [B(100 + j * .1, 100.6 + j * .1, 99.9 + j * .1, 100.4 + j * .1, buy=150, sell=100) for j in range(20)]
    bars.append(B(102.4, 103.2, 102.0, 102.1, buy=20, sell=120))
    s = exhaustion_at(bars, len(bars) - 1)
    assert s and s.side == -1
