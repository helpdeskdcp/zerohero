"""
app/optionchain/qualify.py -- Layer 5 Signal Qualification. Offline.

Independent-gate logic: one FAIL -> NO_TRADE; all PASS/NA -> QUALIFIED;
soft-in-between -> WATCH. Structure alone never reaches QUALIFIED (SIGNAL != ENTRY).
"""
import json

import pytest

from app.optionchain import bs
from app.optionchain.chain import OptionChain, StrikeRow, OptionLeg
from app.optionchain.structure import OptionStructureState, analyze
from app.optionchain import qualify as Q          # submodule (also re-exported on the package)


# --------------------------------------------------------------------------- #
#  a fully-populated, healthy structure state (override blocks per test)       #
# --------------------------------------------------------------------------- #
def _state(**over):
    base = dict(
        underlying="NIFTY", expiry="15SEP2026", ts="2026-09-09T06:00:00Z",
        spot=23450.0, atm_strike=23450.0, source="unit",
        quality={"dqs": 88.0, "verdict": "PASS"},
        capability={"structure_blocks_ok": 5, "structure_complete": True},
        max_pain={"status": "ok", "max_pain_strike": 23700.0, "spot": 23450.0,
                  "distance_pct": 1.07, "magnet_dir": "UP", "magnet_pull": "MODERATE"},
        pcr_regime={"status": "ok", "pcr_oi": 1.42, "regime": "BULLISH"},
        oi_walls={"status": "ok", "ce_wall": 24000.0, "pe_wall": 23000.0,
                  "nearest_resistance": 23900.0, "nearest_support": 23100.0,
                  "dist_res_pct": 1.9, "dist_sup_pct": -1.5,
                  "resistance_in_reach": False, "support_in_reach": False, "boxed": False},
        iv_skew_bias={"status": "ok", "bias": "CALL_CHASE", "rr_25": -0.02, "skew_slope": 0.2},
        iv_vs_realized={"status": "ok", "state": "FAIR", "ratio": 1.0},
        gex_regime={"status": "ok", "regime": "GAMMA_BALANCED", "regime_sign": 0,
                    "flip_strike": 23460.0, "pin_strike": 23500.0},
        notes=[], summary="")
    for k, v in over.items():
        base[k] = v
    return OptionStructureState(**base)


# --------------------------------------------------------------------------- #
#  structure_direction -- transparent vote                                     #
# --------------------------------------------------------------------------- #
def test_structure_direction_long_short_neutral():
    assert Q.structure_direction(_state()).get("bias") == "LONG"        # up-magnet + bullish PCR + call-chase
    bear = _state(max_pain={"status": "ok", "magnet_dir": "DOWN", "magnet_pull": "STRONG",
                            "distance_pct": -1.6, "max_pain_strike": 23050.0},
                  pcr_regime={"status": "ok", "pcr_oi": 0.6, "regime": "BEARISH"},
                  iv_skew_bias={"status": "ok", "bias": "PUT_FEAR", "rr_25": 0.03})
    assert Q.structure_direction(bear)["bias"] == "SHORT"
    flat = _state(max_pain={"status": "ok", "magnet_dir": "AT", "magnet_pull": "AT_MAGNET"},
                  pcr_regime={"status": "ok", "regime": "NEUTRAL"},
                  iv_skew_bias={"status": "ok", "bias": "NEUTRAL"})
    d = Q.structure_direction(flat)
    assert d["bias"] == "NEUTRAL" and d["net"] == 0 and d["votes"] == []


def test_structure_direction_weights_are_config():
    st = _state(max_pain={"status": "ok", "magnet_dir": "AT", "magnet_pull": "AT_MAGNET"},
                pcr_regime={"status": "ok", "regime": "NEUTRAL"})
    # only the skew cue fires (weight 0.5) -> below default thr 1.0 -> NEUTRAL
    assert Q.structure_direction(st)["bias"] == "NEUTRAL"
    # drop the threshold -> the lone skew vote now carries
    assert Q.structure_direction(st, {"bias_thr": 0.4})["bias"] == "LONG"


# --------------------------------------------------------------------------- #
#  verdict aggregation                                                         #
# --------------------------------------------------------------------------- #
def test_aligned_signal_plus_ann_qualifies():
    q = Q.qualify(_state(),
                  external_signal={"direction": "LONG", "source": "HCS",
                                   "calibrated_probability": 0.61},
                  ann_p_win=0.58)
    assert q.verdict == "QUALIFIED" and q.direction == "LONG"
    assert q.blocking == [] and q.watch_reasons == []
    assert {g["name"]: g["status"] for g in q.gates}["G_DIRECTION"] == "PASS"


def test_structure_only_never_qualifies():
    q = Q.qualify(_state(), ann_p_win=0.9)          # great ANN, no external trigger
    assert q.verdict == "WATCH"
    assert any(g["name"] == "G_DIRECTION" and g["status"] == "WATCH" for g in q.gates)
    assert "SIGNAL != ENTRY" in next(g["detail"] for g in q.gates if g["name"] == "G_DIRECTION")


def test_conflict_is_no_trade():
    q = Q.qualify(_state(),                                   # structure LONG
                  external_signal={"direction": "SHORT", "source": "RK"}, ann_p_win=0.7)
    assert q.verdict == "NO_TRADE" and q.blocking == ["G_DIRECTION"]
    assert q.direction == "SHORT"                             # candidate still reported


def test_dq_fail_blocks_everything():
    q = Q.qualify(_state(quality={"dqs": 20.0, "verdict": "FAIL"}),
                  external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.8)
    assert q.verdict == "NO_TRADE" and "G_DQ" in q.blocking


def test_dq_warn_caps_at_watch():
    q = Q.qualify(_state(quality={"dqs": 66.0, "verdict": "WARN"}),
                  external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.8)
    assert q.verdict == "WATCH" and any("G_DQ" in r for r in q.watch_reasons)


def test_ann_below_watch_band_is_no_trade():
    q = Q.qualify(_state(), external_signal={"direction": "LONG", "source": "HCS"},
                  ann_p_win=0.41)
    assert q.verdict == "NO_TRADE" and "G_ANN" in q.blocking


def test_ann_absent_is_NA_and_can_still_qualify():
    q = Q.qualify(_state(), external_signal={"direction": "LONG", "source": "HCS"})
    assert next(g["status"] for g in q.gates if g["name"] == "G_ANN") == "NA"
    assert q.verdict == "QUALIFIED"


def test_require_ann_config_makes_absence_a_watch():
    q = Q.qualify(_state(), external_signal={"direction": "LONG", "source": "HCS"},
                  cfg={"require_ann": True})
    assert q.verdict == "WATCH"
    assert next(g["status"] for g in q.gates if g["name"] == "G_ANN") == "WATCH"


def test_structure_incomplete_gates():
    watch = Q.qualify(_state(capability={"structure_blocks_ok": 3, "structure_complete": False}),
                      external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.7)
    assert next(g["status"] for g in watch.gates if g["name"] == "G_STRUCTURE") == "WATCH"
    assert watch.verdict == "WATCH"
    dead = Q.qualify(_state(capability={"structure_blocks_ok": 1, "structure_complete": False}),
                     external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.7)
    assert dead.verdict == "NO_TRADE" and "G_STRUCTURE" in dead.blocking


# --------------------------------------------------------------------------- #
#  location + regime gates                                                     #
# --------------------------------------------------------------------------- #
def test_long_into_the_call_wall_is_no_trade():
    st = _state(oi_walls={"status": "ok", "ce_wall": 23500.0, "pe_wall": 23000.0,
                          "nearest_resistance": 23500.0, "nearest_support": 23100.0,
                          "dist_res_pct": 0.2, "dist_sup_pct": -1.5,
                          "resistance_in_reach": True, "support_in_reach": False, "boxed": False})
    q = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.7)
    assert q.verdict == "NO_TRADE" and "G_LOCATION" in q.blocking


def test_boxed_book_is_watch():
    st = _state(oi_walls={"status": "ok", "ce_wall": 23600.0, "pe_wall": 23300.0,
                          "nearest_resistance": 23600.0, "nearest_support": 23300.0,
                          "dist_res_pct": 0.4, "dist_sup_pct": -0.4,
                          "resistance_in_reach": True, "support_in_reach": True, "boxed": True})
    q = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.7)
    assert q.verdict == "WATCH" and any("G_LOCATION" in r for r in q.watch_reasons)


def test_expiry_day_downgrades_a_directional_call_to_watch():
    st = _state(expiry_context={"phase": "EXPIRY_DAY", "dte": 0, "is_expiry_day": True,
                                "next_expiry": "22SEP2026"})
    q = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.7)
    ge = next(g for g in q.gates if g["name"] == "G_EXPIRY")
    assert ge["status"] == "WATCH" and "0 DTE" in ge["detail"]
    assert q.verdict == "WATCH"
    # disabling the gate lets it through
    q2 = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"},
                   ann_p_win=0.7, cfg={"use_expiry_gate": False})
    assert next(g for g in q2.gates if g["name"] == "G_EXPIRY")["status"] == "NA"


def test_expired_series_is_no_trade():
    st = _state(expiry_context={"phase": "EXPIRED", "dte": -1, "is_expiry_day": False})
    q = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.8)
    assert q.verdict == "NO_TRADE" and "G_EXPIRY" in q.blocking


def test_gex_pin_context_downgrades_directional_to_watch():
    st = _state(gex_regime={"status": "ok", "regime": "NET_LONG_GAMMA", "regime_sign": 1,
                            "flip_strike": 23455.0, "pin_strike": 23450.0},  # spot == pin
                max_pain={"status": "ok", "magnet_dir": "UP", "magnet_pull": "MODERATE",
                          "distance_pct": 1.1, "max_pain_strike": 23700.0})
    q = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.7)
    assert q.regime_context == "RANGE"
    assert next(g["status"] for g in q.gates if g["name"] == "G_REGIME") == "WATCH"
    assert q.verdict == "WATCH"
    # disabling the context lets it through
    q2 = Q.qualify(st, external_signal={"direction": "LONG", "source": "HCS"},
                   ann_p_win=0.7, cfg={"use_gex_context": False})
    assert next(g["status"] for g in q2.gates if g["name"] == "G_REGIME") == "NA"
    assert q2.verdict == "QUALIFIED"


# --------------------------------------------------------------------------- #
#  signal normaliser + serialisation + integration                            #
# --------------------------------------------------------------------------- #
def test_normalize_signal_variants():
    assert Q.normalize_signal({"side": "buy"})["direction"] == "LONG"
    assert Q.normalize_signal({"bias": "Bearish"})["direction"] == "SHORT"
    assert Q.normalize_signal({"direction": "flat"})["direction"] == "FLAT"
    n = Q.normalize_signal({"direction": "LONG", "hcs_score": 7.1, "calibrated_probability": 0.6})
    assert n["score"] == 7.1 and n["prob"] == 0.6
    assert Q.normalize_signal(None) is None


def test_to_dict_is_json_serialisable():
    q = Q.qualify(_state(), external_signal={"direction": "LONG", "source": "HCS"}, ann_p_win=0.6)
    json.dumps(q.to_dict())


def _mini_chain(spot=23450.0):
    T = bs.year_fraction("15SEP2026", __import__("datetime").datetime(
        2026, 9, 9, tzinfo=__import__("datetime").timezone.utc))
    rows = []
    for k in range(22800, 24101, 50):
        kk = float(k)
        cg = bs.greeks(spot, kk, T, 0.12, True)
        pg = bs.greeks(spot, kk, T, 0.12, False)
        rows.append(StrikeRow(
            strike=kk,
            ce=OptionLeg(ltp=bs.price(spot, kk, T, 0.12, True), oi=6000.0 + max(0.0, kk - spot) * 3,
                         oi_change=100.0, volume=9000.0, iv=0.12,
                         delta=cg["delta"], gamma=cg["gamma"], theta=cg["theta"], vega=cg["vega"]),
            pe=OptionLeg(ltp=bs.price(spot, kk, T, 0.12, False), oi=6000.0 + max(0.0, spot - kk) * 3,
                         oi_change=100.0, volume=9000.0, iv=0.12,
                         delta=pg["delta"], gamma=pg["gamma"], theta=pg["theta"], vega=pg["vega"])))
    return OptionChain(underlying="NIFTY", expiry="15SEP2026", ts="2026-09-09T06:00:00Z",
                       source="unit", spot=spot, rows=rows,
                       capability={"has_greeks": True, "has_iv": True, "has_oi": True,
                                   "greek_coverage": 1.0},
                       quality={"dqs": 85.0, "verdict": "PASS"}).sort().compute_atm()


def test_qualify_from_chain_end_to_end():
    q = Q.qualify_from_chain(_mini_chain(), realized_vol=0.11,
                             external_signal={"direction": "LONG", "source": "HCS"},
                             ann_p_win=0.6)
    assert q.verdict in ("QUALIFIED", "WATCH", "NO_TRADE")
    assert len(q.gates) == 7 and q.structure_bias["bias"] in ("LONG", "SHORT", "NEUTRAL")
    assert {g["name"] for g in q.gates} >= {"G_DQ", "G_STRUCTURE", "G_DIRECTION",
                                            "G_ANN", "G_LOCATION", "G_REGIME", "G_EXPIRY"}
    assert "research-only" in " ".join(q.notes)
