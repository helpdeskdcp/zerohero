"""
EXPIRY ZERO TO HERO — unit tests. Pure/offline (no AngelOne network).
Research engine: assert it never fabricates data and never emits a live ENTRY
while UNCALIBRATED.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.expiry_zero_to_hero import bs
from app.expiry_zero_to_hero.support_detector import PremiumSupportDetector
from app.expiry_zero_to_hero.labeler import ZeroToHeroLabeler
from app.expiry_zero_to_hero.features import ExpiryFeatureEngine
from app.expiry_zero_to_hero.probability import ZeroToHeroProbabilityEngine
from app.expiry_zero_to_hero.signal import ZeroToHeroSignalEngine, ExpiryZeroToHeroReporter
from app.expiry_zero_to_hero.backtester import ExpiryZeroToHeroBacktester


# ---------------------------------------------------------------- bs.py
def test_bs_greeks_put_delta_negative_and_gamma_positive():
    g = bs.greeks(spot=76600, strike=76500, minutes_to_expiry=40, sigma=0.40, is_call=False)
    assert -1.0 < g["delta"] < 0.0
    assert g["gamma"] > 0
    assert g["theta_per_min"] < 0
    assert g["vega_per_volpt"] > 0


def test_bs_greeks_none_when_inputs_missing():
    g = bs.greeks(spot=None, strike=76500, minutes_to_expiry=40, sigma=0.4, is_call=False)
    assert all(v is None for v in g.values())
    assert bs.implied_vol(76600, 76500, 40, None, False) is None


def test_decompose_move_recovers_the_sensex_case_within_tolerance():
    # entry 64.75 @ SENSEX 76599, settle 347.15 @ ~76153, PE 76500, ~49 min
    d = bs.decompose_move(S0=76599.4, S1=76152.86, K=76500.0, is_call=False,
                          mins0=40.0, mins1=-8.0, prem0=64.75, prem1=347.15,
                          sigma0=0.40, d_iv=0.0)
    assert d["dP_observed"] == 282.4 and d["dS"] < 0
    assert d["delta_term"] > 0 and d["gamma_term"] > 0 and d["theta_term"] < 0
    # BS terms explain the bulk of the move; a positive residual (IV + settlement pull) remains
    assert 150 < d["bs_sum"] < 320
    assert 0 < d["residual_vs_observed"] < 120
    assert d["source"] == "MODEL:BS"


# ---------------------------------------------------------------- support detector
def test_support_detector_finds_repeated_level_without_hardcoding():
    # premium tests ~60 three times (60, 59.4, 61) with rebounds, tightening
    closes = [(0, 81), (1, 70), (2, 60.5), (3, 74), (4, 82), (5, 71),
              (6, 65), (7, 59.4), (8, 78), (9, 90), (10, 72), (11, 63),
              (12, 61.0), (13, 85), (14, 110), (15, 160)]
    v = PremiumSupportDetector(min_gap_min=1).detect(closes)
    assert v["verdict"] in ("STRONG", "MODERATE")
    assert v["number_of_tests"] >= 2
    assert 55 <= v["support_level"] <= 65        # discovered, not passed in
    assert v["premium_compression"] < 0.2


def test_support_detector_null_on_no_pattern():
    closes = [(i, 50 + i) for i in range(12)]      # monotone up, no support test
    v = PremiumSupportDetector().detect(closes)
    assert v["verdict"] == "NONE" and v["number_of_tests"] == 0


# ---------------------------------------------------------------- labeler
def test_labeler_multiple_definitions_and_no_lookahead_leak():
    closes = [60, 62, 58, 61, 59, 63, 120, 300, 340]   # explodes near the end
    L = ZeroToHeroLabeler().label_series(closes)
    pos = L["positives_per_definition"]
    assert pos["A_2x"] >= pos["B_3x"] >= pos["C_5x"]     # stricter => fewer
    # an entry at the LAST minute has no forward -> no label
    assert L["rows"][-1]["forward"] is None
    r0 = L["rows"][0]
    assert r0["forward"]["mfe_mult"] >= 5.0 and r0["forward"]["settlement_mult"] > 4


# ---------------------------------------------------------------- features (causal)
def test_features_are_causal_and_tag_model_greeks():
    opt = [{"minute": f"15:{n:02d}", "ltp_c": 60 + n, "strike": 76500, "mins_to_expiry": 30 - n,
            "intrinsic": 0.0, "time_value": 60 + n, "iv": 0.4, "delta": -0.4, "gamma": 1.7e-3,
            "theta_per_min": -1.2, "vega_per_volpt": 5.0} for n in range(12)]
    idx = [{"minute": f"15:{n:02d}", "spot_c": 76600 - 3 * n} for n in range(12)]
    out = ExpiryFeatureEngine().build(opt, idx)
    f5 = out[5]["features"]
    # 3-min premium return at minute 5 uses minutes 2..5 only
    assert f5["prem_ret_3m"] == round((65 - 62), 2)
    assert out[0]["features"]["prem_ret_10m"] is None      # not enough history yet
    assert out[-1]["delta"] == -0.4                        # model greek carried, not recomputed as broker


# ---------------------------------------------------------------- probability + signal
def test_probability_is_uncalibrated_and_signal_never_enters():
    prob = ZeroToHeroProbabilityEngine().score(
        side="PE",
        feats={"prem_compression": 0.06, "spot_momentum": -0.4, "gamma_accel_potential": 60,
               "mins_to_expiry": 20, "atm_distance_pts": 80},
        support={"strength": 100, "number_of_tests": 3},
        oi_imbalance_pct=None, recent_spot_range=400)
    assert prob["calibration_status"] == "UNCALIBRATED"
    assert prob["oi_factor"].startswith("UNAVAILABLE")
    assert 0 <= prob["probability_pct"] <= 100

    sig = ZeroToHeroSignalEngine().evaluate(
        index="SENSEX", expiry="10SEP2026", minute="15:14", side="PE", strike=76500,
        feats={"_prem_now": 63, "prem_range_10m": 20, "gamma_accel_potential": 60,
               "mins_to_expiry": 16},
        support={"verdict": "STRONG", "number_of_tests": 3},
        prob={**prob, "contributions": {"spot_align": 0.9}},
        spot_now=76540, mins_to_expiry=16, expected_spot_move_pts=500, iv_now=0.45)
    assert sig["status"] in ("WATCH", "NO_TRADE")          # NEVER ENTRY while uncalibrated
    assert sig["calibration_status"] == "UNCALIBRATED"
    assert "NOT for live trading" in sig["disclaimer"]
    assert isinstance(ExpiryZeroToHeroReporter().render(sig), str)


def test_backtester_refuses_metrics_on_one_day():
    win = {"index_bars": [{"minute": f"15:{n:02d}", "spot_c": 76600 - 5 * n} for n in range(20)],
           "option_bars": [{"minute": f"15:{n:02d}", "ltp_c": 60 + 12 * n if n > 12 else 60,
                            "strike": 76500, "side": "PE", "mins_to_expiry": 30 - n} for n in range(20)]}
    r = ExpiryZeroToHeroBacktester().run([win])
    assert r["status"] == "INSUFFICIENT_SAMPLE"
    assert r["expiry_days"] == 1 and r["min_expiry_days"] >= 8
    assert "precision" not in r
