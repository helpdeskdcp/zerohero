"""All tunables for TRI-COMPARE. One place. Every engine reads the SAME shared
harness block (data / cost / session / risk / splits); engine-specific blocks
only change WHICH bar and WHICH direction to enter."""
from __future__ import annotations
import copy

DEFAULT_CONFIG: dict = {
    # ================= SHARED HARNESS (identical for all 3 engines) =========
    "symbol": "NIFTY",
    "tf_min": 15,                      # best clean deep history in the project (1m Kaggle -> 15m)
    "start": "2016-01-01",
    "end": "2025-12-31",
    "warmup_bars": 80,

    # transaction cost + slippage, per round trip, in index points
    "cost_points": 1.0,               # brokerage+taxes proxy on the index leg
    "slippage_points": 1.0,           # 1 tick-ish each side folded into entry/exit

    # session rules
    "session_start_ist": "09:15",
    "no_new_entry_after_ist": "14:45",
    "hard_exit_ist": "15:15",

    # ONE risk model for everybody: SL = entry -/+ ATR*mult ; targets = R multiples
    "sl_atr_mult": 1.5,
    "target_r_multiples": [1.0, 2.0, 3.0],
    "scale_out": [[1.0, 0.3333], [2.0, 0.3333]],   # (R, fraction) ; last third -> T3 / trail
    "move_sl_to_be_after_r": 1.0,
    "trail_after_r": 1.0,             # then trail 1R behind the high-water R
    "runner_cap_r": 3.0,
    "min_rr": 1.3,                    # mathematical RR gate (reward-to-T1 / risk)
    "risk_per_trade_pts": 20.0,       # vol-normalized sizing anchor (for points<->R display only)
    "one_position_at_a_time": True,
    "cooldown_bars": 3,              # after a signal, ignore same-direction re-fires for N bars

    # chronological split (by session block) + walk-forward
    "train_frac": 0.55,
    "val_frac": 0.20,               # OOS = remaining 0.25
    "wf_folds": 6,
    "min_trades_for_verdict": 40,   # OOS
    "verdict_min_pf": 1.30,
    "verdict_min_pos_regimes": 2,
    "verdict_min_pos_years": 2,
    "verdict_max_degradation": 0.60,  # |TRAIN_expR - OOS_expR| / |TRAIN_expR|

    # ================= ENGINE 1 : CLAUDE TREND =============================
    "e1": {
        "ema_fast": 20, "ema_slow": 60, "ema_trend": 200,
        "ema_trend_slope_lookback": 10,
        "donchian_n": 40,             # breakout channel
        "donchian_buffer_atr": 0.05,
        "atr_pct_lo_pctl": 0.20,      # vol-regime filter: skip dead-flat
        "atr_pct_hi_pctl": 0.92,      # and skip blow-off
        "min_trend_persist_bars": 4,  # anti-whipsaw: trend state held this long
        "eff_ratio_lookback": 10,
        "eff_ratio_min": 0.30,        # Kaufman efficiency ratio (trendiness)
        "adx_min": 18.0,
        "entry_on": "breakout_close", # enter at the breakout bar close
    },

    # ================= ENGINE 2 : PRECISION STRUCTURE (ChatGPT) ============
    "e2": {
        "signal_states": ["BOS_UP", "CHOCH_UP", "BOS_DN", "CHOCH_DN"],
        "allow_reclaim_signal": True,
        "location_atr": 1.5,          # signal bar within this many ATR of a validated S/R
        "rsi_long_min": 50.0, "rsi_short_max": 50.0,
        "adx_min": 18.0,
        "require_di_align": True,
        "candle_body_min_frac": 0.40, # confirmation candle strength
        "hma_len": 55,
        # two-stage entry (reuses orderflow.entry_timing semantics)
        "entry_zone_frac": 0.35,      # pullback depth as frac of the signal leg (a-priori moderate)
        "entry_zone_tol": 0.10,
        "entry_max_wait_bars": 16,
        "entry_recovery_window": 4,
        "entry_lookback_bars": 20,
        "max_entry_extension_atr": 1.5,   # no-chase vs the signal-bar close
        "nochase_vwap_atr": 4.0,
        "nochase_baseline_atr": 4.0,
        "invalidate_on_origin_break": True,
        "require_momentum_recovery": True,
        "eq_min": 45.0,              # entry-quality gate (0-100)
    },

    # ================= ENGINE 3 : HYBRID =================================
    "e3": {
        "trend_gate": "not_counter",  # 'align' (E1 dir must match, strict) | 'not_counter' (E1 must not veto)
        "trend_gate_min_adx": 20.0,
        "trend_gate_require_eff_ratio": True,
        "trend_gate_eff_ratio_min": 0.30,
        "use_e2_pipeline": True,      # location + two-stage timing from Engine 2
        "e2_eq_min": 50.0,           # slightly stricter entry-quality when hybridised
    },

    # ================= ANN CONFIRMATION LAYER ============================
    "ann": {
        "enabled": True,
        "epochs": 40,
        "lr": 0.03,
        "l2": 1e-4,
        "min_train_trades": 60,      # below this -> ANN INSUFFICIENT, no filtering
        "threshold_mode": "train_expectancy",   # pick p_win cutoff maximising TRAIN expectancy
        "threshold_fixed": 0.50,
        "threshold_grid": [0.40, 0.45, 0.50, 0.55, 0.60, 0.65],
        "seed": 20260909,
    },

    # ================= COMPOSITE RANKING (OOS only) =====================
    # each sub-score in [0,1]; weighted -> 0-100. NOT win-rate driven.
    "composite_weights": {
        "expectancy_R": 0.28,
        "profit_factor": 0.18,
        "drawdown_R": 0.16,
        "stability": 0.16,          # 1 - TRAIN->OOS degradation
        "sample_size": 0.10,
        "regime_robustness": 0.12,
    },
}


def merged(overrides: dict | None = None) -> dict:
    c = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            _deep_update(c[k], v)
        else:
            c[k] = v
    return c


def _deep_update(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_update(dst[k], v)
        else:
            dst[k] = v
