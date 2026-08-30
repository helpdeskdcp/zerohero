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
