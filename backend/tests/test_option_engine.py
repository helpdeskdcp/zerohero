"""P4 CE/PE engines, option-quality selector, EV/RR gate."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.engines.option_engine import (analyse_leg, ce_pe_confirmation,
                                       ev_gate, select_option)


def bars(closes, wick=0.6, vol=5000):
    out, prev = [], closes[0]
    for i, c in enumerate(closes):
        o = prev
        out.append({"t": f"2026-08-27T{9 + (15 + i * 5) // 60:02d}:{(15 + i * 5) % 60:02d}:00",
                    "o": round(o, 2), "h": round(max(o, c) + wick, 2),
                    "l": round(min(o, c) - wick, 2), "c": round(c, 2), "v": vol})
        prev = c
    return out


def _leg(ltp, oi=400000, vd=25000, delta=0.5, theta=-18.0, iv=13.0, strike=24100, ot="CE"):
    return {"ltp": ltp, "oi": oi, "oi_chg": 0.0, "vol_delta": vd, "chg_pct": 6.0,
            "delta": delta, "gamma": 0.001, "theta": theta, "vega": 9.0, "iv": iv,
            "strike": strike, "token": f"T{strike}{ot}", "tradingsymbol": f"NIFTY{strike}{ot}",
            "expiry": "2026-09-01"}


def test_ce_leg_in_uptrend_confirms_strong():
    up = [120 + i * 1.5 for i in range(30)]
    a = analyse_leg({"5m": bars(up), "3m": bars(up)}, _leg(160, delta=0.52),
                    opt_type="CE", index_move_pts=25.0)
    assert a["opt_type"] == "CE" and a["own_trend"]["dir"] == "UP"
    assert a["confirm"] == "STRONG"
    assert a["translation_score"] > 0.4 and a["greeks_available"] is True
    assert 0 <= a["quality_score"] <= 100


def test_ce_leg_going_wrong_way_is_opposing():
    dn = [200 - i * 1.5 for i in range(30)]
    a = analyse_leg({"5m": bars(dn)}, _leg(160, delta=0.5), opt_type="CE", index_move_pts=25.0)
    assert a["confirm"] == "OPPOSING" and a["own_trend"]["dir"] == "DOWN"


def test_no_greeks_uses_fallback_translation():
    up = [100 + i * 2.0 for i in range(30)]
    leg = _leg(160)
    for g in ("delta", "gamma", "theta", "vega", "iv"):
        leg[g] = None
    a = analyse_leg({"5m": bars(up)}, leg, opt_type="CE", index_move_pts=30.0)
    assert a["greeks_available"] is False
    assert a["translation"]["method"] == "fallback"
    assert 0.0 <= a["translation_score"] <= 1.0


def test_illiquid_leg_penalised():
    up = [120 + i * 1.5 for i in range(30)]
    liquid = analyse_leg({"5m": bars(up)}, _leg(160, oi=800000, vd=60000), opt_type="CE", index_move_pts=25)
    thin = analyse_leg({"5m": bars(up)}, _leg(160, oi=2000, vd=200), opt_type="CE", index_move_pts=25)
    assert thin["liquidity_score"] < liquid["liquidity_score"]
    assert thin["quality_score"] < liquid["quality_score"]


def test_ce_pe_confirmation_confirmed_and_conflict():
    strong_ce = {"confirm": "STRONG"}
    weak_pe = {"confirm": "WEAK"}
    strong_pe = {"confirm": "STRONG"}
    assert ce_pe_confirmation("BULLISH", strong_ce, weak_pe)["agreement"] == "CONFIRMED"
    assert ce_pe_confirmation("BULLISH", strong_ce, strong_pe)["agreement"] == "CONFLICT"
    assert ce_pe_confirmation("BULLISH", {"confirm": "OPPOSING"}, weak_pe)["agreement"] == "OPPOSING"


def test_select_option_prefers_quality_and_atm():
    up = [120 + i * 1.5 for i in range(30)]
    b = {"5m": bars(up)}
    atm = analyse_leg(b, _leg(160, oi=900000, vd=70000, delta=0.5, strike=24100), opt_type="CE", index_move_pts=25)
    far = analyse_leg(b, _leg(35, oi=50000, vd=3000, delta=0.15, strike=24400), opt_type="CE", index_move_pts=25)
    deep = analyse_leg(b, _leg(430, oi=120000, vd=8000, delta=0.85, strike=23800), opt_type="CE", index_move_pts=25)
    pick = select_option([atm, far, deep], "BULLISH", atm=24100.0, config={"strike_step": 50})
    assert pick["strike"] == 24100
    assert pick["final_quality"] >= 40


def test_select_option_empty():
    assert select_option([], "BULLISH", atm=24100.0) is None


def test_ev_gate_passes_good_setup():
    g = ev_gate(0.60, entry=100.0, stop_loss=88.0, target_1=124.0)
    assert g["pass"] is True and g["rr"] == 2.0 and g["ev_r"] > 0.12


def test_ev_gate_rejects_thin_ev_despite_direction():
    # 55% prob but RR only 1.0 -> EV ~ 0.1R, below the 0.12R floor
    g = ev_gate(0.55, entry=100.0, stop_loss=90.0, target_1=110.0)
    assert g["pass"] is False and "EV" in g["reason"]


def test_ev_gate_rejects_low_rr():
    g = ev_gate(0.80, entry=100.0, stop_loss=95.0, target_1=104.0)   # RR 0.8
    assert g["pass"] is False and "RR" in g["reason"]


def test_ev_gate_uses_historical_stats_when_supplied():
    # historical avg loss much bigger than the nominal stop -> EV worse
    g = ev_gate(0.6, entry=100, stop_loss=90, target_1=115, avg_win=12.0, avg_loss=25.0)
    assert g["pass"] is False


def test_deterministic():
    up = [120 + i * 1.5 for i in range(30)]
    b = {"5m": bars(up)}
    assert analyse_leg(b, _leg(160), opt_type="CE", index_move_pts=25) == \
           analyse_leg(b, _leg(160), opt_type="CE", index_move_pts=25)
