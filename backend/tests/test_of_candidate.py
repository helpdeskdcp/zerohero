import os

import pytest

from app.of_candidate import run as R

T0 = 1_792_122_900


def bar(i, o, h, l, c, **kw):
    return {"symbol": "NIFTY", "kind": "FUTURE", "tf_sec": 300, "t": T0 + i * 300, "o": o, "h": h, "l": l, "c": c, **kw}


def sig(i, side=1, stop=99.0, tgt=102.0): return {"idx": i, "side": side, "stop": stop, "t2": tgt, "kind": "ABSORPTION"}


def test_target_stop_first_gap_and_costs():
    bars = [bar(0, 100, 100.2, 99.9, 100), bar(1, 100, 100.5, 99.8, 100.3), bar(2, 100.3, 102.5, 100.1, 102.2)]
    r = R.simulate(bars, sig(0)); assert r["exit_reason"] == "TARGET" and r["exit"] == 102.0 and r["gross_bps"] == pytest.approx(200.0) and r["net_bps"] == pytest.approx(200.0 - R.COST_BPS)
    both = [bar(0, 100, 100.2, 99.9, 100), bar(1, 100, 102.5, 98.5, 100)]                                            # same bar touches stop and target -> stop first
    assert R.simulate(both, sig(0))["exit_reason"] == "STOP"
    gap = [bar(0, 100, 100.2, 99.9, 100), bar(1, 98.0, 98.5, 97.5, 98.2)]                                            # opens through the stop -> fills at the open
    r = R.simulate(gap, sig(0)); assert r["exit_reason"] == "STOP" and r["exit"] == 98.0
    s = R.simulate(bars, sig(0, side=-1, stop=101.0, tgt=98.0)); assert s["exit_reason"] == "STOP" and s["side"] == -1 and s["gross_bps"] < 0


def test_time_exit_session_end_missing_bar_and_gap_exit():
    flat = [bar(i, 100, 100.3, 99.8, 100.1) for i in range(20)]
    r = R.simulate(flat, sig(0)); assert r["exit_reason"] == "TIME" and r["hold_bars"] == R.MAX_HOLD_BARS
    short = flat[:5]; assert R.simulate(short, sig(0))["exit_reason"] == "SESSION_END"
    assert R.simulate(flat[:1], sig(0))["status"] == "NO_NEXT_BAR"
    gap = [flat[0], flat[1], dict(flat[2], t=flat[2]["t"] + 3000)]                                                  # a missing stretch of bars: never carried across
    assert R.simulate(gap, sig(0))["exit_reason"] == "GAP_EXIT"


def test_verdict_logic_accumulating_pass_and_fail():
    def trades(n, day_n, net):
        return [{"day": f"2026-10-{1 + i % day_n:02d}" if day_n <= 28 else f"2026-{10 + i % day_n // 28:02d}-{1 + i % day_n % 28:02d}", "net_bps": net[i % len(net)], "gross_bps": net[i % len(net)] + 6.3, "exit_reason": "TIME", "hold_bars": 5} for i in range(n)]
    assert R.evaluate(trades(10, 5, [20, -5]))["verdict"] == "ACCUMULATING" and R.evaluate([])["verdict"] == "ACCUMULATING"
    assert R.evaluate(trades(80, 25, [20, 18, 22, 19, -2]))["verdict"] == "PASS_FOR_NEXT_STAGE"
    assert R.evaluate(trades(80, 25, [-20, 5, -15, -9]))["verdict"] == "FAIL"
    assert R.evaluate(trades(80, 10, [20, 18]))["verdict"] == "ACCUMULATING"                                          # enough trades, too few days


def test_run_day_end_to_end_on_a_temp_tick_db(tmp_path):
    from app.engines.orderflow_store import TickStore
    from app.pa_of_lab import ticks as tk
    import random
    day = "2026-10-16"; s0 = tk.day_bounds(day)[0] + 9 * 3600 + 15 * 60; rnd = random.Random(2); px = 25000.0; rows = []
    for k in range(4000):
        px = round(px + rnd.choice([-0.1, 0.0, 0.1]), 2); ltt = s0 + k * 6
        rows.append({"received_ts": __import__("datetime").datetime.fromtimestamp(ltt + 1, tz=__import__("datetime").timezone.utc).isoformat(), "security_id": 1, "symbol": "NIFTY", "kind": "FUTURE", "ltp": px, "ltq": 10, "ltt": ltt,
                     "side": rnd.choice(["BUY_AGGRESSOR", "SELL_AGGRESSOR"]), "classification_method": "QUOTE_RULE", "best_bid": px - 0.05, "best_ask": px + 0.05, "cvd": 0})
    p = str(tmp_path / "t.db"); TickStore(p).insert_many(rows)
    assert R.tick_days("NIFTY", p) == [day]
    r = R.run_day("NIFTY", day, p); assert r["bars"] > 20 and r["ok_bars"] > 0 and r["signals"] >= len(r["trades"])
    out = R.run_all(p, symbols=("NIFTY",)); assert out["summary"]["symbol_days_with_ticks"] == 1 and out["summary"]["verdict"] == "ACCUMULATING"


def test_one_position_at_a_time_and_module_is_read_only_no_orders():
    s = open(R.__file__).read()
    for bad in ("requests", "urllib", "socket", "app.execution", "place_order", "subprocess", "DELETE", "INSERT"): assert bad not in s, bad
    assert "connect_ro" in s
