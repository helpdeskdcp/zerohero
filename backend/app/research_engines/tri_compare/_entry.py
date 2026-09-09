"""Shared two-stage entry wrapper (Engine 2 & 3). Reuses orderflow.entry_timing
for the SIGNAL_DETECTED -> WAITING_FOR_RETEST -> ENTRY_READY state machine,
no-chase filter, momentum-recovery and entry-quality score. The RR gate and the
SL / T1-T3 come from the SHARED harness risk model, not from here -- so entry_
timing runs with a permissive internal RR and the harness re-gates."""
from __future__ import annotations

from ..orderflow import entry_timing as ET


def _et_cfg(e_cfg: dict, cfg: dict) -> dict:
    return {
        "eps": 1e-9,
        "session_end_ist": cfg["hard_exit_ist"],
        "entry_zone_frac": e_cfg["entry_zone_frac"],
        "entry_zone_tol": e_cfg["entry_zone_tol"],
        "entry_max_wait_bars": e_cfg["entry_max_wait_bars"],
        "entry_lookback_bars": e_cfg["entry_lookback_bars"],
        "entry_recovery_window": e_cfg["entry_recovery_window"],
        "entry_require_momentum_recovery": e_cfg["require_momentum_recovery"],
        "sl_min_atr": 0.4, "sl_buffer_atr": 0.10, "max_risk_atr": 1.5,
        "min_rr": 0.05,                       # permissive: harness applies the real RR gate
        "max_entry_extension_atr": e_cfg["max_entry_extension_atr"],
        "nochase_vwap_atr": e_cfg["nochase_vwap_atr"],
        "nochase_baseline_atr": e_cfg["nochase_baseline_atr"],
        "nochase_fast_atr": e_cfg.get("nochase_fast_atr", 2.5),
        "invalidate_on_origin_break": e_cfg["invalidate_on_origin_break"],
        "eq_weights": {"signal_quality": 20, "retest_quality": 20, "momentum_recovery": 14,
                       "orderflow_confirm": 12, "structure": 12, "vwap_location": 8,
                       "volatility_fit": 6, "rr": 8},
    }


def two_stage(bars, frame, sig_i: int, direction: str, rk_score: float,
              e_cfg: dict, cfg: dict) -> dict:
    """-> the entry_timing outcome dict. status in {ENTRY_READY, EXPIRED_*}."""
    return ET.run(bars, frame, sig_i, direction, rk_score, _et_cfg(e_cfg, cfg))
