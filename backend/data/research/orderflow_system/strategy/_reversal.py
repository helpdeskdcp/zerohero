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

    # -- V-c structural variant (2026-09-29, user request): replace the
    # TRIGGER itself, not just its confirmation. is_overshoot/is_reclaim's
    # "N-bar rolling extreme, then a later close crosses back past it" is a
    # naive heuristic with no real market-structure meaning behind either
    # half. SM-7 (liquidity_sweep) and SM-8 (structure_shift) are REAL
    # (is_proxy=False) price-structure detectors already built for the
    # smart-money gate (v4) but only ever used there as a GATE on top of
    # this same naive trigger -- never as the trigger's own definition.
    # Opt-in via cfg["trigger_mode"]=="liquidity_sweep" (default stays
    # "rolling_extreme", i.e. every existing config/test/backtest is
    # unchanged unless explicitly opted in). See config/v7.yaml.
    def is_sweep_candidate(self, df, i) -> bool:
        from .smartmoney import liquidity_sweep
        r = liquidity_sweep.detect(df, i, self.full_cfg["smartmoney"])
        want_event = "swept_lows" if self.bull else "swept_highs"
        return bool(r.present and r.detail.get("event") == want_event)

    def is_choch_confirm(self, df, i) -> bool:
        from .smartmoney import structure_shift
        r = structure_shift.detect(df, i, self.full_cfg["smartmoney"])
        want_dir = "bull" if self.bull else "bear"
        return bool(r.present and r.direction == want_dir and r.detail.get("event") == "CHoCH")

    def confirm_breakdown(self, df, i, candidate_i) -> dict:
        """Each confirmation sub-condition, individually -- see
        _breakout.py's confirm_breakdown() for why this shape exists
        (shared by confirm() and metrics/funnel.py's diagnostic).

        V-b structural variant (2026-09-29 funnel diagnostic, post zero-
        volume-bug-fix data): absorption_ok was checked on the single
        candidate_i (overshoot) bar alone and fired on literally 0/12-61
        reclaims in ALL FOUR setup/timeframe combinations -- an "exactly on
        this one tick" requirement for a condition that is itself an AND of
        two sub-conditions (volume spike + tight range) is the same
        same-bar-rarity problem _breakout.py's confirm_breakdown() already
        hit and fixed by widening to a window; reversal never got the
        equivalent fix. delta_flip_ok separately requires ALL of
        lt_delta_flip_bars+1 bars to be same-signed, which is brittle to a
        single noise bar (documented in confirm()'s V-a comment) and still
        fired on only 0-8% of reclaims even after V-a's outer score gate.
        Both are now checked as a WINDOW/NET condition instead of a single-
        bar / ALL-of-N one -- a genuine change to what each sub-condition
        MEANS, not another threshold widen on the same brittle shape."""
        c = self.cfg
        # was: df["is_absorption"].iloc[candidate_i] alone.
        absorption_window = df["is_absorption"].iloc[candidate_i:i + 1]
        absorption_ok = bool(absorption_window.any())
        lb = c["divergence_lookback"]
        window = df["cvd"].iloc[max(0, candidate_i - lb):candidate_i]
        cvd_extreme_now = df["cvd"].iloc[candidate_i]
        # bullish divergence: price made a new low but CVD did NOT also make a new low
        # (selling pressure petering out even as price still fell -- the classic divergence)
        divergence_ok = (cvd_extreme_now > window.min() if self.bull
                         else cvd_extreme_now < window.max()) if len(window) else False
        # was: (flip_window > 0).all() / (flip_window < 0).all() -- ALL bars
        # same-signed. Now: the WINDOW'S NET delta is in the expected
        # direction -- one noise bar of the wrong sign no longer zeroes out
        # an otherwise-real flip the way a strict AND did.
        flip_window = df["delta"].iloc[max(0, i - c["lt_delta_flip_bars"]):i + 1]
        delta_flip_ok = bool(flip_window.sum() > 0) if self.bull else bool(flip_window.sum() < 0)
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

    def _build_reversal_entry(self, df, i, candidate_i):
        c = self.cfg
        entry = df["open"].iloc[i + 1]
        extreme = df["low"].iloc[candidate_i] if self.bull else df["high"].iloc[candidate_i]
        stop = extreme - c["reversal_stop_ticks"] if self.bull else extreme + c["reversal_stop_ticks"]
        r = abs(entry - stop)
        rr = c["rr_targets"]
        targets = ([entry + rr[0] * r, entry + rr[1] * r] if self.bull
                  else [entry - rr[0] * r, entry - rr[1] * r])
        return {"entry_i": i + 1, "entry": entry, "stop": stop, "targets": targets,
               "direction": "LONG" if self.bull else "SHORT", "tag": self.name_tag}

    def scan(self, df, i, state) -> dict | None:
        if str(self.cfg.get("trigger_mode", "rolling_extreme")) == "liquidity_sweep":
            return self._scan_liquidity_sweep(df, i, state)
        return self._scan_rolling_extreme(df, i, state)

    def _scan_rolling_extreme(self, df, i, state) -> dict | None:
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
                state["candidate"] = None
                return self._build_reversal_entry(df, i, candidate_i)
            return None

        if i < self.structure_cfg["trend_bars"] + 1:
            return None
        if self.is_overshoot(df, i):
            state["candidate"] = {"i": i}
        return None

    def _scan_liquidity_sweep(self, df, i, state) -> dict | None:
        """V-c: candidate = a real SM-7 sweep (wick beyond a prior swing +
        close retraces back), confirm = a real SM-8 CHoCH in the reversal
        direction -- replaces the naive rolling-extreme/reclaim-ticks pair.
        candidate_i is still "the bar the extreme/sweep happened on", so
        _build_reversal_entry's low/high[candidate_i] stop reference is
        unchanged and correct for both trigger modes."""
        c = self.cfg
        candidate = state.get("candidate")

        if candidate is not None:
            candidate_i = candidate["i"]
            if i - candidate_i > c["reclaim_max_bars"]:
                state["candidate"] = None
                return None
            if self.is_sweep_candidate(df, i):
                still_deeper = (df["low"].iloc[i] < df["low"].iloc[candidate_i] if self.bull
                               else df["high"].iloc[i] > df["high"].iloc[candidate_i])
                if still_deeper:
                    state["candidate"] = {"i": i}  # a deeper sweep supersedes the earlier one
                    return None
            if self.is_choch_confirm(df, i) and self.confirm(df, i, candidate_i) and self.sm_gate_ok(df, i):
                if i + 1 >= len(df):
                    return None
                state["candidate"] = None
                return self._build_reversal_entry(df, i, candidate_i)
            return None

        if i < self.structure_cfg["trend_bars"] + 1:
            return None
        if self.is_sweep_candidate(df, i):
            state["candidate"] = {"i": i}
        return None
