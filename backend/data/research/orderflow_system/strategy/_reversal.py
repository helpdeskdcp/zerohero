"""Shared FSM for setup3 (bull) / setup4 (bear) reversal -- see
_common.py's module docstring for why this is factored out instead of 4
independent copies."""
from __future__ import annotations

from ._common import SetupBase, add_setup_columns


class ReversalSetup(SetupBase):
    name = "reversal"

    def prepare(self, df):
        c = self.cfg
        # add_setup_columns' avg_delta_abs column isn't used by reversal (only
        # vol_ma_setup is) -- pass reclaim_vol_lookback for both rather than a
        # second, unused, magic lookback param.
        out = add_setup_columns(df, c["reclaim_vol_lookback"], c["reclaim_vol_lookback"])
        trend_bars = self.structure_cfg["trend_bars"]
        pct_change = out["close"].pct_change(trend_bars) * 100
        out["extended_trend_ok"] = (pct_change <= -self.structure_cfg["min_decline_pct"] if self.bull
                                    else pct_change >= self.structure_cfg["min_decline_pct"])
        out["rolling_extreme"] = (out["low"].rolling(trend_bars).min() if self.bull
                                  else out["high"].rolling(trend_bars).max())
        return out

    def is_overshoot(self, df, i) -> bool:
        row = df.iloc[i]
        if not bool(row["extended_trend_ok"]):
            return False
        prior_extreme = df["rolling_extreme"].iloc[i - 1]
        return bool(row["low"] < prior_extreme) if self.bull else bool(row["high"] > prior_extreme)

    def is_reclaim(self, df, i, candidate_i) -> bool:
        c = self.cfg
        extreme = df["low"].iloc[candidate_i] if self.bull else df["high"].iloc[candidate_i]
        reclaimed = (df["close"].iloc[i] > extreme + c["reclaim_ticks"] if self.bull
                    else df["close"].iloc[i] < extreme - c["reclaim_ticks"])
        return bool(reclaimed)

    def confirm_breakdown(self, df, i, candidate_i) -> dict:
        """Each confirmation sub-condition, individually -- see
        _breakout.py's confirm_breakdown() for why this shape exists
        (shared by confirm() and metrics/funnel.py's diagnostic)."""
        c = self.cfg
        absorption_ok = bool(df["is_absorption"].iloc[candidate_i])
        lb = c["divergence_lookback"]
        window = df["cvd"].iloc[max(0, candidate_i - lb):candidate_i]
        cvd_extreme_now = df["cvd"].iloc[candidate_i]
        # bullish divergence: price made a new low but CVD did NOT also make a new low
        # (selling pressure petering out even as price still fell -- the classic divergence)
        divergence_ok = (cvd_extreme_now > window.min() if self.bull
                         else cvd_extreme_now < window.max()) if len(window) else False
        flip_window = df["delta"].iloc[max(0, i - c["lt_delta_flip_bars"]):i + 1]
        delta_flip_ok = bool((flip_window > 0).all()) if self.bull else bool((flip_window < 0).all())
        # has_real_volume guard -- see _breakout.py's confirm_breakdown() for
        # why (same zero-volume-desert degenerate-comparison bug, same fix).
        vol_ma = df["vol_ma_setup"].iloc[i]
        vol_ok = bool(vol_ma > 0 and df["volume"].iloc[i] >= c["reclaim_vol_mult"] * vol_ma)
        return {"absorption_ok": absorption_ok, "divergence_ok": bool(divergence_ok),
               "delta_flip_ok": delta_flip_ok, "vol_ok": vol_ok}

    def confirm(self, df, i, candidate_i) -> bool:
        b = self.confirm_breakdown(df, i, candidate_i)
        min_conf = self.cfg.get("min_confirmations")
        if min_conf is None:
            return b["absorption_ok"] and b["divergence_ok"] and b["delta_flip_ok"] and b["vol_ok"]
        # V-a structural variant -- see _breakout.py's confirm() for why.
        # Here the funnel finding is even starker: delta_flip_ok (ALL of the
        # last lt_delta_flip_bars+1 bars same-signed) fired on 0/150-211
        # reclaims in 3 of 4 setup/timeframe combinations (2026-09-29 funnel
        # diagnostic) -- an "ALL of N" requirement is inherently brittle to
        # a single noise bar; score-based confirmation is the structural fix,
        # not a further loosening of delta_flip's own window/threshold.
        return sum(b.values()) >= min_conf

    def scan(self, df, i, state) -> dict | None:
        c = self.cfg
        candidate = state.get("candidate")

        if candidate is not None:
            candidate_i = candidate["i"]
            if i - candidate_i > c["reclaim_max_bars"]:
                state["candidate"] = None
                return None
            still_extreme = (df["low"].iloc[i] < df["low"].iloc[candidate_i] if self.bull
                             else df["high"].iloc[i] > df["high"].iloc[candidate_i])
            if still_extreme:
                state["candidate"] = {"i": i}  # a deeper overshoot supersedes the earlier one
                return None
            if self.is_reclaim(df, i, candidate_i) and self.confirm(df, i, candidate_i) and self.sm_gate_ok(df, i):
                if i + 1 >= len(df):
                    return None
                entry = df["open"].iloc[i + 1]
                extreme = df["low"].iloc[candidate_i] if self.bull else df["high"].iloc[candidate_i]
                stop = extreme - c["reversal_stop_ticks"] if self.bull else extreme + c["reversal_stop_ticks"]
                r = abs(entry - stop)
                rr = c["rr_targets"]
                targets = ([entry + rr[0] * r, entry + rr[1] * r] if self.bull
                          else [entry - rr[0] * r, entry - rr[1] * r])
                state["candidate"] = None
                return {"entry_i": i + 1, "entry": entry, "stop": stop, "targets": targets,
                        "direction": "LONG" if self.bull else "SHORT", "tag": self.name_tag}
            return None

        if i < self.structure_cfg["trend_bars"] + 1:
            return None
        if self.is_overshoot(df, i):
            state["candidate"] = {"i": i}
        return None
