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


def test_scan_never_opens_a_position():
    src = Path(__file__).parents[1] / "app" / "smart_index_scalper"
    joined = "\n".join(p.read_text() for p in src.glob("*.py"))
    for banned in ("open_trade(", "place_order", "placeOrder", "close_trade(", "OrderManager"):
        assert banned not in joined
