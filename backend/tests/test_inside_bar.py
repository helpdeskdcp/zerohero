"""
Inside-Bar 2m research engine -- offline unit tests. No network.

Verifies: inside-bar detection + contraction/mother-candle guards, EMA + momentum
gates, breakout-window trigger, SL/1R geometry, the scale-out + trail trade
lifecycle, session-time limits, determinism, and that the package imports
nothing from the live app.
"""
import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.research_engines._archived.inside_bar import strategy as S       # noqa: E402
from app.research_engines._archived.inside_bar import backtest as BT      # noqa: E402
from app.research_engines._archived.inside_bar.config import merged       # noqa: E402

CFG = merged()


def _bar(t, o, h, l, c, hhmm="10:00", sd="2024-01-02"):
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "hhmm": hhmm, "session_date": sd,
            "minute_of_day": int(hhmm[:2]) * 60 + int(hhmm[3:])}


def test_inside_bar_detection():
    # mother candle: strong up move 100->110 (body 9, range ~11)
    mother = _bar(0, 100, 111, 100, 110)
    inside = _bar(120, 108, 109, 105, 106)          # contained in [100,111]
    bars = [_bar(-120, 90, 101, 90, 100), mother, inside]
    assert S.is_inside_bar(bars, 2, CFG)
    # not contained -> not an inside bar
    bars2 = [_bar(-120, 90, 101, 90, 100), mother, _bar(120, 108, 112, 105, 106)]
    assert not S.is_inside_bar(bars2, 2, CFG)
    # mother is a doji -> reject
    doji = _bar(0, 105, 111, 100, 105.2)
    assert not S.is_inside_bar([_bar(-120, 90, 101, 90, 100), doji, inside], 2, CFG)


def test_ema_and_momentum():
    e = S.ema_series([10] * 5 + [20] * 20, 20)
    assert e[0] == 10 and e[4] < e[-1] <= 20
    up = [_bar(i * 120, 100 + i, 100 + i + 1, 99 + i, 100 + i) for i in range(10)]
    # atr ~1; move over 5 bars = 5 >> 1.2*atr -> momentum yes for LONG
    assert S.had_momentum(up, 9, CFG, "LONG", atr=1.0)
    assert not S.had_momentum(up, 9, CFG, "SHORT", atr=1.0)


def _session_with_setup(side="LONG"):
    """Build a synthetic 2m session: warmup + push + inside bar + breakout."""
    bars = []
    px = 100.0
    for i in range(CFG["warmup_bars"] + 2):                 # flat warmup
        bars.append(_bar(i * 120, px, px + 0.4, px - 0.4, px, hhmm="09:%02d" % (16 + i % 40)))
    base_t = len(bars) * 120
    # momentum push up over 6 bars
    for i in range(6):
        px += 3.0
        bars.append(_bar(base_t + i * 120, px - 2.6, px + 0.5, px - 2.9, px, hhmm="10:%02d" % (i * 2)))
    mother_lo, mother_hi = px - 3.0, px + 1.0
    bars.append(_bar(base_t + 6 * 120, px - 2.5, mother_hi, mother_lo, px + 0.2, hhmm="10:14"))   # mother
    ib_hi, ib_lo = px, px - 1.5
    bars.append(_bar(base_t + 7 * 120, px - 0.5, ib_hi, ib_lo, px - 0.5, hhmm="10:16"))            # inside bar
    # breakout up on next bar
    bars.append(_bar(base_t + 8 * 120, px, ib_hi + 3.0, px - 0.2, ib_hi + 2.5, hhmm="10:18"))
    # then drift up to +4R then stall
    for i in range(20):
        p = ib_hi + 3.0 + i * 0.8
        bars.append(_bar(base_t + (9 + i) * 120, p - 0.3, p + 0.5, p - 0.4, p, hhmm="10:%02d" % (20 + i * 2)))
    return bars


def test_find_setups_and_trigger():
    bars = _session_with_setup()
    setups = S.find_setups(bars, CFG)
    assert setups, "expected one triggered long setup"
    su = setups[0]
    assert su["side"] == "LONG"
    assert su["entry"] > su["ib_high"] >= su["stop"]        # long: entry above IB high, stop below
    assert su["r_points"] > 0
    assert su["entry_index"] > su["ib_index"]


def test_trade_lifecycle_scale_and_trail():
    bars = _session_with_setup()
    su = S.find_setups(bars, CFG)[0]
    tr = BT.simulate_trade(bars, su, CFG)
    # this synthetic run drifts to +4R -> should be a solid win, capped
    assert tr["win"] and tr["r_multiple"] > 1.0
    assert tr["exit_reason"] in ("TARGET_4R", "TRAIL", "TIME_1PM", "EOD")
    assert tr["mfe"] >= 1.0


def test_session_limits_respected():
    # a setup that triggers after 12:30 must be dropped
    bars = _session_with_setup()
    for b in bars:
        if int(b["hhmm"][:2]) == 10:
            b["hhmm"] = b["hhmm"].replace("10:", "12:")
            b["minute_of_day"] = 12 * 60 + int(b["hhmm"][3:])
    # push everything past 12:30
    for b in bars:
        if b["hhmm"].startswith("12:"):
            mm = int(b["hhmm"][3:]) + 35
            b["hhmm"] = "12:%02d" % mm if mm < 60 else "13:%02d" % (mm - 60)
    assert S.find_setups(bars, CFG) == []


def test_run_symbol_insufficient_is_flagged():
    r = BT.run_symbol("SENSEX", "2016-01-01", "2016-02-01", CFG)
    assert r["status"] in ("INSUFFICIENT_SAMPLE", "NO_TRADES", "NO_DATA", "OK")
    if r["status"] == "INSUFFICIENT_SAMPLE":
        assert r["n_sessions"] < 40


def test_deterministic():
    bars = _session_with_setup()
    a = S.find_setups(bars, CFG)
    b = S.find_setups(bars, CFG)
    assert a == b
    assert BT.simulate_trade(bars, a[0], CFG) == BT.simulate_trade(bars, b[0], CFG)


def test_close_confirm_is_causal():
    """A 'close' breakout confirm can only be known after the trigger bar closes,
    so the fill + simulation must start on the NEXT bar (no mid-bar time travel)."""
    bars = _session_with_setup()
    cfg_touch = merged({"breakout_confirm": "touch"})
    cfg_close = merged({"breakout_confirm": "close"})
    s_t = S.find_setups(bars, cfg_touch)[0]
    s_c = S.find_setups(bars, cfg_close)[0]
    assert s_c["entry_index"] > s_t["entry_index"]        # close waits one more bar
    # close-confirm fills at a bar close, never at the raw breakout level
    assert s_c["entry"] == bars[s_c["entry_index"] - 1]["c"]
    # entry bar's own high/low is fair game for the sim; the trigger bar is not
    assert s_c["entry_index"] >= s_c["ib_index"] + 2


def test_mother_bar_refs_widen_risk():
    bars = _session_with_setup()
    ib = S.find_setups(bars, merged({"stop_ref": "ib"}))[0]
    mo = S.find_setups(bars, merged({"stop_ref": "mother"}))[0]
    assert mo["r_points"] >= ib["r_points"]               # mother bar is wider than the inside bar


def test_no_live_app_imports():
    pkg = Path(__file__).parents[1] / "app" / "research_engines" / "_archived" / "inside_bar"
    banned = ("app.autoscalp", "app.execution", "app.engines", "app.hcs")
    for py in pkg.glob("*.py"):
        tree = ast.parse(py.read_text())
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module:
                mods.add(n.module)
            elif isinstance(n, ast.Import):
                mods.update(a.name for a in n.names)
        hits = [m for m in mods if any(m == b or m.startswith(b + ".") for b in banned)]
        assert not hits, f"{py.name}: {hits}"
