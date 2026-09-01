"""P1 dynamic S/R engine — zones, 0-100 strength, index vs option mode."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines.sr_engine import compute_sr


def mk(prices, wick=0.4, vol=1000):
    """[{t,o,h,l,c,v}] from a close list; o = prev close."""
    out, prev = [], prices[0]
    for i, c in enumerate(prices):
        o = prev
        out.append({"t": f"2026-08-27T09:{15 + i:02d}:00" if i < 45 else f"2026-08-27T10:{i-45:02d}:00",
                    "o": round(o, 2), "h": round(max(o, c) + wick, 2),
                    "l": round(min(o, c) - wick, 2), "c": round(c, 2), "v": vol})
        prev = c
    return out


def _range_path(lo=100.0, hi=110.0, cycles=6):
    """Oscillate lo<->hi several times so both edges get real touches+rejections."""
    seg = []
    for _ in range(cycles):
        seg += [lo + 0.2, lo + 1, lo + 3, lo + 6, hi - 1, hi - 0.2, hi - 2, hi - 5, lo + 4, lo + 0.5]
    return seg


def test_range_market_finds_both_edges_with_strength():
    bars = mk(_range_path())
    r = compute_sr({"5m": bars}, mode="index")
    assert r["status"] == "OK"
    assert r["support"] and r["resistance"]
    assert r["support"]["level"] < r["price"] < r["resistance"]["level"]
    # both edges touched many times and rejected -> meaningful strength
    assert r["support_strength"] > 15 and r["resistance_strength"] > 15
    assert r["support"]["dist_atr"] < 0 and r["resistance"]["dist_atr"] > 0
    assert 0 <= r["support_strength"] <= 100 and 0 <= r["resistance_strength"] <= 100
    # support near ~100, resistance near ~110
    assert 96 <= r["support"]["level"] <= 104
    assert 106 <= r["resistance"]["level"] <= 114


def test_oi_walls_contribute_in_index_mode():
    prices = [24150 + (i % 5) * 4 - 8 for i in range(40)]
    bars = mk(prices, wick=6, vol=0)
    chain = [
        {"strike": 24000, "ce": {"oi": 100000, "oi_chg": 0}, "pe": {"oi": 9000000, "oi_chg": 400000}},
        {"strike": 24100, "ce": {"oi": 200000, "oi_chg": 0}, "pe": {"oi": 3000000, "oi_chg": 0}},
        {"strike": 24150, "ce": {"oi": 500000, "oi_chg": 0}, "pe": {"oi": 500000, "oi_chg": 0}},
        {"strike": 24200, "ce": {"oi": 2500000, "oi_chg": 0}, "pe": {"oi": 200000, "oi_chg": 0}},
        {"strike": 24300, "ce": {"oi": 9000000, "oi_chg": 500000}, "pe": {"oi": 90000, "oi_chg": 0}},
    ]
    r = compute_sr({"5m": bars}, chain=chain, mode="index")
    srcs = {s for z in r["levels"] for s in z["sources"]}
    assert "oi_wall_ce" in srcs and "oi_wall_pe" in srcs
    # the CE wall at 24300 should surface as a resistance zone with oi_backing
    res_zone = next((z for z in r["levels"] if 24270 <= z["level"] <= 24330), None)
    assert res_zone is not None
    assert res_zone["components"]["oi_backing"] > 0
    assert "oi" in res_zone["families"]


def test_option_mode_redistributes_oi_weight_and_still_scores():
    # option premium chops between 40 and 70
    prices = [40 + 3, 45, 55, 68, 69, 60, 48, 41, 40.5, 52] * 4
    bars = mk(prices, wick=1.5, vol=800)
    r = compute_sr({"5m": bars}, mode="option")
    assert r["status"] == "OK" and r["mode"] == "option"
    assert r["support"] and r["resistance"]
    # no OI families anywhere in option mode
    fam = {f for z in r["levels"] for f in z["families"]}
    assert "oi" not in fam
    for z in r["levels"]:
        assert z["components"]["oi_backing"] == 0.0
        assert 0 <= z["strength"] <= 100


def test_prevday_pivots_present_in_index_mode():
    bars = mk([100 + (i % 7) for i in range(30)], vol=0)
    r = compute_sr({"5m": bars}, prev_day={"high": 112, "low": 96, "close": 104}, mode="index")
    srcs = {s for z in r["levels"] for s in z["sources"]}
    assert "prevday_high" in srcs and "prevday_low" in srcs
    assert any(s.startswith("pivot_") for s in srcs)


def test_htf_agreement_marks_family():
    # a clean level at ~120 on both 5m and 15m
    p5 = [110, 113, 117, 120, 119, 116, 112, 110, 114, 120, 118, 115, 111, 113, 120, 117,
          114, 110, 116, 120, 119, 115]
    p15 = [108, 114, 120, 117, 112, 119, 120, 115, 111, 120, 116, 113, 118, 120, 114, 110]
    r = compute_sr({"5m": mk(p5), "15m": mk(p15)}, mode="index")
    fams = {f for z in r["levels"] for f in z["families"]}
    assert "htf" in fams


def test_insufficient_data():
    r = compute_sr({"5m": mk([100, 101, 102, 103])}, mode="index")
    assert r["status"] == "DATA_UNAVAILABLE"


def test_deterministic():
    bars = mk(_range_path())
    chain = [{"strike": 100 + i, "ce": {"oi": 1000 * i, "oi_chg": 0},
              "pe": {"oi": 1000 * (10 - i), "oi_chg": 0}} for i in range(1, 10)]
    a = compute_sr({"5m": bars}, chain=chain, mode="index")
    b = compute_sr({"5m": bars}, chain=chain, mode="index")
    assert a == b


def test_strength_bounds_and_shape():
    r = compute_sr({"5m": mk(_range_path())}, mode="index")
    for z in r["levels"]:
        assert 0.0 <= z["strength"] <= 100.0
        assert z["zone"][0] <= z["level"] <= z["zone"][1] + 1e-6
        assert set(z["components"]) >= {"confluence", "touch_quality", "recency", "vwap_prox"}


# --------------------------------------------------------------------------- #
# Audit (2026-09-01): S/R + OI-wall engine vs a real NATURALGAS chain snapshot
# --------------------------------------------------------------------------- #
_L = 100_000
_NG_CHAIN = [
    {"strike": 255, "ce": {"oi": 1.98 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 44.99 * _L, "expiry": "23SEP2026"}},
    {"strike": 260, "ce": {"oi": 21.65 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 82.21 * _L, "expiry": "23SEP2026"}},
    {"strike": 265, "ce": {"oi": 12.01 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 53.24 * _L, "expiry": "23SEP2026"}},
    {"strike": 270, "ce": {"oi": 49.70 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 96.85 * _L, "expiry": "23SEP2026"}},
    {"strike": 275, "ce": {"oi": 38.24 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 68.19 * _L, "expiry": "23SEP2026"}},
    {"strike": 280, "ce": {"oi": 94.09 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 70.22 * _L, "expiry": "23SEP2026"}},
    {"strike": 285, "ce": {"oi": 36.68 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 19.41 * _L, "expiry": "23SEP2026"}},
    {"strike": 290, "ce": {"oi": 57.94 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 15.30 * _L, "expiry": "23SEP2026"}},
    {"strike": 295, "ce": {"oi": 20.30 * _L, "expiry": "23SEP2026"}, "pe": {"oi": 2.34 * _L, "expiry": "23SEP2026"}},
]


def _ng_bars():
    # a plausible ~278.7 tape that oscillates a couple of points — deterministic
    seg = [278.7, 279.1, 279.6, 279.2, 278.4, 277.9, 278.3, 278.9, 279.4, 278.6]
    return mk(seg * 6, wick=0.35, vol=1000)


def test_ng_snapshot_oi_walls_recognised_as_candidates():
    r = compute_sr({"5m": _ng_bars(), "15m": _ng_bars()}, chain=_NG_CHAIN, mode="index",
                   symbol="NATURALGAS")
    assert r["status"] == "OK"
    srcs = {s for z in r["levels"] for s in z["sources"]}
    # the single largest CE OI (280, 94.09L) and PE OI (270, 96.85L) DO become walls
    assert "oi_wall_ce" in srcs and "oi_wall_pe" in srcs
    ce_zone = next((z for z in r["levels"] if 279.5 <= z["level"] <= 280.5 and "oi" in z["families"]), None)
    pe_zone = next((z for z in r["levels"] if 269.5 <= z["level"] <= 270.5 and "oi" in z["families"]), None)
    assert ce_zone and pe_zone, "280 CE wall and 270 PE wall must appear as OI-family zones"


def test_ng_snapshot_oi_is_a_minor_factor_not_dominant():
    # RES STR < SUP STR despite 280 holding the biggest wall in the chain is
    # mathematically consistent: OI is ~17% of the score, the rest is price
    # structure. Prove OI is not the dominant driver.
    r = compute_sr({"5m": _ng_bars(), "15m": _ng_bars()}, chain=_NG_CHAIN, mode="index",
                   symbol="NATURALGAS")
    d = r["sr_diag"]
    # the biggest CE/PE OI strikes are correctly identified as the top walls
    assert d["oi_walls"]["call_walls"][0]["strike"] == 280
    assert d["oi_walls"]["put_walls"][0]["strike"] == 270
    # the SELECTED support / resistance are price-structure zones, not the raw
    # OI strikes — a lone OI wall with no swing/pivot confluence does not win
    for kind in ("support", "resistance"):
        z = r[kind]
        if z is None:
            continue
        assert z["families"] != ["oi"], f"{kind} must not be a lone OI wall"
    # any zone whose ONLY family is 'oi' scores modestly (confluence 0.25 +
    # oi_backing only) — never OI-dominant
    for z in r["levels"]:
        if z["families"] == ["oi"]:
            assert z["strength"] < 45
            assert z["components"]["oi_backing"] > 0.3


def test_ng_snapshot_diagnostic_shape():
    r = compute_sr({"5m": _ng_bars(), "15m": _ng_bars()}, chain=_NG_CHAIN, mode="index",
                   symbol="NATURALGAS")
    d = r["sr_diag"]
    ow = d["oi_walls"]
    assert ow["symbol"] == "NATURALGAS" and ow["expiry"] == "23SEP2026" and ow["spot"]
    # top-3 walls each side, with the required fields
    for w in ow["call_walls"] + ow["put_walls"]:
        assert set(w) >= {"strike", "oi", "oi_chg", "dist_pct", "raw_wall_score", "dist_weighted_score"}
    # the far 290 CE wall is present but its distance-weighted view is penalised
    c290 = next(w for w in ow["call_walls"] if w["strike"] == 290)
    c280 = next(w for w in ow["call_walls"] if w["strike"] == 280)
    assert abs(c290["dist_pct"]) > abs(c280["dist_pct"])
    assert c290["dist_weighted_score"] < c280["dist_weighted_score"]
    # per selected level: symbol / expiry / spot / nearest strike / OI / reason
    for kind in ("support", "resistance"):
        lv = d[kind]
        if lv is None:
            continue
        assert set(lv) >= {"kind", "symbol", "expiry", "spot", "level", "strength",
                           "dist_pct", "dist_atr", "nearest_strike", "call_oi", "put_oi",
                           "oi_change", "components", "reason"}


def test_zero_or_missing_oi_creates_no_phantom_wall():
    from app.engines.sr_engine import _candidates
    blank = [{"strike": s, "ce": {}, "pe": {}} for s in (270, 275, 280, 285, 290)]
    cands = _candidates([278.0] * 20, [277.0] * 20, [278.0] * 20, [0.0] * 20,
                        atr=0.5, vwap=278.0, mode="index", chain=blank,
                        prev_day=None, round_step=50.0)
    assert not [c for c in cands if c[1].startswith("oi_")], "missing OI must not seed a wall"
    # and a chain where only PE OI is present -> only a PE wall, no CE wall
    half = [{"strike": s, "ce": {}, "pe": {"oi": 1000 * (s - 265)}} for s in (270, 275, 280, 285)]
    cands = _candidates([278.0] * 20, [277.0] * 20, [278.0] * 20, [0.0] * 20,
                        atr=0.5, vwap=278.0, mode="index", chain=half,
                        prev_day=None, round_step=50.0)
    kinds = {c[1] for c in cands if c[1].startswith("oi_")}
    assert kinds == {"oi_wall_pe"}


def test_shared_engine_nifty_and_crude_still_ok():
    # the engine is shared across NIFTY / NG / CRUDE — a smoke check that the
    # new symbol kwarg + diagnostic do not perturb other symbols
    nifty_chain = [{"strike": 24000 + 50 * i,
                    "ce": {"oi": 1e6 * (1 + (i % 3)), "expiry": "04SEP2026"},
                    "pe": {"oi": 1e6 * (3 - (i % 3)), "expiry": "04SEP2026"}} for i in range(9)]
    r = compute_sr({"5m": mk(_range_path())}, chain=nifty_chain, mode="index", symbol="NIFTY")
    assert r["status"] == "OK" and 0 <= r["support_strength"] <= 100
    assert r["sr_diag"]["oi_walls"]["symbol"] == "NIFTY"

    crude_chain = [{"strike": 8000 + 50 * i,
                    "ce": {"oi": 5e4 * (1 + i), "expiry": "17SEP2026"},
                    "pe": {"oi": 5e4 * (9 - i), "expiry": "17SEP2026"}} for i in range(9)]
    bars = mk([8100 + (i % 6) * 3 for i in range(40)], wick=4)
    r = compute_sr({"5m": bars}, chain=crude_chain, mode="index", symbol="CRUDEOIL")
    assert r["status"] == "OK" and 0 <= r["resistance_strength"] <= 100
    assert r["sr_diag"]["oi_walls"]["expiry"] == "17SEP2026"


def test_compute_sr_symbol_kwarg_is_optional_and_backwards_compatible():
    a = compute_sr({"5m": mk(_range_path())}, mode="index")               # old call
    b = compute_sr({"5m": mk(_range_path())}, mode="index", symbol=None)  # new kwarg
    assert a["support_strength"] == b["support_strength"]
    assert a["resistance_strength"] == b["resistance_strength"]
    assert "sr_diag" in a
