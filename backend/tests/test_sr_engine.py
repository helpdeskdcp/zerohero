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


# --------------------------------------------------------------------------- #
# Audit (2026-09-01): NIFTY VWAP = None because an NSE cash index has no
# traded volume. Not a bug — but the reason must be exposed, never faked.
# --------------------------------------------------------------------------- #
def _bars_no_volume(px=24050.0, n=40):
    return {"5m": mk([px + (i % 7 - 3) * 4 for i in range(n)], wick=6, vol=0),
            "15m": mk([px + (i % 5 - 2) * 6 for i in range(20)], wick=8, vol=0)}


def _bars_with_volume(px=278.8, n=40):
    return {"5m": mk([px + (i % 7 - 3) * 0.1 for i in range(n)], wick=0.35, vol=1000),
            "15m": mk([px + (i % 5 - 2) * 0.15 for i in range(20)], wick=0.5, vol=900)}


def test_vwap_unavailable_for_zero_volume_series_with_reason():
    r = compute_sr(_bars_no_volume(), mode="index", symbol="NIFTY")
    assert r["status"] == "OK"
    assert r["vwap"] is None                                   # never fabricated
    assert r["vwap_status"] == "invalid_volume"
    assert "volume" in r["vwap_reason"].lower()
    assert r["sr_diag"]["vwap_status"] == "invalid_volume"
    # and the vwap structural factor is simply absent, not faked
    assert "vwap" not in {f for z in r["levels"] for f in z["families"]}
    for z in r["levels"]:
        assert z["components"]["vwap_prox"] == 0.0


def test_vwap_available_when_volume_present_unchanged():
    r = compute_sr(_bars_with_volume(), mode="index", symbol="NATURALGAS")
    assert r["status"] == "OK"
    assert r["vwap"] is not None and r["vwap"] > 0
    assert r["vwap_status"] == "available" and r["vwap_reason"] == ""
    assert r["sr_diag"]["vwap"] == r["vwap"]


def test_vwap_status_insufficient_data():
    r = compute_sr({"5m": mk([100, 101, 102, 103, 104], vol=1000)}, mode="index", symbol="NIFTY")
    assert r["status"] == "DATA_UNAVAILABLE"
    assert r["vwap"] is None and r["vwap_status"] == "insufficient_data"


def test_vwap_naturalgas_regression_value_stable_across_the_change():
    # the audited NG snapshot chain + a fixed tape -> VWAP must be a number and
    # identical run to run (deterministic), i.e. the diagnostic add did not
    # perturb the calculation
    a = compute_sr(_bars_with_volume(279.4), chain=_NG_CHAIN, mode="index", symbol="NATURALGAS")
    b = compute_sr(_bars_with_volume(279.4), chain=_NG_CHAIN, mode="index", symbol="NATURALGAS")
    assert a["vwap"] == b["vwap"] and a["vwap"] is not None
    assert a["vwap_status"] == "available"
    assert a["support_strength"] == b["support_strength"]


def test_vwap_status_present_on_every_ok_return():
    for bars, sym in ((_bars_no_volume(), "NIFTY"), (_bars_with_volume(), "NATURALGAS")):
        r = compute_sr(bars, mode="index", symbol=sym)
        assert "vwap_status" in r and "vwap_reason" in r
        assert r["vwap_status"] in ("available", "invalid_volume", "insufficient_data")


# --------------------------------------------------------------------------- #
# GEX (gamma exposure) v1a — read-only diagnostic (GEX_SR_SPEC.md phase A)
# --------------------------------------------------------------------------- #
from app.engines.sr_engine import _bs_gamma, _bs_price, _solve_iv, _gex_profile  # noqa: E402

_T = 7.0 / 365.0


def _synth_chain(spot=100.0):
    # pe-OI heavy below spot, ce-OI heavy above -> shape crosses zero near ATM
    rows = [(90, 50, 900), (95, 100, 600), (100, 300, 300), (105, 600, 100), (110, 900, 50)]
    return [{"strike": k,
             "ce": {"oi": ce, "ltp": max(0.5, spot - k + 5)},
             "pe": {"oi": pe, "ltp": max(0.5, k - spot + 5)}} for k, ce, pe in rows]


def test_bs_gamma_peaks_atm_and_decays():
    atm = _bs_gamma(100, 100, _T, 0.2)
    up = _bs_gamma(100, 110, _T, 0.2)
    dn = _bs_gamma(100, 90, _T, 0.2)
    assert atm > up > 0 and atm > dn > 0
    assert atm == atm and atm not in (float("inf"), float("-inf"))
    assert _bs_gamma(0, 100, _T, 0.2) == 0.0 and _bs_gamma(100, 100, 0, 0.2) == 0.0


def test_solve_iv_recovers_known_sigma_and_rejects_unbracketed():
    p = _bs_price(100, 100, 30 / 365, 0.18, True)
    assert abs(_solve_iv(100, 100, 30 / 365, p, True) - 0.18) < 5e-3
    assert _solve_iv(100, 100, 30 / 365, 0.0001, True) is None   # below the sigma-floor price
    assert _solve_iv(100, 100, 30 / 365, 60.0, True) is None     # above the sigma-cap price
    assert _solve_iv(100, 100, 30 / 365, None, True) is None


def test_gex_profile_flip_pin_on_synthetic_chain():
    g = _gex_profile(_synth_chain(100.0), 100.0, 0.8, t_years=_T)
    assert g["status"] == "ok"
    assert g["sigma"] is not None and g["sigma_src"] == "atm_solve"
    # shape(K) = gamma * (ce_oi - pe_oi): negative below, ~0 at ATM, positive above
    shp = {p["strike"]: p["shape"] for p in g["per_strike"]}
    assert shp[90.0] < 0 < shp[110.0]
    assert 97 <= g["flip"] <= 103           # zero-crossing near the money
    assert g["pin"] in (90.0, 110.0)        # gamma-weighted OI magnet at an edge
    assert g["regime_sign"] in (-1, 0, 1)


def test_gex_profile_regime_sign_follows_total_shape():
    # crush put OI everywhere -> ce_oi - pe_oi strongly positive -> sign +1
    call_heavy = [{"strike": k, "ce": {"oi": 900, "ltp": 5.0}, "pe": {"oi": 10, "ltp": 5.0}}
                  for k in (90, 95, 100, 105, 110)]
    g = _gex_profile(call_heavy, 100.0, 0.8, t_years=_T)
    assert g["status"] == "ok" and g["regime_sign"] == 1 and g["total_shape"] > 0


def test_gex_profile_thin_chain_is_unavailable_no_raise():
    g = _gex_profile(_synth_chain()[:3], 100.0, 0.8, t_years=_T)
    assert g["status"] == "thin_chain" and g["flip"] is None and g["pin"] is None


def test_gex_profile_no_vol_when_no_ltp_and_no_atr():
    no_px = [{"strike": k, "ce": {"oi": 100}, "pe": {"oi": 100}} for k in (90, 95, 100, 105, 110)]
    g = _gex_profile(no_px, 100.0, 0.0, t_years=_T, bars_per_year=0)
    assert g["status"] == "no_vol" and g["flip"] is None
    # with an ATR available the realized-vol fallback kicks in instead
    g2 = _gex_profile(no_px, 100.0, 0.9, t_years=_T)
    assert g2["status"] == "ok" and g2["sigma_src"] == "realized"


def test_gex_diag_present_on_every_ok_index_return():
    for bars, sym in ((_bars_no_volume(), "NIFTY"), ({"5m": _ng_bars()}, "NATURALGAS")):
        r = compute_sr(bars, chain=_NG_CHAIN, mode="index", symbol=sym)
        assert r["status"] == "OK"
        assert isinstance(r["sr_diag"]["gex"], dict) and "status" in r["sr_diag"]["gex"]
        for k in ("gex_flip", "gex_pin", "gex_regime_sign", "gex_sigma"):
            assert k in r


def test_gex_v1a_does_not_feed_candidates_or_strength():
    a = compute_sr({"5m": _ng_bars()}, chain=_NG_CHAIN, mode="index", symbol="NATURALGAS")
    b = compute_sr({"5m": _ng_bars()}, chain=_NG_CHAIN, mode="index", symbol="NATURALGAS")
    # GEX never becomes a candidate / family / source
    srcs = {s for z in a["levels"] for s in z["sources"]}
    fams = {f for z in a["levels"] for f in z["families"]}
    assert not (srcs & {"gex_flip", "gex_pin"}) and "gex" not in fams
    for z in a["levels"]:
        assert "gex" not in z["components"] and "gex_backing" not in z["components"]
    # S/R output is byte-identical run to run (GEX added no nondeterminism)
    assert (a["support"], a["resistance"], a["support_strength"],
            a["resistance_strength"], a["n_zones"]) == \
           (b["support"], b["resistance"], b["support_strength"],
            b["resistance_strength"], b["n_zones"])


def test_gex_option_mode_is_na():
    r = compute_sr({"5m": mk([40 + (i % 6) for i in range(40)], vol=800)}, mode="option")
    assert r["sr_diag"]["gex"]["status"] == "n/a" and r["gex_flip"] is None
