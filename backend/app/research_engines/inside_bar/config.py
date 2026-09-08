"""Every tunable for the Inside-Bar 2m engine. One place; override via
backtest.run(config=...)."""
from __future__ import annotations
import copy

DEFAULT_CONFIG: dict = {
    "tf": "2m",
    "eps": 1e-9,

    # --- context ---
    "ema_period": 20,                 # 20-EMA on 2m closes
    "ema_side_strict": True,          # LONG needs close > EMA, SHORT needs close < EMA (at the IB)

    # --- inside-bar detection ---
    "ib_contained_tol_atr": 0.0,     # 0 = strict containment (h<=prev.h AND l>=prev.l)
    "ib_max_range_frac_of_prev": 0.85,  # IB range must be <= this * prev bar range (a real contraction)
    "ib_min_prev_body_frac": 0.35,   # the bar BEFORE the IB must be a real candle (body/range)

    # --- momentum qualifier (the IB must follow a push) ---
    "mom_lookback": 5,               # bars before the IB
    "mom_min_move_atr": 1.2,         # |close_now - close_{-lookback}| >= this * ATR
    "mom_dir_must_match_side": True, # push direction must agree with the trade side

    # --- breakout trigger ---
    "breakout_window": 3,           # break IB high/low within this many bars (spec: 2-3)
    "breakout_buffer_atr": 0.03,   # price must exceed IB extreme by this * ATR to count
    "entry_on": "stop",            # 'stop' = enter at IB extreme +/- buffer when touched

    # --- risk / targets ---
    "sl_buffer_atr": 0.05,         # stop = opposite IB extreme -/+ this * ATR
    "target_r_multiples": [2, 3, 4],
    "scale_out": [                 # (R_level, fraction_of_position_to_close)
        [1.0, 0.3333],
        [2.0, 0.3333],
    ],
    "trail_after_r": 1.0,          # once +1R reached, stop -> breakeven; then trail by...
    "trail_step_r": 1.0,          # ...1R steps behind the high-water R
    "runner_cap_r": 4.0,         # close the last third at +4R
    "atr_period": 14,           # 2m ATR for all *_atr params

    # --- session limits ---
    "max_trades_per_day": 4,
    "no_new_entry_after_ist": "12:30",
    "hard_exit_ist": "13:00",
    "session_start_ist": "09:15",   # skip the first bars? see warmup_bars
    "warmup_bars": 25,             # need EMA(20) + momentum lookback settled

    # --- costs ---
    "cost_r": 0.0,                # index points have no spread; set >0 to stress-test

    # --- walk-forward ---
    "wf_block_sessions": 20,
    "wf_min_train_blocks": 3,
    "wf_max_folds": 12,
    "min_trades_for_verdict": 60,   # OOS trades needed before GO/NO-GO is meaningful
}


def merged(overrides: dict | None = None) -> dict:
    c = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c
