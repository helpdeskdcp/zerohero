"""
MATHEMATICAL_CONFLUENCE_ENGINE_V1 — unit tests.

Includes the NATURALGAS mathematical validation (spec section 25) and the
anti-look-ahead guarantee (section 27). All levels computed dynamically from
input OHLC — nothing hard-coded.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.mathematical_confluence import (
    MathematicalConfluenceEngine, classical_pivots, gann_levels,
    cluster_levels, detect_swings, market_position, classify_regime, oi_matrix,
)
from app.mathematical_confluence.levels import normalized_levels
from app.mathematical_confluence import scoring


# --------------------------------------------------------- section 25: NATURALGAS
def test_naturalgas_pivot_and_gann_validation():
    PDH, PDL, PDC = 282.20, 277.20, 278.80
    piv = classical_pivots(PDH, PDL, PDC)
    # P = (282.20 + 277.20 + 278.80) / 3 = 279.40
    assert abs(piv["pivot"] - 279.40) < 0.01
    assert abs(piv["r1"] - 281.60) < 0.05      # 2*279.40 - 277.20
    assert abs(piv["s1"] - 276.60) < 0.05      # 2*279.40 - 282.20
    assert abs(piv["r2"] - 284.40) < 0.05      # 279.40 + 5.00
    assert abs(piv["s2"] - 274.40) < 0.05
    assert abs(piv["r3"] - 286.60) < 0.05      # 282.20 + 2*(279.40-277.20)
    assert abs(piv["s3"] - 271.60) < 0.05

    g = gann_levels(PDH, PDL)
    assert abs(g["gann_balance"] - 279.70) < 0.01   # (282.20 + 277.20) / 2
    assert abs(g["range"] - 5.00) < 0.01


def test_naturalgas_confluence_zone_forms_around_279_4_to_280():
    PDH, PDL, PDC = 282.20, 277.20, 278.80
    lv = normalized_levels(pdh=PDH, pdl=PDL, pdc=PDC, today_open=279.90,
                           oi_support=280.0)                 # OI wall confirms
    zones = cluster_levels(lv, spot=280.5, tol_pct=0.0022)   # ~0.6 pt tolerance
    # a zone should merge pivot 279.40 + gann balance 279.70 + OI 280.0 (+ today_open)
    z = min(zones, key=lambda z: abs(z["center"] - 279.7))
    assert 279.3 <= z["center"] <= 280.05
    assert z["evidence_count"] >= 3
    fams = set(z["families"])
    assert "pivot" in fams and "gann_balance" in fams
    srcs = set(z["sources"])
    assert "PIVOT" in srcs and "GANN_BALANCE" in srcs and "OI_SUPPORT_STRIKE" in srcs

    # 277.20 PDL support zone exists and is distinct
    pdl_zone = min(zones, key=lambda z: abs(z["center"] - 277.2))
    assert abs(pdl_zone["center"] - 277.2) < 0.4 and "prev_day" in pdl_zone["families"]


# --------------------------------------------------------- section 27: no look-ahead
def test_swings_are_causal_never_use_future_bars():
    bars = [{"high": 10 + (i % 5), "low": 8 + (i % 5), "close": 9 + (i % 5)} for i in range(20)]
    # a pivot at index i needs n bars each side -> confirmed at i+n
    full = detect_swings(bars, n=2)
    for s in full["swing_highs"] + full["swing_lows"]:
        assert s["confirmed_at"] == s["index"] + 2
    # if the caller only knows up to index 10, nothing confirmed after 10 leaks
    partial = detect_swings(bars, n=2, now_index=10)
    for s in partial["swing_highs"] + partial["swing_lows"]:
        assert s["confirmed_at"] <= 10


# --------------------------------------------------------- section 12: market position
def test_market_position_and_regime():
    mp = market_position(pdh=282.2, pdl=277.2, pdc=278.8, today_open=283.0,
                         day_high=284.0, day_low=282.5, current_price=283.5)
    assert mp["open_type"] == "GAP_UP_ABOVE_PDH"
    assert mp["position_in_prev_day_range"] > 1.0     # price above PDH
    r = classify_regime(mp, prev_range=5.0, day_range=1.5, mom_3m=0.2)
    assert r["regime"] == "BULLISH_EXPANSION" and r["reasons"]


# --------------------------------------------------------- section 6/7: OI matrix
def test_oi_matrix_interpretation_and_walls_no_absolute_claims():
    chain = [
        {"strike": 277.5, "ce_oi": 1000, "ce_oi_change": -50, "ce_ltp": 6.0, "ce_ltp_change": 0.2,
         "pe_oi": 9000, "pe_oi_change": 1500, "pe_ltp": 2.1, "pe_ltp_change": -0.4},   # PE writing
        {"strike": 280.0, "ce_oi": 4000, "ce_oi_change": 200, "ce_ltp": 3.0, "ce_ltp_change": -0.1,
         "pe_oi": 8000, "pe_oi_change": 900, "pe_ltp": 3.4, "pe_ltp_change": -0.3},
        {"strike": 282.5, "ce_oi": 12000, "ce_oi_change": 3000, "ce_ltp": 1.4, "ce_ltp_change": -0.5,
         "pe_oi": 1200, "pe_oi_change": -100, "pe_ltp": 6.6, "pe_ltp_change": 0.3},   # CE writing
    ]
    m = oi_matrix(chain, spot=280.0)
    assert m["status"] == "OK"
    assert m["walls"]["PUT_SUPPORT_WALL"]["strike"] in (277.5, 280.0)
    assert m["walls"]["CALL_RESISTANCE_WALL"]["strike"] == 282.5
    r0 = next(r for r in m["rows"] if r["strike"] == 277.5)
    assert r0["pe_interpretation"] == "fresh_writing"           # PE OI up + PE LTP down
    assert 0.0 <= r0["pe_interpretation_confidence"] <= 1.0     # confidence, not absolute
    assert m["pcr"] is not None


def test_oi_matrix_data_insufficient_when_thin():
    m = oi_matrix([{"strike": 100, "ce_oi": 1}], spot=100)
    assert m["status"] == "DATA_INSUFFICIENT" and m["rows"] == []


# --------------------------------------------------------- section 28: data gate
def test_engine_returns_data_insufficient_with_exact_missing_fields():
    eng = MathematicalConfluenceEngine()
    out = eng.evaluate(instrument="NIFTY", timestamp="2026-09-03T10:00:00Z",
                       prev_day={"high": 24000, "low": None, "close": 23900},
                       today_open=23950, current_price=None,
                       day_high=24010, day_low=23940)
    assert out["status"] == "DATA_INSUFFICIENT"
    assert "previous_day_low" in out["missing"] and "current_price" in out["missing"]
    assert out["signal_type"] == "NO_TRADE"


# --------------------------------------------------------- section 13/14: full run
def test_engine_full_evaluation_shape_and_never_single_indicator():
    eng = MathematicalConfluenceEngine()
    bars = [{"high": 279.8 + 0.1 * (i % 4), "low": 279.2 + 0.1 * (i % 4),
             "close": 279.5 + 0.1 * (i % 4)} for i in range(30)]
    chain = [
        {"strike": 278.0, "ce_oi": 900, "ce_oi_change": -20, "ce_ltp": 3.1, "ce_ltp_change": 0.1,
         "pe_oi": 8000, "pe_oi_change": 1200, "pe_ltp": 1.2, "pe_ltp_change": -0.3},
        {"strike": 279.5, "ce_oi": 3000, "ce_oi_change": 100, "ce_ltp": 2.0, "ce_ltp_change": 0.0,
         "pe_oi": 9000, "pe_oi_change": 1500, "pe_ltp": 2.2, "pe_ltp_change": -0.4},
        {"strike": 281.0, "ce_oi": 11000, "ce_oi_change": 2500, "ce_ltp": 1.0, "ce_ltp_change": -0.4,
         "pe_oi": 1500, "pe_oi_change": -80, "pe_ltp": 4.0, "pe_ltp_change": 0.2},
    ]
    out = eng.evaluate(instrument="NATURALGAS", timestamp="2026-09-03T18:30:00Z",
                       prev_day={"high": 282.2, "low": 277.2, "close": 278.8},
                       today_open=279.9, current_price=279.6,
                       day_high=280.4, day_low=279.1,
                       current_volume=1400, avg_volume=1000,
                       bars=bars, chain=chain, candle_signals=["hammer", "lower_wick"],
                       mom_3m=0.05)
    assert out["status"] == "OK"
    assert out["signal_type"] in ("BUY_CE", "BUY_PE", "NO_TRADE", "WAIT",
                                  "BREAKOUT_WATCH", "BREAKDOWN_WATCH", "REVERSAL_WATCH")
    # score is a weighted composite of 7 sub-scores — never a single indicator
    b = out["score_breakdown"]
    assert set(b) == {"mathematical", "oi", "price_action", "volume", "breakout", "retest", "swing"}
    assert 0 <= out["confluence_score"] <= 100
    assert out["reason_codes"]                      # always explained
    assert "UNCALIBRATED" in out["calibration"]
    assert out["mathematical_levels"]["pivots"]["pivot"] is not None


def test_scoring_weights_are_configurable():
    sub = {"mathematical": 20, "oi": 20, "price_action": 20, "volume": 10,
           "breakout": 10, "retest": 10, "swing": 10}
    a = scoring.confluence_score(sub)
    b = scoring.confluence_score(sub, {"oi": 0.40, "mathematical": 0.0})
    assert a["confluence_score"] == 100.0
    assert b["breakdown"]["oi"]["weight_pct"] == 40.0
    assert "NOT statistically calibrated" in a["weights_source"]
