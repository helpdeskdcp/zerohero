from __future__ import annotations

from dataclasses import asdict, dataclass

VERSION = "smart_u_turn_py 2.1.0 (port of Pine v2.1 Calibrated)"

# Pine section 02 "TIMEFRAME MAPPING", verbatim: chart TF -> (TF-A, TF-B, TF-C). Pine tf strings: minutes as "3", then "D", "W", "M", "3M", "6M".
PINE_TF_MAP = {
    "1": ("3", "5", "15"), "3": ("5", "30", "45"), "5": ("15", "30", "45"), "15": ("30", "60", "120"), "30": ("45", "60", "120"), "45": ("60", "120", "240"),
    "60": ("120", "240", "D"), "120": ("240", "D", "W"), "240": ("D", "W", "M"), "D": ("W", "M", "3M"), "W": ("M", "3M", "6M"),
}


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SignalParams:
    """Pine inputs (groups 01-03). Defaults are the Pine defaults; do not change to flatter results."""
    show_a: bool = True
    show_b: bool = True
    show_c: bool = True
    fast_len: int = 9
    slow_len: int = 21
    rsi_len: int = 14
    swing_len: int = 8
    min_score: int = 3
    wick_ratio: float = 1.3
    strict_trend: bool = True
    min_trend_votes: int = 1
    use_volume: bool = False
    volume_len: int = 20
    cooldown_bars: int = 3
    atr_len: int = 14
    sl_buffer: float = 0.20
    min_risk_atr: float = 0.5
    rr1: float = 1.0
    rr2: float = 2.0
    rr3: float = 3.0
    mintick: float = 0.05            # syminfo.mintick of the instrument (NIFTY index 0.05); set per instrument

    def validate(self) -> "SignalParams":
        if self.fast_len < 2 or self.slow_len < 3 or self.rsi_len < 2 or self.swing_len < 3 or self.volume_len < 2 or self.atr_len < 2: raise ConfigError("length below Pine minval")
        if not 2 <= self.min_score <= 5: raise ConfigError("min_score must be 2..5")
        if not 1 <= self.min_trend_votes <= 3: raise ConfigError("min_trend_votes must be 1..3")
        if self.cooldown_bars < 0 or self.wick_ratio < 0.5 or self.sl_buffer < 0 or self.min_risk_atr < 0.1 or self.mintick <= 0: raise ConfigError("invalid risk/threshold parameter")
        if self.rr1 < 0.5 or self.rr2 < 1.0 or self.rr3 < 1.5: raise ConfigError("target R below Pine minval")
        return self

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class BacktestParams:
    """Execution assumptions of the replay (the Pine script has none: it only draws levels)."""
    entry_mode: str = "NEXT_OPEN"            # NEXT_OPEN: fill at the open of the bar after the signal (conservative, tradable) | SIGNAL_CLOSE: Pine's own entry := close (not executable exactly)
    exit_model: str = "THIRDS"               # THIRDS: 1/3 at T1, T2, T3, rest stopped at the original stop | T1 | T2 | T3: whole position at that target
    position_policy: str = "IGNORE_WHILE_OPEN"   # IGNORE_WHILE_OPEN: one position, new signals rejected | REPLACE: close at the new signal bar's close and re-enter
    same_bar_rule: str = "STOP_FIRST"        # stop and target both inside one candle -> the stop is assumed first (never the favourable order)
    session_exit: bool = True                # flat at the last bar's close of the session day
    cost_points_roundtrip: float = 2.0       # brokerage+taxes+slippage per round trip, in index points (configurable assumption, not a measured cost)
    capital: float = 500000.0
    risk_pct: float = 1.0                    # risk per trade as % of the INITIAL capital (no compounding)

    def validate(self) -> "BacktestParams":
        if self.entry_mode not in ("NEXT_OPEN", "SIGNAL_CLOSE"): raise ConfigError("entry_mode")
        if self.exit_model not in ("THIRDS", "T1", "T2", "T3"): raise ConfigError("exit_model")
        if self.position_policy not in ("IGNORE_WHILE_OPEN", "REPLACE"): raise ConfigError("position_policy")
        if self.same_bar_rule != "STOP_FIRST": raise ConfigError("same_bar_rule: only STOP_FIRST (never the favourable order)")
        if self.cost_points_roundtrip < 0 or self.capital <= 0 or not 0 < self.risk_pct <= 100: raise ConfigError("invalid cost/capital/risk")
        return self

    def to_dict(self) -> dict:
        return asdict(self)
