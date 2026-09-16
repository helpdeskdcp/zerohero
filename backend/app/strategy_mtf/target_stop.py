"""
Stop-loss placement that minimizes BOTH (a) probability of a random-noise
stop-out and (b) absolute risk size, plus the staged R-multiple trailing
target system (1R confirm -> 2R major target + trail -> 3R trail -> 4R
momentum-gated).

SL noise-clearance method: for each candidate buffer in
`cfg.sl_noise_grid_atr_mults` (ascending), measure what fraction of the
recent `cfg.sl_noise_lookback_bars` bar-to-bar excursions (how far a single
bar's wick moved against the PRIOR close, a real historical noise sample)
would have breached that buffer -- pick the SMALLEST buffer whose breach
rate is <= `cfg.sl_max_noise_touch_rate`. A genuine, backtest-style
two-objective placement (small deviation size AND cleared >=95% of recent
real noise), not an arbitrary fixed multiple.

Simplification, documented not hidden: excursions are normalized by the
single most-recent ATR value (not a full rolling ATR series recomputed at
every historical bar) -- reasonable over a bounded lookback where ATR does
not typically change by an order of magnitude, and avoids a second,
separate rolling-indicator computation this package doesn't otherwise need.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .mtf_config import MTFConfig


@dataclass
class SLPlacement:
    sl_price: float
    buffer_atr_mult: float
    noise_touch_rate: float
    structure_level: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TargetStopPlan:
    entry: float
    direction: str
    stop_loss: float
    r_unit: float
    targets: dict            # {1.0: price, 2.0: price, ...}
    sl_placement: SLPlacement

    def to_dict(self) -> dict:
        return {**asdict(self), "sl_placement": self.sl_placement.to_dict()}


@dataclass
class TrailState:
    stage_reached: float     # highest R-multiple stage confirmed so far (0.0 = none yet)
    current_sl: float
    closed: bool = False
    exit_reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def find_noise_clearing_sl(bars: list[dict], direction: str, structure_level: float,
                           atr: float | None, cfg: MTFConfig | None = None) -> SLPlacement:
    cfg = cfg or MTFConfig()
    if not atr or atr <= 0 or len(bars) < 3:
        # fall back to the widest grid buffer -- no noise data to optimise against
        buf = cfg.sl_noise_grid_atr_mults[-1]
        sl = structure_level - buf * (atr or 0.0) if direction == "BULLISH" else structure_level + buf * (atr or 0.0)
        return SLPlacement(sl_price=sl, buffer_atr_mult=buf, noise_touch_rate=1.0, structure_level=structure_level)

    window = bars[-cfg.sl_noise_lookback_bars:]
    excursions_atr = []
    for i in range(1, len(window)):
        prev_close = window[i - 1]["c"]
        if direction == "BULLISH":
            excursion = max(0.0, prev_close - window[i]["l"])
        else:
            excursion = max(0.0, window[i]["h"] - prev_close)
        excursions_atr.append(excursion / atr)

    chosen_buf = cfg.sl_noise_grid_atr_mults[-1]
    chosen_rate = 1.0
    for buf in cfg.sl_noise_grid_atr_mults:
        breaches = sum(1 for e in excursions_atr if e > buf)
        rate = breaches / len(excursions_atr) if excursions_atr else 1.0
        if rate <= cfg.sl_max_noise_touch_rate:
            chosen_buf, chosen_rate = buf, rate
            break

    sl_price = structure_level - chosen_buf * atr if direction == "BULLISH" else structure_level + chosen_buf * atr
    return SLPlacement(sl_price=sl_price, buffer_atr_mult=chosen_buf, noise_touch_rate=round(chosen_rate, 4),
                       structure_level=structure_level)


def build_plan(entry: float, direction: str, structure_level: float, bars: list[dict],
              atr: float | None, cfg: MTFConfig | None = None) -> TargetStopPlan:
    cfg = cfg or MTFConfig()
    placement = find_noise_clearing_sl(bars, direction, structure_level, atr, cfg)
    r_unit = abs(entry - placement.sl_price)
    sign = 1 if direction == "BULLISH" else -1
    targets = {stage: round(entry + sign * stage * r_unit, 4) for stage in cfg.r_multiple_stages}
    return TargetStopPlan(entry=entry, direction=direction, stop_loss=placement.sl_price,
                          r_unit=round(r_unit, 4), targets=targets, sl_placement=placement)


def initial_trail_state(plan: TargetStopPlan) -> TrailState:
    return TrailState(stage_reached=0.0, current_sl=plan.stop_loss)


def update_trailing(plan: TargetStopPlan, price: float, *, momentum_ok: bool, trail: TrailState) -> TrailState:
    """Staged trailing: reaching 1R is a confirmation checkpoint (trail SL
    to breakeven). Reaching 2R is the first major target -- trail SL to 1R.
    Continuing to 3R/4R is GATED on `momentum_ok` (the caller's own
    OI/volume/volatility read) -- without it, lock in the current stage and
    stop advancing rather than holding for a target momentum no longer
    supports."""
    if trail.closed:
        return trail
    sign = 1 if plan.direction == "BULLISH" else -1
    favourable = sign * (price - plan.entry)

    # stop-loss hit first, at whatever level it's currently trailed to
    if sign * (price - trail.current_sl) <= 0:
        trail.closed, trail.exit_reason = True, "STOPPED_OUT"
        return trail

    stages = sorted(plan.targets.keys())
    for stage in stages:
        target_price = plan.targets[stage]
        reached = favourable >= sign * (target_price - plan.entry)
        if not reached or stage <= trail.stage_reached:
            continue
        if stage >= stages[2] and not momentum_ok:   # advancing past the 2nd stage needs live momentum support
            continue
        trail.stage_reached = stage
        if stage == stages[0]:
            trail.current_sl = plan.entry                                    # 1R -> breakeven
        elif stage == stages[-1]:
            trail.closed, trail.exit_reason = True, "FINAL_TARGET"
            trail.current_sl = plan.targets[stages[-2]]
        else:
            idx = stages.index(stage)
            trail.current_sl = plan.targets[stages[idx - 1]]                 # trail to the PRIOR stage's price
    return trail
