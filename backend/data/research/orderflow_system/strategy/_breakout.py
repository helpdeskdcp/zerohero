"""Shared FSM for setup1 (bull) / setup2 (bear) breakout -- see
_common.py's module docstring for why this is factored out instead of 4
independent copies."""
from __future__ import annotations

from ._common import SetupBase, add_setup_columns


class BreakoutSetup(SetupBase):
    name = "breakout"

    def prepare(self, df):
        c = self.cfg
        return add_setup_columns(df, c["avg_delta_lookback"], c["vol_lookback"])

    def detect_context(self, df, i) -> bool:
        row = df.iloc[i]
        want_trend = "bullish" if self.bull else "bearish"
        return (row["htf_trend"] == want_trend and
                row["range_height_pct"] <= self.structure_cfg["range_max_height_pct"])

    def detect_trigger(self, df, i) -> bool:
        row = df.iloc[i]
        buf = self.cfg["breakout_buffer_ticks"] if self.bull else self.cfg["breakdown_buffer_ticks"]
        level = df["range_high"].iloc[i - 1] if self.bull else df["range_low"].iloc[i - 1]
        return row["close"] > level + buf if self.bull else row["close"] < level - buf

    def confirm(self, df, i) -> bool:
        c = self.cfg
        row = df.iloc[i]
        # delta is checked on the trigger bar itself -- the rulebook's own
        # wording ties it explicitly to "the breakout candle". CVD-new-high
        # and absorption carry no such same-candle qualifier, and both were
        # verified live (real NIFTY data) to be near-mutually-exclusive with
        # a wide-range breakout bar when required on that exact bar (a
        # strongly-trending, CVD-confirming bar is rarely also narrow-range)
        # -- both checked instead over the approach window (retest_bars back
        # through the trigger), matching "confirmed near the breakout", not
        # "confirmed on the literal breakout tick".
        delta_ok = (row["delta"] >= c["delta_mult"] * row["avg_delta_abs"] if self.bull
                   else row["delta"] <= -c["delta_mult"] * row["avg_delta_abs"])
        lb = c["cvd_new_high_lookback"] if self.bull else c["cvd_new_low_lookback"]
        cvd_ok = False
        for j in range(max(0, i - c["retest_bars"]), i + 1):
            window = df["cvd"].iloc[max(0, j - lb):j]
            if len(window) and (df["cvd"].iloc[j] > window.max() if self.bull else df["cvd"].iloc[j] < window.min()):
                cvd_ok = True
                break
        absorption_window = df["is_absorption"].iloc[max(0, i - c["retest_bars"]):i + 1]
        absorption_ok = bool(absorption_window.any())
        vol_ok = row["volume"] >= c["vol_mult"] * row["vol_ma_setup"]
        return bool(delta_ok) and bool(cvd_ok) and absorption_ok and bool(vol_ok)

    def invalidated(self, df, i, trigger_i, level) -> bool:
        c = self.cfg
        row = df.iloc[i]
        if i - trigger_i > c["fakeout_bars"]:
            return False  # fakeout window has passed -- no longer checked this way
        back_inside = row["close"] < level if self.bull else row["close"] > level
        if not self.bull:
            # bear-trap: price wicked down (making the breakdown look real)
            # but closed back up near the top of the bar's own range -- a
            # long LOWER wick relative to the bar's range. (close - low),
            # not (low - close): the latter is always <= 0 and could never
            # clear a positive threshold -- caught by a unit test before
            # this shipped.
            wick_ratio = ((row["close"] - row["low"]) / (row["high"] - row["low"])
                         if row["high"] > row["low"] else 0)
            if wick_ratio >= c["wick_threshold"]:
                return True
        return bool(back_inside)

    def scan(self, df, i, state) -> dict | None:
        c = self.cfg
        pending = state.get("pending")

        if pending is not None:
            trigger_i, level = pending["trigger_i"], pending["level"]
            if self.invalidated(df, i, trigger_i, level):
                state["pending"] = None
                return None
            if c["entry_mode"] == "breakout_close":
                if i == trigger_i + 1:
                    entry = df["open"].iloc[i]
                    state["pending"] = None
                    return self._build_entry(df, i, entry)
                return None
            # retest_hold
            tol = c["retest_tol_ticks"]
            retested = (abs(df["low"].iloc[i] - level) <= tol if self.bull
                       else abs(df["high"].iloc[i] - level) <= tol)
            holds = df["close"].iloc[i] > level - tol if self.bull else df["close"].iloc[i] < level + tol
            if retested and holds:
                if i + 1 >= len(df):
                    return None
                entry = df["open"].iloc[i + 1]
                state["pending"] = None
                return self._build_entry(df, i + 1, entry)
            if i - trigger_i > c["retest_bars"]:
                state["pending"] = None
            return None

        if i < 1 or not self.detect_context(df, i) or not self.detect_trigger(df, i) or not self.confirm(df, i):
            return None
        level = df["range_high"].iloc[i - 1] if self.bull else df["range_low"].iloc[i - 1]
        state["pending"] = {"trigger_i": i, "level": level}
        return None

    def _build_entry(self, df, entry_i, entry) -> dict:
        c = self.cfg
        stop_buf = c["stop_buffer_ticks"]
        breakout_low_high = df["low"].iloc[entry_i - 1] if self.bull else df["high"].iloc[entry_i - 1]
        stop = (min(breakout_low_high, entry) - stop_buf if self.bull
               else max(breakout_low_high, entry) + stop_buf)
        r = abs(entry - stop)
        rr = c["rr_targets"]
        targets = [entry + rr[0] * r, entry + rr[1] * r] if self.bull else [entry - rr[0] * r, entry - rr[1] * r]
        return {"entry_i": entry_i, "entry": entry, "stop": stop, "targets": targets,
                "direction": "LONG" if self.bull else "SHORT", "tag": self.name_tag}
