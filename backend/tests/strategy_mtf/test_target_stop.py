"""
SL noise-clearance placement + staged R-multiple trailing targets.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2]))

from app.strategy_mtf.mtf_config import MTFConfig  # noqa: E402
from app.strategy_mtf.target_stop import build_plan, initial_trail_state, update_trailing  # noqa: E402


def _bar(o, h, l, c, v=1000):
    return {"t": "2026-08-04T03:45:00Z", "o": o, "h": h, "l": l, "c": c, "v": v}


def _quiet_bars(n=100, price=100.0):
    """Small, consistent noise -- every bar's low sits ~0.2 below the prior close."""
    bars = [_bar(price, price + 0.3, price - 0.3, price)]
    for _ in range(n - 1):
        c = bars[-1]["c"]
        bars.append(_bar(c, c + 0.3, c - 0.2, c))
    return bars


def test_sl_buffer_clears_recent_real_noise_at_the_configured_rate():
    bars = _quiet_bars(100)
    cfg = MTFConfig(sl_noise_grid_atr_mults=(0.1, 0.2, 0.3, 0.5, 1.0), sl_max_noise_touch_rate=0.05)
    plan = build_plan(entry=100.0, direction="BULLISH", structure_level=99.5, bars=bars, atr=1.0, cfg=cfg)
    assert plan.sl_placement.noise_touch_rate <= 0.05
    assert plan.stop_loss < 99.5   # SL sits below the structure level for a bullish plan


def test_smaller_buffer_chosen_when_noise_is_genuinely_small():
    quiet = _quiet_bars(100)
    cfg = MTFConfig(sl_noise_grid_atr_mults=(0.1, 0.2, 0.5, 1.0, 2.0), sl_max_noise_touch_rate=0.05)
    plan_quiet = build_plan(entry=100.0, direction="BULLISH", structure_level=99.5, bars=quiet, atr=1.0, cfg=cfg)

    noisy = [_bar(100, 101, 100 - i % 3, 100) for i in range(100)]   # much bigger wicks
    plan_noisy = build_plan(entry=100.0, direction="BULLISH", structure_level=99.5, bars=noisy, atr=1.0, cfg=cfg)
    assert plan_quiet.sl_placement.buffer_atr_mult <= plan_noisy.sl_placement.buffer_atr_mult


def test_targets_are_staged_at_the_configured_r_multiples():
    bars = _quiet_bars(100)
    cfg = MTFConfig(r_multiple_stages=(1.0, 2.0, 3.0, 4.0))
    plan = build_plan(entry=100.0, direction="BULLISH", structure_level=99.0, bars=bars, atr=1.0, cfg=cfg)
    r = plan.r_unit
    assert plan.targets[1.0] == round(100.0 + r, 4)
    assert plan.targets[2.0] == round(100.0 + 2 * r, 4)
    assert plan.targets[4.0] == round(100.0 + 4 * r, 4)


def test_trailing_locks_breakeven_at_1r_and_advances_to_2r():
    bars = _quiet_bars(100)
    plan = build_plan(entry=100.0, direction="BULLISH", structure_level=99.0, bars=bars, atr=1.0)
    trail = initial_trail_state(plan)
    trail = update_trailing(plan, plan.targets[1.0], momentum_ok=True, trail=trail)
    assert trail.stage_reached == 1.0 and trail.current_sl == plan.entry
    trail = update_trailing(plan, plan.targets[2.0], momentum_ok=True, trail=trail)
    assert trail.stage_reached == 2.0 and trail.current_sl == plan.targets[1.0]


def test_advancing_past_2r_is_gated_on_momentum():
    bars = _quiet_bars(100)
    plan = build_plan(entry=100.0, direction="BULLISH", structure_level=99.0, bars=bars, atr=1.0)
    trail = initial_trail_state(plan)
    trail = update_trailing(plan, plan.targets[2.0], momentum_ok=True, trail=trail)
    # price reaches 3R but momentum has faded -- must NOT advance the stage
    trail = update_trailing(plan, plan.targets[3.0], momentum_ok=False, trail=trail)
    assert trail.stage_reached == 2.0


def test_stop_loss_hit_closes_the_trade():
    bars = _quiet_bars(100)
    plan = build_plan(entry=100.0, direction="BULLISH", structure_level=99.0, bars=bars, atr=1.0)
    trail = initial_trail_state(plan)
    trail = update_trailing(plan, plan.stop_loss - 0.01, momentum_ok=True, trail=trail)
    assert trail.closed and trail.exit_reason == "STOPPED_OUT"


def test_final_target_closes_the_trade():
    bars = _quiet_bars(100)
    plan = build_plan(entry=100.0, direction="BULLISH", structure_level=99.0, bars=bars, atr=1.0)
    trail = initial_trail_state(plan)
    for stage in (1.0, 2.0, 3.0, 4.0):
        trail = update_trailing(plan, plan.targets[stage], momentum_ok=True, trail=trail)
    assert trail.closed and trail.exit_reason == "FINAL_TARGET"
