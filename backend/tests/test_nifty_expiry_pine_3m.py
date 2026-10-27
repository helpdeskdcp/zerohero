"""3-minute (lower-timeframe) host tests: the 5-minute engine is read from the last COMPLETED 5-minute bar (request.security, lookahead_on, [1]) and entries can be
refined on the chart's own bars. Uses the research worker (1-minute base bars -> exact 3-minute chart and exact 5-minute security bars). PineTS engine, not TradingView."""
import math, os, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
from app.smart_chart import runner
import nifty_pine_fakes as F
import pine_nifty_audit as AU

pytestmark = pytest.mark.skipif(not runner.available()["ok"] or not os.path.exists(os.path.join(os.path.dirname(HERE), "app", "smart_chart", "pine_runner", "worker_research.mjs")), reason="PineTS research runner unavailable")
STRESS_ONLY = {"Entry trigger": "Stress only"}
LOOSE = {"Reject setup if risk > x ATR": 20, "Minimum distance to nearest opposing level (R)": 0, "No-trade if a bar's range > x ATR (abnormal volatility)": 100}
nan = float("nan")


@pytest.fixture(autouse=True)
def _runner_limits(monkeypatch):
    """the Smart Chart web limits (40 s CPU, 512 MB) are for interactive use; rule tests on real history need more. Test-process only."""
    from app.smart_chart import config
    monkeypatch.setattr(config, "NODE_CPU_SEC", 900); monkeypatch.setattr(config, "NODE_HEAP_MB", 2048)


def _days(stress_bars):
    return F.warmup(6) + [(6, stress_bars)]


def _p(res): return F.plots(res)


def test_engine_steps_match_the_5m_chart_and_never_come_from_a_partial_bar():
    days = _days(F.stress_day()); r1 = F.rows1m_from_5m(days)
    p5 = F.plots(F.run(F.rows_from_days(days), STRESS_ONLY)); t5col = {int(F.rows_from_days(days)[i]["time"]): i for i in range(len(F.rows_from_days(days)))}
    res = F.run_host(r1, "3", STRESS_ONLY); p3 = _p(res); times = res["times"]
    steps = [i for i in range(len(times)) if p3["New 5m step"][i] == 1]
    assert len(steps) >= 6 * 75                                                      # one step per completed 5-minute bar
    seen = []
    for i in steps:
        T = int(p3["Engine 5m bar time"][i] * 1000); seen.append(T)
        assert times[i] >= T + 300000                                                # the host bar opens at/after the END of the 5-minute bar it reads (never a partial bar)
        j = t5col[T]
        for k in ("Ref ATR", "Ref RSI", "Ref swing high", "Ref swing low", "Ref orH", "Ref orL", "Gap scenario code", "Scenario validated", "Gap points", "Daily ATR", "Gap / daily ATR",
                  "Engine 5m close", "Event bits", "Open bull FVG", "Open bear FVG", "Stress flags (1 bull, 2 bear)", "Candle/volume proxy dir" if False else "Ref ATR"):
            a, b = p3[k][i], p5[k][j]
            assert AU.same(a, b, 1e-9), (k, i, a, b)
    assert seen == sorted(seen) and len(set(seen)) == len(seen)


def test_refine_off_enters_on_the_host_bar_where_the_5m_signal_arrives_and_fills_next_open():
    days = _days(F.stress_day()); r1 = F.rows1m_from_5m(days)
    res = F.run_host(r1, "3", {**STRESS_ONLY, **LOOSE}); p = _p(res); times = res["times"]
    sig = [i for i in range(len(times)) if p["Signal (+1 CE / -1 PE)"][i] == -1]
    assert len(sig) == 1 and p["New 5m step"][sig[0]] == 1 and p["Entry decision (+1 CE / -1 PE)"][sig[0]] == -1
    tr = res["strategy"]["closed_trades"]; assert tr and all(t["entry_time"] == times[sig[0] + 1] for t in tr)         # fills on the NEXT chart-bar open
    T46 = F.rows_from_days(days)[6 * 75 + 46]["time"]
    assert int(round(p["Engine 5m bar time"][sig[0]] * 1000)) == T46                                                                  # the signal is the one produced by 5-minute bar 46 (rejection bar)


def _decline_day():
    """the stress day, but the market keeps falling right after the rejection bar (no flat pause)"""
    return F.pad(F.stress_day()[:47] + F.walk([23850], 6, start=23924))


def test_micro_breakout_refinement_waits_for_the_lower_timeframe_trigger():
    days = _days(_decline_day()); r1 = F.rows1m_from_5m(days)
    off = F.run_host(r1, "3", {**STRESS_ONLY, **LOOSE}); pof = _p(off); sig = [i for i in range(len(off["times"])) if pof["Signal (+1 CE / -1 PE)"][i] == -1][0]
    res = F.run_host(r1, "3", {**STRESS_ONLY, **LOOSE, "Entry refinement mode": "Micro-breakout"}); p = _p(res); times = res["times"]
    assert [i for i in range(len(times)) if p["Signal (+1 CE / -1 PE)"][i] == -1] == [sig]                      # the 5-minute signal is the same with or without refinement
    dec = [i for i in range(len(times)) if p["Entry decision (+1 CE / -1 PE)"][i] == -1]
    assert dec == [sig + 1]                                                       # armed on the signal bar, confirmed by the NEXT chart bar (never the arming bar itself)
    assert p["Refine confirmed"][-1] == 1 and p["Refine state (dir*10+phase)"][sig] == -8 and p["Refine state (dir*10+phase)"][dec[0]] == 0
    tr = res["strategy"]["closed_trades"]; assert tr and all(t["entry_time"] == times[dec[0] + 1] for t in tr) and all(t["size"] < 0 for t in tr)
    offtr = off["strategy"]["closed_trades"]; assert offtr and all(t["entry_time"] == times[sig + 1] for t in offtr)     # without refinement the fill is one bar earlier
    assert tr[0]["entry_price"] < offtr[0]["entry_price"] + 0.6                      # in a falling market the refined short fills no worse than the immediate one
    assert min(t["exit_time"] for t in tr) > times[dec[0] + 1] - 1


def test_micro_breakout_without_a_trigger_expires_and_does_not_chase():
    res = F.run_host(F.rows1m_from_5m(_days(F.stress_day())), "3", {**STRESS_ONLY, **LOOSE, "Entry refinement mode": "Micro-breakout"}); p = _p(res)
    assert not res["strategy"]["closed_trades"] and p["Refine expired+cancelled"][-1] >= 1 and p["Refine confirmed"][-1] == 0   # flat pause: no close beyond the prior bar within 4 bars


def test_pullback_mode_in_a_one_way_decline_expires_and_never_chases():
    days = _days(F.stress_day()); r1 = F.rows1m_from_5m(days)
    res = F.run_host(r1, "3", {**STRESS_ONLY, **LOOSE, "Entry refinement mode": "Pullback then resume"}); p = _p(res)
    assert not res["strategy"]["closed_trades"]                                     # no pullback bar ever came -> no entry
    assert p["Refine expired+cancelled"][-1] >= 1 and p["Refine confirmed"][-1] == 0


def test_refinement_is_ignored_on_a_5m_chart():
    days = _days(F.stress_day()); rows = F.rows_from_days(days)
    a = F.run(rows, {**STRESS_ONLY, **LOOSE}); b = F.run(rows, {**STRESS_ONLY, **LOOSE, "Entry refinement mode": "Micro-breakout"})
    key = lambda r: sorted((t["entry_time"], round(t["entry_price"], 6), t["size"], round(t["exit_price"], 6)) for t in r["strategy"]["closed_trades"])
    assert key(a) == key(b) and key(a)


def test_stop_break_while_armed_cancels_the_setup():
    b = F.stress_day()[:47]                                                          # up to and including the rejection bar (index 46)
    b += [F.bar(23924, 23990, 23923, 23985)]                                         # bar 47: closes far above the plan stop (zone high 23970 + buffer)
    days = _days(F.pad(b)); r1 = F.rows1m_from_5m(days)
    res = F.run_host(r1, "3", {**STRESS_ONLY, **LOOSE, "Entry refinement mode": "Micro-breakout"}); p = _p(res)
    assert p["Refine expired+cancelled"][-1] >= 1 or not res["strategy"]["closed_trades"]
    assert all(t["size"] < 0 for t in res["strategy"]["closed_trades"])


def test_a_chart_above_5_minutes_is_rejected_not_traded():
    days = _days(F.stress_day()); r1 = F.rows1m_from_5m(days)
    res = F.run_host(r1, "15", {**STRESS_ONLY, **LOOSE}); p = _p(res)
    assert not any(p["Signal (+1 CE / -1 PE)"][i] for i in range(len(res["times"]))) and not res["strategy"]["closed_trades"]


def test_3m_host_is_causal_truncated_history_gives_identical_past():
    days = _days(F.stress_day()); r1 = F.rows1m_from_5m(days)
    inp = {**STRESS_ONLY, **LOOSE, "Entry refinement mode": "Micro-breakout"}
    full = F.run_host(r1, "3", inp); pf = _p(full)
    cut_1m = (6 * 75 + 52) * 5                                                       # inside the stress day, after the signal
    part = F.run_host(r1[:cut_1m], "3", inp); pp = _p(part)
    n = len(part["times"]) - 2
    for k, v in pp.items():
        for i in range(n):
            assert AU.same(v[i], pf[k][i], 1e-9), (k, i, v[i], pf[k][i])
