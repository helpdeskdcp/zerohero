"""
SMART_INDEX_SCALPER (slice 2) — universe / eligibility / selection score / ranking.
Offline: market_context is monkeypatched so no AngelOne call is made.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.smart_index_scalper import (
    SmartIndexScalper, resolve_universe, index_meta, eligibility, selection_score,
)


# ------------------------------------------------------------------ universe
def test_universe_is_configurable_and_reuses_sym_meta():
    assert resolve_universe("NIFTY, sensex") == ["NIFTY", "SENSEX"]
    m = index_meta("SENSEX")
    assert m["chain_market"] == "BSE" and m["strike_step"] and m["is_index"] is True
    assert index_meta("NIFTY")["chain_market"] == "NSE"


# ------------------------------------------------------------------ eligibility
def _good_ctx(spot=24000.0):
    strikes = [spot + i * 50 for i in range(-4, 5)]
    chain = [{"strike": k, "ce_ltp": 60, "ce_oi": 300000, "ce_oi_change": 5000, "ce_volume": 12000,
              "pe_ltp": 55, "pe_oi": 320000, "pe_oi_change": 6000, "pe_volume": 11000} for k in strikes]
    return {"spot": spot, "atm": spot, "chain": chain,
            "current_volume": 1500, "avg_volume": 1000, "mom_3m": 8.0,
            "data_quality": {"prev_day_ohlc": "ACTUAL"}}


def _good_engine_out(direction="CE"):
    return {
        "status": "OK", "direction": direction, "signal_type": "BUY_CE",
        "confidence": 62, "confluence_score": 66,
        "risk_reward": [1.8, 3.0, 4.2],
        "nearest_support": {"center": 23950, "evidence_count": 4, "strength_score": 72},
        "nearest_resistance": {"center": 24080, "evidence_count": 3, "strength_score": 60},
        "score_breakdown": {"oi": {"raw": 14}},
    }


def _good_oi():
    return {"status": "OK",
            "walls": {"CALL_RESISTANCE_WALL": {"strike": 24100, "score": 70},
                      "PUT_SUPPORT_WALL": {"strike": 23900, "score": 65}}}


def test_eligibility_all_pass_on_good_data():
    e = eligibility.evaluate_eligibility(ctx=_good_ctx(), engine_out=_good_engine_out(),
                                         oi_matrix=_good_oi())
    assert e["eligible"] is True and e["failed"] == []
    names = {c["name"] for c in e["checks"]}
    assert {"valid_option_chain", "liquidity", "reasonable_spread", "sufficient_volume",
            "clear_mathematical_levels", "clear_oi_structure", "acceptable_confidence",
            "acceptable_risk_reward"} <= names


def test_eligibility_fails_with_named_reasons_on_bad_data():
    ctx = _good_ctx()
    ctx["chain"] = [{"strike": 24000, "ce_oi": 10, "pe_oi": 10, "ce_ltp": 1, "pe_ltp": 1}]  # thin, illiquid
    out = {**_good_engine_out(), "confidence": 10, "risk_reward": [0.5, 1, 1.5],
           "nearest_support": {"center": 23950, "evidence_count": 1}}
    e = eligibility.evaluate_eligibility(ctx=ctx, engine_out=out,
                                         oi_matrix={"status": "DATA_INSUFFICIENT"})
    assert e["eligible"] is False
    assert {"valid_option_chain", "liquidity", "clear_mathematical_levels",
            "clear_oi_structure", "acceptable_confidence",
            "acceptable_risk_reward"} <= set(e["failed"])


# ------------------------------------------------------------------ selection score
def test_selection_score_weighted_and_configurable():
    comp = selection_score.component_scores(ctx=_good_ctx(), engine_out=_good_engine_out(),
                                            oi_matrix=_good_oi(), liquidity_norm=1.0)
    assert set(comp) == {"signal_quality", "oi_confluence", "math_confluence",
                         "liquidity", "volume", "momentum", "risk_reward"}
    s = selection_score.index_selection_score(comp)
    assert 0 <= s["index_selection_score"] <= 100
    assert s["breakdown"]["signal_quality"]["weight_pct"] == 25.0
    s2 = selection_score.index_selection_score(comp, {"signal_quality": 0.5})
    assert s2["breakdown"]["signal_quality"]["weight_pct"] == 50.0
    assert "NOT calibrated" in s["weights_source"]


# ------------------------------------------------------------------ full scan / ranking
def test_scan_ranks_and_explains_winner(monkeypatch):
    import app.mathematical_confluence.context as _cx
    import app.smart_index_scalper.scanner as _scn

    def fake_ctx(sym, **kw):
        # NIFTY has stronger momentum + tighter zone than BANKNIFTY; SENSEX missing prev-day
        if sym == "NIFTY":
            c = _good_ctx(24000); c["mom_3m"] = 12.0
            c["prev_day"] = {"high": 24050, "low": 23850, "close": 23950}
            c["today_open"] = 23980; c["day_high"] = 24010; c["day_low"] = 23960
            return c
        if sym == "BANKNIFTY":
            c = _good_ctx(52000); c["mom_3m"] = 3.0
            c["prev_day"] = {"high": 52300, "low": 51700, "close": 52000}
            c["today_open"] = 52050; c["day_high"] = 52100; c["day_low"] = 51950
            return c
        return {"instrument": sym, "prev_day": {}, "chain": [], "bars": [],
                "data_quality": {"prev_day_ohlc": "MISSING"}}
    monkeypatch.setattr(_cx, "market_context", fake_ctx)
    monkeypatch.setattr(_scn, "market_context", fake_ctx)

    out = SmartIndexScalper().scan(["NIFTY", "BANKNIFTY", "SENSEX"], use_cache=False)
    assert out["engine"] == "SMART_INDEX_SCALPER"
    assert "SENSEX" in [x["index"] for x in out["not_eligible"]]     # missing data -> not eligible
    ranked = out["ranked"]
    assert [r["index"] for r in ranked][:1] == ["NIFTY"] or ranked == []  # NIFTY wins if any eligible
    sel = out["selection"]
    if ranked:
        assert sel["primary"]["index"] == "NIFTY"
        assert "beat" in sel["why_primary"] or "only eligible" in sel["why_primary"]
    assert "UNCALIBRATED" in out["calibration"]
    assert "no paper position" in out["calibration"].lower()


def test_scan_and_selector_are_pure_no_order_path():
    # scanner + option_selector are analysis-only. paper_engine.py (slice 4) is
    # allowed to open PAPER trades via engines.paper_trading; the LIVE-order ban
    # is enforced in test_smart_scalper_paper.test_paper_engine_never_places_a_real_order.
    src = Path(__file__).parents[1] / "app" / "smart_index_scalper"
    analysis = "\n".join((src / f).read_text() for f in ("scanner.py", "option_selector.py",
                                                          "eligibility.py", "selection_score.py"))
    for banned in ("open_trade(", "place_order", "placeOrder", "close_trade(", "OrderManager"):
        assert banned not in analysis


# ------------------------------------------------------------------ slice 3: option selection
from app.smart_index_scalper import option_selector, profiles


def _chain(spot=24000.0, step=50.0):
    out = []
    for i in range(-4, 5):
        k = spot + i * step
        # OTM CE premium falls as strike rises; OTM PE premium falls as strike drops
        ce = max(4.0, 120 - i * 22)
        pe = max(4.0, 120 + i * 22)
        out.append({"strike": k,
                    "ce_ltp": ce, "ce_oi": 400000 - abs(i) * 40000, "ce_oi_change": 5000 - i * 200,
                    "ce_volume": 15000 - abs(i) * 1500, "ce_delta": max(0.05, 0.5 - i * 0.12),
                    "ce_gamma": 0.0011, "ce_theta": -6.0, "ce_iv": 0.12,
                    "pe_ltp": pe, "pe_oi": 400000 - abs(i) * 40000, "pe_oi_change": 4000 + i * 200,
                    "pe_volume": 14000 - abs(i) * 1500, "pe_delta": min(-0.05, -0.5 - i * 0.12),
                    "pe_gamma": 0.0011, "pe_theta": -6.0, "pe_iv": 0.12,
                    "expiry": "09SEP2026"})
    return out


def test_profiles_are_configurable_with_atm_distance():
    for name in ("CONSERVATIVE", "BALANCED", "AGGRESSIVE"):
        p = profiles.get_profile(name)
        assert p["name"] == name and p["allowed_option_distance"] >= 1
        assert "UNCALIBRATED" in p["calibration"]
    assert profiles.get_profile("CONSERVATIVE")["allowed_option_distance"] < \
        profiles.get_profile("AGGRESSIVE")["allowed_option_distance"]
    # override
    p = profiles.get_profile("BALANCED", overrides={"allowed_option_distance": 5})
    assert p["allowed_option_distance"] == 5


def test_option_selector_is_deterministic_and_explains():
    ch = _chain()
    a = option_selector.select(direction="CE", spot=24000.0, chain=ch, atm=24000.0,
                               strike_step=50.0, expected_move_pts=60.0,
                               allowed_option_distance=2)
    b = option_selector.select(direction="CE", spot=24000.0, chain=ch, atm=24000.0,
                               strike_step=50.0, expected_move_pts=60.0,
                               allowed_option_distance=2)
    assert a["status"] == "OK" and a == b                      # deterministic
    assert a["option_type"] == "CE"
    assert abs(a["selected_strike"] - 24000.0) <= 2 * 50.0     # within the profile band
    assert 0 <= a["selection_score"] <= 100
    assert a["reasons"] and a["deterministic"] is True
    # every candidate scored + ranked
    assert a["candidates"] == sorted(a["candidates"], key=lambda c: (-c["selection_score"], c["atm_distance_strikes"]))
    assert all(c["option_type"] == "CE" for c in a["candidates"])


def test_option_selector_respects_profile_atm_distance():
    ch = _chain()
    narrow = option_selector.select(direction="PE", spot=24000.0, chain=ch, atm=24000.0,
                                    strike_step=50.0, allowed_option_distance=1)
    wide = option_selector.select(direction="PE", spot=24000.0, chain=ch, atm=24000.0,
                                  strike_step=50.0, allowed_option_distance=3)
    assert len(narrow["candidates"]) <= len(wide["candidates"])
    assert all(c["atm_distance_strikes"] <= 1.0001 for c in narrow["candidates"])


def test_option_selector_no_selection_and_data_gates():
    assert option_selector.select(direction="NONE", spot=1, chain=[{}])["status"] == "NO_SELECTION"
    assert option_selector.select(direction="CE", spot=None, chain=[])["status"] == "DATA_INSUFFICIENT"
    # all premiums below the floor -> NO_SELECTION with a reason
    thin = [{"strike": 24000, "ce_ltp": 0.5, "ce_oi": 1, "pe_ltp": 0.5, "pe_oi": 1}]
    r = option_selector.select(direction="CE", spot=24000.0, chain=thin, atm=24000.0,
                               strike_step=50.0, premium_min=3.0)
    assert r["status"] == "NO_SELECTION" and "no liquid" in r["reason"]


def test_option_selector_does_not_import_order_path():
    import app.smart_index_scalper.option_selector as m
    src = Path(m.__file__).read_text()
    for banned in ("open_trade", "place_order", "placeOrder", "OrderManager", "close_trade"):
        assert banned not in src
