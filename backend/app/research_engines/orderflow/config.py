"""Every tunable for ORDERFLOW_ENGINE v1. One place. Defaults are neutral;
anything marked CALIBRATED is a starting value that backtest.calibrate()
overwrites from the TRAIN split only."""
from __future__ import annotations
import copy

DEFAULT_CONFIG: dict = {
    "tf_min": 5,
    "eps": 1e-9,
    "warmup_bars": 60,                 # EMA/ADX/pivots/profile settled
    "session_start_ist": "09:15",
    "session_end_ist": "15:15",        # entry-timing hard flat

    # ---- indicators ----
    "ema_fast": 20,                    # "SSL / fast trend" proxy on this engine
    "ema_slow": 60,                    # "60 baseline"
    "ema_trend": 200,
    "atr_period": 14,
    "adx_period": 14,
    "rsi_period": 14,
    "pivot_halfwidth": 3,             # fractal swing pivot
    "vwap_method": "PRICE_PROXY_EQUAL_WEIGHT",   # cash index has no volume

    # ---- order-flow proxy ----
    "of_lookback": 10,               # bars for cum-pressure slope / persistence
    "of_divergence_pivots": 2,       # consecutive pivots for proxy divergence

    # ---- RK score (0-100) : weights sum to 100, capability-aware renorm ----
    "rk_weights": {
        "flow_dir": 20, "flow_persist": 12, "flow_vs_price": 12,
        "structure": 18, "regime_fit": 12, "vwap_ctx": 10,
        "value_area": 8, "reclaim": 8,
    },
    "rk_min": 60.0,                   # below this -> no signal
    "rk_dir_min_abs": 0.15,          # |directional tilt| below this -> NONE

    # ---- regime ----
    "adx_trend_min": 22.0,
    "adx_range_max": 18.0,
    "atr_pct_hi_pctl": 0.70,
    "atr_pct_lo_pctl": 0.30,
    "eff_ratio_lookback": 10,

    # ---- ENTRY TIMING ENGINE ----
    "entry_max_wait_bars": 18,       # SIGNAL_DETECTED -> must confirm within (5m: 90 min)
    "entry_lookback_bars": 20,       # fallback leg length when no confirmed pivot
    "entry_zones": [0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60],   # retracement grid (frac of the LEG)
    "entry_zone_frac": 0.30,        # CALIBRATED : chosen pullback depth (frac of the signal LEG)
    "entry_zone_tol": 0.08,        # +/- band around the zone, frac of the leg
    "sl_min_atr": 0.4,            # minimum stop distance from the fill, in ATR
    "entry_require_momentum_recovery": True,
    "entry_recovery_window": 3,      # bars after zone-tag to see a recovery bar (close in dir, reclaims zone)
    "momentum_recovery_rule": "close_reclaims_half",   # bar closes back past 50% of its range in trade dir
    "min_rr": 1.3,               # reject entries below this RR to T1
    "max_entry_extension_atr": 1.5, # NO-CHASE: signal-origin distance cap at fill
    "nochase_vwap_atr": 3.5,      # NO-CHASE: |price-vwap_proxy|/atr cap
    "nochase_baseline_atr": 3.0,   # NO-CHASE: |price-ema_slow|/atr cap
    "nochase_fast_atr": 2.0,       # NO-CHASE: |price-ema_fast|/atr cap
    "invalidate_on_origin_break": True,   # price through signal_low(long)/high(short) -> EXPIRED

    # ---- risk / targets ----
    "sl_buffer_atr": 0.10,
    "max_risk_atr": 1.5,           # cap the stop distance (deep-pullback guard)
    "target_r_multiples": [1.0, 2.0, 3.0],
    "scale_out": [[1.0, 0.3333], [2.0, 0.3333]],   # (R, fraction); last third -> T3
    "move_sl_to_be_after_r": 1.0,
    "runner_cap_r": 3.0,
    "cost_points": 0.0,

    # ---- ENTRY QUALITY SCORE (0-100) : weights sum to 100 ----
    "eq_weights": {
        "signal_quality": 20,      # RK score at detection
        "retest_quality": 20,      # how cleanly price reached the zone (overshoot/whipsaw)
        "momentum_recovery": 14,
        "orderflow_confirm": 12,   # proxy flow flipped/aligned into the zone
        "structure": 12,
        "vwap_location": 8,
        "volatility_fit": 6,       # ATR regime suits the family
        "rr": 8,
    },
    "eq_min": 45.0,               # below this -> WAIT even if zone touched

    # ---- calibration split (by session blocks, chronological) ----
    "train_frac": 0.55,
    "val_frac": 0.20,             # OOS = remaining 0.25
    "wf_folds": 6,
    "min_trades_per_zone": 30,    # a zone needs this many TRAIN trades to be eligible
    "min_trades_for_verdict": 40, # OOS
    "verdict_min_pf": 1.3,
    "verdict_min_pos_years": 2,
}


def merged(overrides: dict | None = None) -> dict:
    c = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c
