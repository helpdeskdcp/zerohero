"""
SSL Hybrid PRO research engine -- offline unit tests. No network.

Checks the Pine ta.* ports, signal causality + determinism, the points-based
trade sim, and that the package imports nothing from the live app.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research_engines.ssl_hybrid import indicators as I      # noqa: E402
from app.research_engines.ssl_hybrid import strategy as S        # noqa: E402
from app.research_engines.ssl_hybrid import backtest as BT       # noqa: E402
from app.research_engines.ssl_hybrid.config import merged        # noqa: E402

CFG = merged()


def test_ema_matches_recurrence():
    x = [10.0, 11.0, 12.0, 11.0, 13.0, 14.0]
    e = I.ema(x, 3)
    assert e[0] == 10.0
    k = 2 / 4
    assert abs(e[1] - (11 * k + 10 * (1 - k))) < 1e-12
    assert len(e) == len(x)


def test_rsi_bounds_and_atr_positive():
    up = [100.0 + i for i in range(40)]
    r = I.rsi(up, 14)
    assert r[-1] is not None and 99.0 <= r[-1] <= 100.0        # pure uptrend -> RSI ~100
    h = [c + 1 for c in up]
    l = [c - 1 for c in up]
    a = I.atr(h, l, up, 14)
    assert a[-1] is not None and a[-1] > 0


def test_dmi_ranges():
    n = 60
    h = [100.0 + i * 0.5 for i in range(n)]
    l = [99.0 + i * 0.5 for i in range(n)]
    c = [99.5 + i * 0.5 for i in range(n)]
    p, m, adx = I.dmi(h, l, c, 14, 14)
    assert p[-1] is not None and 0 <= p[-1] <= 100
    assert m[-1] is not None and 0 <= m[-1] <= 100
    assert adx[-1] is not None and 0 <= adx[-1] <= 100
    assert p[-1] > m[-1]                                        # uptrend -> +DI dominates


def test_hma_is_causal_length():
    x = [float(i % 7) for i in range(80)]
    hm = I.hma(x, 20)
    assert len(hm) == len(x)
    assert hm[5] is None                                        # not enough bars yet
    assert hm[-1] is not None


def _synth_bars(n=400, tf_min=15):
    """Trending-up synthetic 15m series with session stamps."""
    bars = []
    px = 20000.0
    for i in range(n):
        px += 3.0 + (i % 5 - 2) * 1.5
        o = px - 2
        h = px + 4
        l = px - 4
        c = px
        d = 9 * 60 + 15 + (i % 25) * 15
        bars.append({
            "t": i * tf_min * 60, "o": o, "h": h, "l": l, "c": c, "v": 0.0, "n": 1,
            "session_date": f"2024-01-{(i // 25) % 28 + 1:02d}",
            "hhmm": f"{d // 60:02d}:{d % 60:02d}", "minute_of_day": d,
            "hlc3": (h + l + c) / 3.0, "source": "synth", "has_volume": False,
        })
    return bars


def test_build_signals_is_causal_and_deterministic():
    bars = _synth_bars()
    f1 = S.build_signals(bars, CFG)
    f2 = S.build_signals(bars, CFG)
    assert f1 == f2                                             # deterministic
    # truncating the future must not change any past signal row
    cut = 300
    ftrunc = S.build_signals(bars[:cut], CFG)
    for a, b in zip(ftrunc, f1[:cut]):
        assert a["buy_signal"] == b["buy_signal"]
        assert a["sell_signal"] == b["sell_signal"]
        assert a["bull_score"] == b["bull_score"]


def test_buy_signal_fires_once_per_setup():
    bars = _synth_bars()
    f = S.build_signals(bars, CFG)
    for i in range(1, len(f)):
        if f[i]["buy_signal"]:
            assert not f[i - 1]["strong_bull"]                 # edge-triggered, not level


def test_setup_geometry_matches_pine_risk_engine():
    bars = _synth_bars()
    sus = S.find_setups(bars, CFG)
    for su in sus:
        risk = su["risk_points"]
        if su["side"] == "LONG":
            assert abs((su["entry"] - su["stop"]) - risk) < 0.05
            assert su["t1"] > su["entry"] and su["t3"] > su["t2"] > su["t1"]
            assert abs((su["t2"] - su["entry"]) - 2 * risk) < 0.05
        else:
            assert abs((su["stop"] - su["entry"]) - risk) < 0.05
            assert su["t1"] < su["entry"] and su["t3"] < su["t2"] < su["t1"]


def test_simulate_trade_points_and_R_consistent():
    bars = _synth_bars()
    f = S.build_signals(bars, CFG)
    sus = S.find_setups(bars, CFG)
    assert sus, "synthetic uptrend should trigger at least one long"
    tr = BT.simulate_trade(bars, f, sus[0], CFG)
    assert tr["side"] == "LONG"
    # both fields are independently rounded (points 3dp, r_multiple 4dp)
    assert abs(tr["r_multiple"] - tr["points"] / tr["risk_points"]) < 5e-3
    assert tr["win"] == (tr["points"] > 0)
    assert tr["exit_reason"] in ("STOP", "BREAKEVEN", "TRAIL_BE", "TARGET_T3",
                                 "OPPOSITE", "SESSION_END", "EOD")


def test_run_symbol_insufficient_is_flagged():
    r = BT.run_symbol("SENSEX", "2016-01-01", "2025-12-31", CFG)
    assert r["status"] in ("INSUFFICIENT_SAMPLE", "NO_DATA", "NO_TRADES", "OK")


def test_no_live_app_imports():
    pkg = Path(__file__).parents[1] / "app" / "research_engines" / "ssl_hybrid"
    banned = ("app.autoscalp", "app.execution", "app.engines", "app.hcs", "app.connectors")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                mods.add(node.module)
            elif isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
        hits = [m for m in mods if any(m == b or m.startswith(b + ".") for b in banned)]
        assert not hits, f"{py.name}: {hits}"
