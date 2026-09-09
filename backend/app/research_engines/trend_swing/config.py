"""All tunables for the daily trend-swing harness. FROZEN before OOS; no value
is fitted on OOS or before the baseline result. Lookbacks are in TRADING DAYS.
The E1 signal logic is exactly the intraday tri_compare E1, moved to daily."""
from __future__ import annotations
import copy

DEFAULT_CONFIG: dict = {
    "symbol": "NIFTY",
    "start": "2010-01-01",
    "end": "2025-12-31",
    "warmup_days": 220,               # EMA200 + momentum lookback settled

    # ---- costs: 1 index point each, per round trip (matches tri_compare) ----
    "cost_points": 1.0,
    "slippage_points": 1.0,          # applied adverse on the entry fill

    # ---- E1 trend signal (SAME frozen logic as tri_compare E1) ----
    "ema_fast": 20,
    "ema_slow": 60,
    "ema_trend": 200,
    "ema_trend_slope_lookback": 10,
    "donchian_n": 40,                # breakout of the prior-N high/low
    "donchian_buffer_atr": 0.05,
    "atr_period": 14,
    "adx_period": 14,
    "eff_ratio_lookback": 10,
    "atr_pct_lo_pctl": 0.20,
    "atr_pct_hi_pctl": 0.92,
    "min_trend_persist_days": 4,     # anti-whipsaw: trend state held this long
    "eff_ratio_min": 0.30,          # Kaufman efficiency ratio (trendiness)
    "adx_min": 18.0,

    # ---- risk / exit (ATR SL + trailing exit + vol-normalised sizing) ----
    "sl_atr_mult": 2.5,            # hard stop distance from entry
    "trail_mode": "chandelier",   # 'chandelier' | 'donchian' | 'none'
    "trail_atr_mult": 3.0,        # chandelier: high_water -/+ this * ATR
    "donchian_exit_n": 20,        # donchian exit channel (if trail_mode='donchian')
    "exit_on_trend_flip": True,   # a confirmed opposite E1 setup -> flatten
    "reverse_on_flip": False,
    "max_hold_days": 0,           # 0 = no cap
    "cooldown_days": 2,          # after an exit, wait N days before a same-dir re-entry
    "one_position_at_a_time": True,

    # ---- splits + PURGED walk-forward ----
    "train_frac": 0.55,
    "val_frac": 0.20,             # OOS = remaining 0.25
    "wf_folds": 6,
    "purge_days": 10,           # drop trades straddling a fold/split boundary + embargo

    # ---- GO / NO-GO / INCONCLUSIVE (OOS, on the RAW engine) ----
    "min_trades_for_verdict": 40,
    "verdict_min_sharpe": 0.6,
    "verdict_min_pf": 1.3,
    "verdict_min_expectancy_R": 0.0,
    "verdict_min_pos_years": 3,
    "verdict_min_pos_wf_folds": 4,     # of wf_folds
    "verdict_max_dd_R": 18.0,
    "verdict_max_degradation": 0.60,   # |TRAIN metric - OOS metric| / |TRAIN metric|

    # ---- baselines to beat ----
    "bench_ema_fast": 50,        # simple long-only EMA-cross baseline
    "bench_ema_slow": 200,
    "bench_donchian_n": 50,      # simple long+short Donchian-breakout baseline

    # ---- crisis-window diagnostic ----
    "crisis_min_decline_pct": 0.10,
    "crisis_max_days": 90,
}


def merged(overrides: dict | None = None) -> dict:
    c = copy.deepcopy(DEFAULT_CONFIG)
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(c.get(k), dict):
            c[k].update(v)
        else:
            c[k] = v
    return c
