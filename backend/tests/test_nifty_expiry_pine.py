"""Rule-level tests of data/research/nifty_expiry_pine/nifty_expiry_intelligence.pine, executed in the PineTS engine (app/smart_chart/pine_runner).
These prove the script's logic against hand-derived expectations and an independent Python reference. They do NOT prove TradingView compilation."""
import math, os, sqlite3, sys
import pytest

HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
from app.smart_chart import runner
import nifty_pine_fakes as F
import pine_nifty_audit as AU

pytestmark = pytest.mark.skipif(not runner.available()["ok"], reason="PineTS runner (node + pinets) unavailable")
nan = float("nan")


def at(p, key, i): return p[key][i]


# ---------------------------------------------------------------- gap classes + the six scenarios
def _day(gap, first, second=None, flat=None, base=24000.0):
    o = base + gap
    if flat:                                              # flat open: three quiet bars (opening range), then breakout / failure bars
        b = [F.bar(o, o + 8, o - 8, o + 1), F.bar(o + 1, o + 6, o - 6, o), F.bar(o, o + 5, o - 5, o + 1)]
        if flat in ("break", "fail"): b.append(F.bar(o + 1, o + 24, o, o + 22))
        if flat == "fail": b.append(F.bar(o + 22, o + 23, o - 3, o + 2))
        return F.pad(b + F.drift(b[-1][3], 70, 0.0))
    fc = F.first_candles(o, first); b = [fc]
    if second == "bear_ok": b.append(F.bar(fc[3], fc[3] + 1, fc[3] - 15, fc[3] - 12))
    elif second == "bull_ok": b.append(F.bar(fc[3], fc[3] + 15, fc[3] - 1, fc[3] + 12))
    elif second == "bear_fail": b.append(F.bar(fc[3], fc[1] + 10, fc[3] - 2, fc[3] + 5))
    elif second == "bull_fail": b.append(F.bar(fc[3], fc[3] + 2, fc[2] - 10, fc[3] - 5))
    else: b.append(F.bar(fc[3], fc[3] + 1, fc[3] - 1, fc[3]))
    return F.pad(b + F.drift(b[-1][3], 73, 0.0))


def _run_day(bars, warm=6, inputs=None):
    return F.plots(F.run(F.rows_from_days(F.warmup(warm) + [(warm, bars)]), inputs)), warm * 75


CASES = [
    # name, gap, first, second, flat, (class_code, scn@bar1, valid@bar1, openEv@bar1, E_gap@bar1)
    ("gap-down continuation", -30, "bear", "bear_ok", None, (-1, 1, 1, -1, -1)),
    ("gap-down reversal", -50, "bull", "bull_ok", None, (-1, 2, 1, 1, 1)),
    ("gap-down conflicting first candle", -80, "bull", "bull_ok", None, (-1, 6, 0, 1, 0)),
    ("gap-up continuation", 30, "bull", "bull_ok", None, (1, 3, 1, 1, 1)),
    ("gap-up reversal", 50, "bear", "bear_ok", None, (1, 4, 1, -1, -1)),
    ("second candle does not validate", -50, "bear", "bear_fail", None, (-1, 6, 0, 0, 0)),
    ("doji first candle after gap-down", -30, "doji", "bear_ok", None, (-1, 6, 0, 0, 0)),
    ("exceptional gap is never tradable", 120, "bull", "bull_ok", None, (2, 3, 0, 1, 0)),
]


@pytest.mark.parametrize("name,gap,first,second,flat,exp", CASES, ids=[c[0] for c in CASES])
def test_gap_scenarios(name, gap, first, second, flat, exp):
    p, i0 = _run_day(_day(gap, first, second, flat))
    cls, scn, valid, oev, egap = exp
    assert at(p, "Daily ATR", i0) == pytest.approx(100.0) and at(p, "Gap points", i0) == pytest.approx(gap)
    assert at(p, "Gap class code", i0) == cls
    assert at(p, "Gap scenario code", i0 + 1) == scn and at(p, "Scenario validated", i0 + 1) == valid
    assert at(p, "E opening candle", i0 + 1) == oev and at(p, "E gap scenario", i0 + 1) == egap
    assert at(p, "Scenario validated", i0) == 0                          # nothing is validated by the first candle alone


def test_flat_open_breakout_and_failure():
    p, i0 = _run_day(_day(5, None, flat="break"))
    assert at(p, "Gap class code", i0) == 0 and at(p, "Gap scenario code", i0 + 1) == 5 and at(p, "Scenario validated", i0 + 2) == 0
    assert at(p, "Scenario validated", i0 + 3) == 1 and at(p, "E gap scenario", i0 + 3) == 1 and at(p, "Gap scenario code", i0 + 3) == 5
    p, i0 = _run_day(_day(5, None, flat="fail"))
    assert at(p, "Scenario validated", i0 + 3) == 1                      # at the break bar it is valid ...
    assert at(p, "Scenario validated", i0 + 4) == 0 and at(p, "Gap scenario code", i0 + 4) == 6 and at(p, "E gap scenario", i0 + 4) == 0   # ... and withdrawn when the break fails


def test_unknown_gap_class_before_atr_warmup():
    p, i0 = _run_day(_day(-30, "bear", "bear_ok"), warm=3)
    assert math.isnan(at(p, "Gap class code", i0)) and at(p, "Gap scenario code", i0) == 6


def test_gap_scenario_never_changes_retroactively():
    p, i0 = _run_day(_day(-30, "bear", "bear_ok"))
    full = p["Gap scenario code"][i0:i0 + 75]
    p2, _ = _run_day(_day(-30, "bear", "bear_ok")[:30])
    assert p2["Gap scenario code"][i0:i0 + 30] == full[:30]


# ---------------------------------------------------------------- SMC: Pine vs independent Python reference
def _compare_smc(rows, p, skip=0):
    ref = AU.ref_smc(rows, p["Ref ATR"])
    for k, rv in ref.items():
        bad = [i for i in range(skip, len(rv)) if not AU.same(p[k][i], float(rv[i]))]
        assert not bad, f"{k}: {len(bad)} mismatches, first {[(i, p[k][i], rv[i]) for i in bad[:3]]}"
    return ref


def test_smc_events_fvg_and_swings_match_reference_on_synthetic_zigzag():
    pts = [24000, 24030, 24010, 24040, 24004, 24046, 24008, 24060, 24020, 24052, 24012]
    b = F.pad(F.walk([24000, 24000], 20)[:20] + F.walk(pts, 6, start=24000))
    b[44] = F.bar(b[43][3], 24057.0, b[43][3] - 1, b[43][3] + 0.5)       # a wick above the confirmed swing high that closes back below it (sweep)
    gap = [F.bar(24000, 24001, 23999, 24000), F.bar(24000, 24025, 24001, 24024), F.bar(24024, 24030, 24012, 24020)]   # bar0.low(23999)... see FVG note below
    rows = F.rows_from_days(F.warmup(6) + [(6, b)])
    p = F.plots(F.run(rows)); ref = _compare_smc(rows, p, skip=6 * 75)
    ev = set(int(x) for x in ref["Event bits"][6 * 75:])
    assert ev - {0}, "synthetic path produced no SMC events (vacuous test)"
    assert any(x & 1 or x & 2 or x & 4 or x & 8 for x in ev)


def test_fvg_forms_at_the_third_candle_and_is_mitigated_by_a_close_through_it():
    b = F.pad([F.bar(24000, 24001, 23999, 24000)] * 20 + [F.bar(24000, 24003, 23999, 24002), F.bar(24002, 24020, 24001, 24019), F.bar(24019, 24025, 24006, 24024),   # low 24006 > high[2] 24003 -> bull FVG at the 3rd candle
                                                           F.bar(24024, 24026, 24015, 24016), F.bar(24016, 24017, 23990, 23995)])                                   # close 23995 < FVG bottom 24003 -> mitigated
    rows = F.rows_from_days(F.warmup(6) + [(6, b)]); p = F.plots(F.run(rows)); i0 = 6 * 75
    assert at(p, "FVG formed (+1 bull / -1 bear)", i0 + 22) == 1 and at(p, "FVG formed (+1 bull / -1 bear)", i0 + 21) == 0
    ob = lambda k: at(p, "Open bull FVG", i0 + k)                                      # warm-up days already hold up to the cap of 8 zones, so only compare mitigation + the reference
    assert ob(24) < ob(23) and ob(22) <= 8
    _compare_smc(rows, p, skip=6 * 75)


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(HERE), "data", "historical", "stocks_1m", "stocks_1m.db")), reason="stored NIFTY history absent")
def test_smc_matches_reference_on_real_nifty_bars():
    import pine_nifty_harness as H
    rows = H.candles("5", bars=2600); p = F.plots(F.run(rows)); ref = _compare_smc(rows, p, skip=60)
    bits = ref["Event bits"]
    assert sum(1 for x in bits if x & 3) > 10 and sum(1 for x in bits if x & 48) > 5 and sum(ref["FVG formed (+1 bull / -1 bear)"][i] != 0 for i in range(len(bits))) > 20


# ---------------------------------------------------------------- seller stress: symmetrical, zone-based, price-confirmed
STRESS_ONLY = {"Entry trigger": "Stress only"}        # keep gap-scenario triggers out of the stress tests (the synthetic day also contains a valid flat-open breakout)


def _stress_run(bars, inputs=None):
    rows = F.rows_from_days(F.warmup(6) + [(6, bars)]); return F.plots(F.run(rows, {**STRESS_ONLY, **(inputs or {})})), 6 * 75


def test_potential_seller_stress_put_side_and_its_mirror_call_side():
    p, i0 = _stress_run(F.stress_day())
    bear = [j for j in range(75) if p["Stress bear"][i0 + j] == 1]
    assert bear and bear[0] == 46 and all(p["Stress bull"][i0 + j] == 0 for j in range(75)), bear
    assert not any(p["Stress bear"][i0 + j] for j in range(46)) and p["Stress bear zone slot"][i0 + 46] == 4     # slot 4 = previous-day low
    pm, _ = _stress_run(F.mirror(F.stress_day()))
    bull = [j for j in range(75) if pm["Stress bull"][i0 + j] == 1]
    assert bull == bear and all(pm["Stress bear"][i0 + j] == 0 for j in range(75)) and pm["Stress bull zone slot"][i0 + 46] == 4


def test_break_without_prior_respect_is_not_stress_unless_confirmation_is_switched_off():
    p, i0 = _stress_run(F.stress_day(respect=False))
    assert not any(p["Stress bear"][i0 + j] for j in range(75))
    p2, _ = _stress_run(F.stress_day(respect=False), {"Zone must be price-confirmed (respected) before its break counts": False})
    assert any(p2["Stress bear"][i0 + j] for j in range(75))


def test_zone_weight_gate_and_oi_zone_priority():
    p, i0 = _stress_run(F.stress_day(), {"Weight: previous-day high/low price levels": 0.2})
    assert not any(p["Stress bear"][i0 + j] for j in range(75))                          # weight 0.2 < minimum 0.35
    p2, _ = _stress_run(F.stress_day(), {"Put OI #1 strike (highest)": 23950, "Put OI #1 (contracts)": 1000000})
    j = next(j for j in range(75) if p2["Stress bear"][i0 + j] == 1)
    assert p2["Stress bear zone slot"][i0 + j] == 0                                       # the higher-weight manual OI zone is the one reported


LOOSE = {"Reject setup if risk > x ATR": 20, "Minimum distance to nearest opposing level (R)": 0}


def test_default_gates_block_this_setup_by_risk_distance():
    p, i0 = _stress_run(F.stress_day())
    assert any(p["Setup state (+1/-1)"][i0 + j] == -1 for j in range(75))                      # the setup itself is detected ...
    assert not any(p["Signal (+1 CE / -1 PE)"][i0 + j] for j in range(75))                      # ... but risk (48 pts > 3 ATR) keeps it out
    assert p["Risk PE (pts)"][i0 + 46] > 3 * p["Ref ATR"][i0 + 46]


def test_a_setup_edge_that_starts_on_a_blocked_bar_is_not_chased_later():
    p, i0 = _stress_run(F.stress_day(), {**LOOSE, "No-trade if a bar's range > x ATR (abnormal volatility)": 1.5})     # bar 46 (range 12) > 1.5 ATR (11.1): blocked
    assert p["Setup state (+1/-1)"][i0 + 46] == -1 and p["Setup state (+1/-1)"][i0 + 50] == -1                # the state persists on later, quiet bars ...
    assert not any(p["Signal (+1 CE / -1 PE)"][i0 + j] for j in range(75))                                  # ... but no late entry is fired


def test_entry_fills_next_bar_open_with_slippage_and_flattens_at_square_off():
    rows = F.rows_from_days(F.warmup(6) + [(6, F.stress_day())]); i0 = 6 * 75
    res = F.run(rows, {**STRESS_ONLY, **LOOSE}); p = F.plots(res)
    sig = [j for j in range(75) if p["Signal (+1 CE / -1 PE)"][i0 + j] == -1]
    assert sig == [46]
    tr = res["strategy"]["closed_trades"]; assert tr and all(t["size"] < 0 for t in tr)
    assert all(t["entry_time"] == rows[i0 + 47]["time"] for t in tr)                              # every part of the entry fills on the NEXT bar
    assert tr[0]["entry_price"] == pytest.approx(rows[i0 + 47]["open"], abs=0.6)                  # next-bar open +/- the 10-tick (0.5 pt) slippage
    assert all(t["exit_time"] <= rows[i0 + 74]["time"] for t in tr) and abs(sum(t["size"] for t in tr)) == 10      # all 10 units are closed by the end of the session


def test_liquidity_sweep_bos_and_failed_breakout_on_explicit_bars():
    b = [F.bar(24000, 24001, 23999, 24000)] * 20
    b += [F.bar(24000, 24006, 23999, 24005), F.bar(24005, 24011, 24004, 24010), F.bar(24010, 24016, 24009, 24015), F.bar(24015, 24021, 24014, 24020),
          F.bar(24020, 24030, 24019, 24025),                                           # bar 24: pivot high 24030 (strictly above 3 bars left)
          F.bar(24025, 24022, 24012, 24014), F.bar(24014, 24013, 24007, 24009), F.bar(24009, 24010, 24004, 24006)]   # bars 25-27 lower -> pivot confirmed at bar 27
    b += [F.bar(24006, 24008, 24004, 24005), F.bar(24005, 24007, 24003, 24004), F.bar(24004, 24007, 24002, 24006)]    # 28-30 quiet
    b += [F.bar(24006, 24034, 24005, 24012)]                                         # bar 31: wick above 24030, close back below -> SWEEP HIGH
    b += [F.bar(24012, 24035, 24010, 24014)]                                         # bar 32: another wick above, no second sweep for the same swing
    b += [F.bar(24014, 24040, 24013, 24036)]                                         # bar 33: close > 24030 -> BOS up
    b += [F.bar(24036, 24037, 24025, 24026)]                                         # bar 34: close back below 24030 within the window -> FAILED BREAKOUT
    rows = F.rows_from_days(F.warmup(6) + [(6, F.pad(b))]); p = F.plots(F.run(rows)); i0 = 6 * 75
    bits = [int(p["Event bits"][i0 + j]) for j in range(40)]
    assert bits[31] == 16 and bits[32] == 0 and bits[33] == 1 and bits[34] == 64, bits[28:36]
    assert at(p, "Ref swing high", i0 + 26) != at(p, "Ref swing high", i0 + 27) and at(p, "Ref swing high", i0 + 27) == 24030      # visible only R=3 bars after the pivot bar
    assert at(p, "E sweep", i0 + 31) == -1                                           # sweep of highs = bearish evidence, fresh for the evidence window
    _compare_smc(rows, p, skip=6 * 75)


# ---------------------------------------------------------------- bull / bear symmetry on real bars
@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(HERE), "data", "historical", "stocks_1m", "stocks_1m.db")), reason="stored NIFTY history absent")
def test_mirrored_market_gives_mirrored_output():
    import pine_nifty_harness as H
    rows = H.candles("5", bars=3200); K = 50000.0
    mir = [{"time": r["time"], "open": K - r["open"], "high": K - r["low"], "low": K - r["high"], "close": K - r["close"], "volume": 0} for r in rows]
    a, b = F.plots(F.run(rows)), F.plots(F.run(mir))
    neg = ["E HTF", "E gap scenario", "E levels", "E opening candle", "E structure", "E sweep", "E momentum", "E vwap", "Evidence index (net)", "Gap points", "Gap / daily ATR", "Candle/volume proxy dir",
           "Signal (+1 CE / -1 PE)", "Setup state (+1/-1)", "HTF vote 1", "HTF vote 2", "HTF vote 3", "HTF vote 4", "Gap class code", "HTF evidence"]
    same = ["Active components", "Daily ATR", "Ref ATR", "Bar of day", "Scenario validated", "Trades today", "Day halted"]
    swap = [("Stress bull", "Stress bear"), ("Evidence bull", "Evidence bear"), ("Risk CE (pts)", "Risk PE (pts)"), ("Open bull FVG", "Open bear FVG")]
    n = len(rows); bad = {}
    for k in neg:
        d = [i for i in range(n) if not AU.same(a[k][i], -b[k][i] if not math.isnan(b[k][i]) else nan, 1e-7)]
        if d: bad[k] = (len(d), d[:3])
    for k in same:
        d = [i for i in range(n) if not AU.same(a[k][i], b[k][i], 1e-7)]
        if d: bad[k] = (len(d), d[:3])
    for x, y in swap:
        d = [i for i in range(n) if not AU.same(a[x][i], b[y][i], 1e-7)]
        if d: bad[x] = (len(d), d[:3])
    sc = {1: 3, 2: 4, 3: 1, 4: 2, 5: 5, 6: 6}
    d = [i for i in range(n) if not math.isnan(a["Gap scenario code"][i]) and sc[int(a["Gap scenario code"][i])] != int(b["Gap scenario code"][i])]
    if d: bad["scenario"] = (len(d), d[:3])
    bs = {1: 2, 2: 1, 4: 8, 8: 4, 16: 32, 32: 16, 64: 128, 128: 64, 256: 512, 512: 256}
    def sw(x):
        x = int(x); r = 0
        for bit, to in bs.items():
            if x & bit: r |= to
        return r
    d = [i for i in range(n) if sw(a["Event bits"][i]) != int(b["Event bits"][i])]
    if d: bad["Event bits"] = (len(d), d[:3])
    d = [i for i in range(n) if not AU.same(a["Ref RSI"][i], 100 - b["Ref RSI"][i], 1e-6)]
    if d: bad["RSI"] = (len(d), d[:3])
    assert not bad, bad
    assert sum(1 for x in a["Stress bull"] if x == 1) + sum(1 for x in a["Stress bear"] if x == 1) > 0 or sum(1 for x in a["Setup state (+1/-1)"] if x != 0) > 0     # not a vacuous test


@pytest.mark.skipif(not os.path.exists(os.path.join(os.path.dirname(HERE), "data", "historical", "stocks_1m", "stocks_1m.db")), reason="stored NIFTY history absent")
def test_hard_rules_hold_on_real_history_including_cooldown_after_a_loss():
    """Regression: before the fix a cooldown created by the exit bar of a losing trade was applied one bar too late (entries 1 bar after a loss)."""
    import pine_nifty_harness as H
    from datetime import datetime, timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    rows = H.candles("5", d_from="2025-07-01", d_to="2025-10-01"); res = F.run(rows); p = F.plots(res)
    times = [r["time"] for r in rows]; idx = {t: i for i, t in enumerate(times)}; byent = {}
    for t in res["strategy"]["closed_trades"]: byent.setdefault(t["entry_time"], []).append(t)
    ents = sorted(byent); assert len(ents) >= 8, "not enough trades to test the rules"
    day = lambda ms: datetime.fromtimestamp(ms / 1000, IST).date()
    per_day = {}
    for k in ents:
        i = idx[k]; si = i - 1; dt = datetime.fromtimestamp(k / 1000, IST); per_day[dt.date()] = per_day.get(dt.date(), 0) + 1
        sdt = datetime.fromtimestamp(times[si] / 1000, IST)
        assert p["Signal (+1 CE / -1 PE)"][si] in (1.0, -1.0)                                  # every entry follows a signal on the previous bar
        assert p["Bar of day"][si] >= 1                                                       # never on the first candle
        assert sdt.hour * 60 + sdt.minute < 14 * 60 + 45                                      # no new entry from 14:45
        assert abs(p["Evidence index (net)"][si]) >= 0.35 - 1e-9 and p["Active components"][si] >= 4
        ex = max(t["exit_time"] for t in byent[k]); assert day(ex) == dt.date()                  # nothing held overnight
    assert max(per_day.values()) <= 2                                                         # max trades per session
    for a, b in zip(ents, ents[1:]):
        ea = max(t["exit_time"] for t in byent[a])
        assert idx[b] > idx[ea]                                                               # one position at a time
        if sum(t["profit"] for t in byent[a]) < 0 and day(a) == day(b):
            assert idx[b] - idx[ea] >= 6 + 1, (str(datetime.fromtimestamp(b / 1000, IST)), idx[b] - idx[ea])     # cooldown: signal >= 6 bars after the loss bar, fill one bar later
