import numpy as np
import pandas as pd
import pytest

from app.research_engines.hybrid_sr_mtf import data as D, engine as E, sim as S


def bars(rows):
    """rows = [(open, high, low, close), ...] one day, minutes from 09:15"""
    o, h, l, c = (np.array(x, float) for x in zip(*rows)); n = len(rows); mins = 9 * 60 + 15 + np.arange(n); return o, h, l, c, mins, np.zeros(n, int)


def test_long_stop_first_when_one_bar_touches_stop_and_target_and_it_is_flagged():
    o, h, l, c, m, d = bars([(100, 100, 100, 100), (100, 112, 94, 100)])
    r = S.simulate(o, h, l, c, m, d, 0, 1, 100.0, 95.0, 105.0, 110.0, 115.0)
    assert r["reason"] == "SL" and r["ambiguous"] and r["pts"] == pytest.approx(-5.0) and r["R"] == pytest.approx(-1.0)


def test_targets_scale_out_40_30_30_and_the_stop_moves_up_only_from_the_next_bar():
    o, h, l, c, m, d = bars([(100, 101, 99.5, 100), (100, 105.5, 99.8, 105), (105, 106, 100.5, 101), (101, 101, 99.9, 100)])
    r = S.simulate(o, h, l, c, m, d, 0, 1, 100.0, 95.0, 105.0, 110.0, 115.0)
    # T1 (40 %) filled in bar 1 at 105 (+2.0 points); in bar 2 the stop is now at entry 100: low 100.5 does not touch it; bar 3 low 99.9 <= 100 stops the remaining 60 % at 100 -> 0
    assert r["hit_t1"] and not r["hit_t2"] and r["reason"] == "SL_AFTER_TARGET" and r["pts"] == pytest.approx(0.4 * 5.0)


def test_short_gap_through_the_stop_fills_at_the_open_not_the_stop():
    o, h, l, c, m, d = bars([(100, 100.2, 99.9, 100), (103, 104, 102.5, 103.5)])
    r = S.simulate(o, h, l, c, m, d, 0, -1, 100.0, 101.0, 98.0, 96.0, 94.0)
    assert r["reason"] == "SL" and r["pts"] == pytest.approx(-3.0)


def test_time_exit_uses_the_open_of_the_first_bar_at_or_after_the_exit_minute():
    n = 8; o = np.full(n, 100.0); h = o + 0.1; l = o - 0.1; c = o.copy(); m = np.array([15 * 60 + 10 + i for i in range(n)]); o[5] = 100.7
    r = S.simulate(o, h, l, c, m, np.zeros(n, int), 0, 1, 100.0, 90.0, 120.0, 130.0, 140.0, exit_min=15 * 60 + 15)
    assert r["reason"] == "TIME" and r["pts"] == pytest.approx(0.7)


def _synthetic(days=14, seed=2):
    rng = np.random.default_rng(seed); rows = []; px = 20000.0
    for d in pd.bdate_range("2026-01-05", periods=days, tz="Asia/Kolkata"):
        ts = pd.date_range(d + pd.Timedelta(hours=9, minutes=15), periods=375, freq="1min"); r = rng.normal(0, 4.0, 375).cumsum() * 0.6 + np.sin(np.arange(375) / 40) * 25
        c = px + r; o = np.r_[px, c[:-1]]; h = np.maximum(o, c) + np.abs(rng.normal(0, 1.5, 375)); l = np.minimum(o, c) - np.abs(rng.normal(0, 1.5, 375)); px = c[-1]
        rows.append(pd.DataFrame({"open": o, "high": h, "low": l, "close": c}, index=ts))
    return pd.concat(rows)


@pytest.mark.parametrize("cand", E.CANDIDATES)
def test_decisions_before_day_D_do_not_change_when_the_future_is_removed(cand):
    full = _synthetic(); days = sorted(set(full.index.date)); cut = days[9]
    a = D.prepare("X", full); b = D.prepare("X", full[full.index.date < cut])
    p = E.Params(max_trades_day=9, max_consec_loss=9, win_from=9 * 60 + 25)
    ta, _, _ = E.scan(a, cand, p); tb, _, _ = E.scan(b, cand, p)
    cut_key = int(pd.Timestamp(cut).strftime("%Y%m%d")); ka = [(t["t_entry"], t["side"], round(t["entry"], 4), round(t["R"], 6)) for t in ta if t["day"] < cut_key]
    kb = [(t["t_entry"], t["side"], round(t["entry"], 4), round(t["R"], 6)) for t in tb if t["day"] < cut_key]
    assert ka == kb


def test_entry_is_always_the_open_of_the_bar_after_the_trigger_and_never_before_the_setup_completed():
    full = _synthetic(20, seed=5); p = D.prepare("X", full); tr, rej, _ = E.scan(p, "S2_BREAK_RETEST", E.Params(win_from=9 * 60 + 25, min_rr=0.5))
    assert tr, "synthetic series should produce some trades"
    for t in tr:
        assert t["entry"] == pytest.approx(p.o[t["entry_i"]]) and t["t_entry"] > t["t_setup"] - 1 and t["entry_i"] > 3


def test_the_no_trade_filters_actually_reject():
    full = _synthetic(20, seed=7); p = D.prepare("X", full)
    _, rej, st = E.scan(p, "S2_BREAK_RETEST", E.Params(win_from=9 * 60 + 25))
    assert sum(rej.values()) > 0 and set(rej) <= {"TIME_WINDOW", "INCOMPLETE_BAR", "DAY_TRADE_LIMIT", "DAY_LOSS_LIMIT", "HTF_CONTRADICTION", "CHOP_RANGE", "IN_POSITION", "COOLDOWN", "STALE_OR_GAPPED_DATA", "ENTRY_BEYOND_STOP", "RISK_TOO_WIDE", "RISK_TOO_NARROW", "NO_ROOM_TO_OPPOSING_LEVEL", "NO_1M_TRIGGER"}


def test_a_trade_keeps_its_target_prices_and_the_hit_flags_separately_and_the_mirror_is_a_true_mirror():
    """regression: the simulator result used to overwrite the stored target PRICES t1/t2/t3 with booleans (the mirror control then used True/False as prices)."""
    full = _synthetic(20, seed=5); p = D.prepare("X", full); tr, _, _ = E.scan(p, "S2_BREAK_RETEST", E.Params(win_from=9 * 60 + 25, min_rr=0.5))
    assert tr
    for t in tr:
        assert t["t1"] > 1.0 and (t["t1"] - t["entry"]) * t["side"] > 0 and (t["t3"] - t["entry"]) * t["side"] >= (t["t1"] - t["entry"]) * t["side"] and isinstance(t["hit_t1"], bool)
        e = t["entry"]; m = S.simulate(p.o, p.h, p.l, p.c, p.t1 % 1440, p.day, t["entry_i"], -t["side"], e, 2 * e - t["sl"], 2 * e - t["t1"], 2 * e - t["t2"], 2 * e - t["t3"])
        assert abs(m["risk"] - t["risk"]) < 1e-9 and m["R"] > -1.0001 and abs(m["R"]) < 50
