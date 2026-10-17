import random

from app.hybrid_orderflow.bar_eval import BAR, IST, InstrumentEvaluator
from app.hybrid_orderflow.policy import load_policy

P = load_policy()
T0 = 1_792_300_000 - (1_792_300_000 + IST) % 86400 + 4 * 3600        # 04:00 UTC = 09:30 IST on some IST day


def ticks(n_days=2, bars_per_day=60, seed=3, gap_every=0):
    rng = random.Random(seed)
    out = []
    px = 100.0
    for d in range(n_days):
        base = T0 + d * 86400
        for b in range(bars_per_day):
            if gap_every and b % gap_every == gap_every - 1:
                continue                                                 # a bucket with no ticks at all
            for j in range(12):
                px = round(max(1.0, px + rng.choice([-0.1, 0, 0.1, 0.1, -0.2, 0.2])), 1)
                side = rng.choice(["BUY_AGGRESSOR", "SELL_AGGRESSOR", "BUY_AGGRESSOR", "UNKNOWN"])
                out.append((base + b * BAR + j * 20 + 1, px, rng.randint(5, 60), side, "QUOTE_RULE"))
    return out


def run(tk, now=1e12):
    ev = InstrumentEvaluator("NATURALGAS", 0.1, P)
    res = []
    for t in tk:
        res += ev.add_tick(*t)
    res += ev.flush(now)
    return ev, res


def test_warmup_bars_are_not_scored_and_later_bars_are():
    _, res = run(ticks(1, 60))
    scored = [r for r in res if r["scored"]]
    assert len(res) == 60 and not any(r["scored"] for r in res[: P["data"]["min_session_bars_for_features"] - 1]) and scored


def test_no_look_ahead_prefix_results_are_identical():
    tk = ticks(2, 60)
    _, full = run(tk)
    cut = len(tk) // 2 + 7
    _, pre = run(tk[:cut], now=0)                                        # no flush: only bars closed by a later tick
    n = len(pre)
    strip = lambda r: {k: v for k, v in r.items() if k != "groups"}
    assert n > 10 and [strip(r) for r in pre] == [strip(r) for r in full[:n]]


def test_deterministic():
    tk = ticks(2, 60)
    assert run(tk)[1] == run(tk)[1]


def test_late_tick_never_reopens_a_closed_bar():
    tk = ticks(1, 30)
    ev = InstrumentEvaluator("NATURALGAS", 0.1, P)
    for t in tk:
        ev.add_tick(*t)
    before = ev.bars_seen
    assert ev.add_tick(tk[0][0] - 3000, 100.0, 10, "BUY_AGGRESSOR", "QUOTE_RULE") == [] and ev.late_ticks == 1 and ev.bars_seen == before


def test_missing_buckets_are_not_forward_filled():
    ev, res = run(ticks(1, 60, gap_every=7))
    assert len(res) == 60 - 60 // 7 and len({r["k"] for r in res}) == len(res)
    ks = sorted(r["k"] for r in res)
    assert any(b - a > 1 for a, b in zip(ks, ks[1:]))                    # holes stay holes


def test_flush_closes_only_after_the_bucket_has_ended():
    ev = InstrumentEvaluator("NATURALGAS", 0.1, P)
    t = T0 + 5
    ev.add_tick(t, 100.0, 10, "BUY_AGGRESSOR", "QUOTE_RULE")
    end = ((t + IST) // BAR + 1) * BAR - IST
    assert ev.flush(end - 1) == [] and len(ev.flush(end + 3)) == 1 and ev.flush(end + 10) == []


def test_new_ist_day_resets_session_state_and_sets_prior_day_levels():
    ev, res = run(ticks(2, 60))
    assert ev.prior_hl is not None and ev.prior_hl[0] >= ev.prior_hl[1]
    assert ev.day is not None and len(ev.hist) <= 60


def test_non_futures_symbol_or_stale_context_cannot_signal():
    ev = InstrumentEvaluator("RELIANCE", 0.1, P)
    res = []
    for t in ticks(1, 60):
        res += ev.add_tick(*t)
    assert all(r["status"] in (None, "NO_TRADE") for r in res)
