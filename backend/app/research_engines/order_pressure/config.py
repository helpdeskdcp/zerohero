"""Every tunable constant for the order-pressure engine. One place. Overridable
by passing a dict into the public entry points (backtest.run(config=...))."""
from __future__ import annotations

import copy

DEFAULT_CONFIG: dict = {
    # ---- candle math ----------------------------------------------------------
    "eps": 1e-9,                       # zero-range / zero-div guard
    "zero_range_score": 50.0,         # neutral pressure for a doji with range<=eps

    # ---- pressure blend weights (sum ~1 within buyer / seller) --------------
    "pressure_weights": {
        "body": 0.45,                 # directional body / range
        "wick_rejection": 0.35,      # lower-wick (buyer) or upper-wick (seller) / range
        "close_location": 0.20,      # CloseLocation (buyer) or 1-CloseLocation (seller)
    },

    # ---- lookback windows + recency weighting -------------------------------
    "lookbacks": [1, 3, 5, 10, 15],   # completed candles
    "recency_halflife": 3.0,         # candles; weight_i = 0.5 ** (age_i / halflife)

    # ---- series dynamics ---------------------------------------------------
    "slope_window": 3,               # bars used for the pressure slope (linear fit)
    "accel_window": 3,               # bars used for the 2nd difference
    "persistence_sign_frac": 0.6,   # >= this fraction of same-sign net-pressure -> "persistent"
    "divergence_min_price_move": 0.15,  # in ATR units; below this, no divergence call

    # ---- multi-TF fusion -------------------------------------------------
    "tf_set": ["1m", "3m", "5m"],
    "tf_align_tol": 15.0,            # |net_pressure_a - net_pressure_b| <= tol -> aligned

    # ---- OI / price state --------------------------------------------------
    "oi_change_min_pct": 0.5,        # |oi_change / oi| * 100 >= this -> OI move is "real"
    "price_move_min_atr": 0.10,     # |close-open| / atr >= this -> price move is "real"

    # ---- next-candle label ---------------------------------------------
    # INSIDE  = next bar fully contained: next.high <= cur.high AND next.low >= cur.low
    # else UP if next.close - cur.close >=  dir_min_atr * atr
    #      DOWN if next.close - cur.close <= -dir_min_atr * atr
    #      otherwise -> INSIDE (small, non-committal move)
    "label_dir_min_atr": 0.05,
    "atr_window": 14,

    # ---- model ----------------------------------------------------------
    "mlp_hidden": 6,                 # single hidden layer, small on purpose
    "mlp_l2": 5e-3,
    "mlp_lr": 0.05,
    "mlp_epochs": 40,
    "mlp_patience": 6,              # early-stop on validation logloss
    "mlp_seed_scale": 0.1,         # deterministic weight init magnitude
    "min_rows_for_fit": 400,       # below this -> INSUFFICIENT_SAMPLE, no model
    "min_rows_per_class": 30,

    # ---- walk-forward -------------------------------------------------
    "wf_scheme": "expanding",       # expanding-window by chronological block
    "wf_min_train_blocks": 4,
    "wf_val_frac": 0.15,           # tail of each train slice, chronological, for early-stop
    "wf_block_sessions": 15,       # one block = this many consecutive IST sessions
    "wf_max_folds": 14,           # test only the most recent N blocks (pure-Python runtime)
    "wf_embargo_bars": 2,        # purge gap each side of the train/test cut

    # curated low-variance SPOT feature set for the linear / MLP models (the GB
    # ensemble is fed the full feature_names() list -- it is robust to width).
    "spot_core_features": [
        "spot_buyer", "spot_seller", "spot_net", "spot_wnet", "spot_slope",
        "spot_persistence", "spot_reversal", "spot_divergence", "ret_1",
        "close_loc", "body_ratio", "spot_mtf_confirm",
        "spot_net_lb1", "spot_net_lb3", "spot_net_lb5", "spot_net_lb10", "spot_net_lb15",
        "spot_slope_lb3", "spot_slope_lb5", "spot_accel_lb3", "spot_accel_lb5",
        "mtf_aligned", "mtf_align_dir", "mtf_conflict", "mtf_spread",
    ],

    # ---- dynamic trade management ------------------------------------
    "tm_sl_atr": 1.1,              # initial SL distance in ATR (fixed across variants)
    "tm_t1_atr": 1.7,
    "tm_max_hold_bars": 20,
    "tm_threat_hi": 0.60,         # SLThreatProbability >= this  -> "HIGH threat"
    "tm_recovery_hi": 0.55,      # RecoveryProbability   >= this  -> "HIGH recovery"
    "tm_trail_confirm_bars": 2,  # bars of confirmed favourable pressure before trailing
    "tm_trail_atr": 0.9,        # trail distance in ATR (only ever tightened, never widened)
    "tm_profit_lock_atr": 0.8,  # once MFE >= this, momentum-weakening -> early exit / trail
}


def merged(overrides: dict | None = None) -> dict:
    c = copy.deepcopy(DEFAULT_CONFIG)
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(c.get(k), dict):
                c[k].update(v)
            else:
                c[k] = v
    return c
