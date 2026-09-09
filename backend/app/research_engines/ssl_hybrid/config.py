"""Every tunable for the SSL Hybrid PRO engine. Pine input defaults are kept
verbatim; the backtest-only knobs are grouped at the bottom."""
from __future__ import annotations
import copy

DEFAULT_CONFIG: dict = {
    # ---- Pine inputs ----
    # SSL1 = 100 is the user's TRAINED baseline config (NOT the Pine default 60).
    # Do not change it during filter-layer optimisation; robustness sweep
    # {80,90,100,110,120} only after baseline + precision-filter are fixed.
    "ssl1_len": 100,
    "ssl2_len": 5,
    "exit_len": 15,
    "baseline_len": 60,
    "ema_len": 200,
    "hma_len": 55,
    "rsi_len": 14,
    "adx_len": 14,
    "adx_min": 20.0,
    "vol_len": 20,
    "vol_mult": 1.0,
    "atr_len": 14,
    "atr_mult": 1.5,
    "rr1": 1.0,
    "rr2": 2.0,
    "rr3": 3.0,
    "min_confidence": 70,

    # ---- backtest: timeframe + data ----
    "tf_min": 15,                    # chart timeframe in minutes (Pine is chart-TF agnostic)
    "warmup_bars": 220,             # EMA200 needs ~200 settled bars before a valid signal
    "session_anchored_vwap": True,  # reset the VWAP proxy each session day

    # ---- backtest: trade management ----
    "scale_out": [[1.0, 0.3333], [2.0, 0.3333]],   # (RR level, fraction) -- last third rides to T3
    "move_sl_to_be_after_rr": 1.0,  # once +1R banked, stop -> entry
    "final_target_rr": 3.0,        # last third exits here (== rr3)
    "exit_on_opposite_signal": True,
    "hard_exit_session_end": True,  # VWAP is session-anchored -> treat as intraday
    "session_end_ist": "15:15",
    "one_position_at_a_time": True,
    "cost_points": 0.0,            # per round-trip; set >0 to stress slippage/fees

    # ---- walk-forward / verdict ----
    "oos_frac": 0.30,             # most-recent fraction of sessions held out
    "min_trades_for_verdict": 40,
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
