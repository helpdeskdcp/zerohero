"""
ANN CONFIRMATION LAYER -- SEPARATE, TRAIN-ONLY, NO FUTURE LEAKAGE.

Reuses the existing hcs adaptive logit (`hcs.adaptive.OnlineLogit`) + PAV
isotonic (`hcs.adaptive_mc.Isotonic`). It is NOT a new model -- it is the
project's small L2-regularised logistic, fit here on an engine's TRAIN trades
only, per walk-forward fold, to produce P(win) for VAL / OOS trades.

  A = engine-only                (all ENTRY_READY trades)
  B = engine + ANN confirmation  (keep trades with P(win) >= threshold)

The threshold is chosen on TRAIN (max TRAIN expectancy over a grid) and frozen.
Features are all measured at the signal bar (past-only). The trade's own future
never enters its feature vector; the label (win/loss) is used only for TRAIN
fitting. ANN is kept only if B beats A on OOS.
"""
from __future__ import annotations

import random

from app.hcs.adaptive import OnlineLogit
from app.hcs.adaptive_mc import Isotonic


def _feat(t: dict) -> dict:
    """Bounded, past-only feature vector from a trade's signal-bar context."""
    ctx = t.get("context") or {}
    reg_t = {"TRENDING": 1.0, "MIXED": 0.5, "RANGING": 0.2, "CHOP": 0.0}.get(t.get("regime_trend"), 0.3)
    reg_v = {"HIGH": 1.0, "MID": 0.5, "LOW": 0.0}.get(t.get("regime_vol"), 0.5)
    x = {
        "bias": 1.0,
        "long": 1.0 if t["direction"] == "LONG" else 0.0,
        "adx": _n((t.get("adx") or 0.0) / 40.0),
        "atr_pct_rank": _n(t.get("atr_pct_rank") or 0.5),
        "regime_trend": reg_t,
        "regime_vol": reg_v,
        "rr_room": _n((t.get("rr_room") or 0.0) / 4.0),
        "entry_quality": _n((t.get("entry_quality") or 50.0) / 100.0),
        "eff_ratio": _n(ctx.get("eff_ratio") or 0.0),
        "rsi_dev": _n(abs((ctx.get("rsi") or 50.0) - 50.0) / 50.0),
        "sr_dist": _n((ctx.get("sr_dist_atr") or 1.5) / 3.0),
        "hour": _n(t["minute_of_day"] / 375.0),
    }
    return x


def _n(v):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return -1.0 if v < -1.0 else 3.0 if v > 3.0 else v


class AnnConfirm:
    def __init__(self, cfg_ann: dict):
        self.c = cfg_ann
        self.model: OnlineLogit | None = None
        self.iso: Isotonic | None = None
        self.threshold: float = cfg_ann["threshold_fixed"]
        self.status = "UNFIT"
        self.n_train = 0

    def fit(self, train_trades: list[dict]) -> "AnnConfirm":
        self.n_train = len(train_trades)
        if self.n_train < self.c["min_train_trades"]:
            self.status = "INSUFFICIENT"
            return self
        rng = random.Random(self.c["seed"])
        base = sum(1 for t in train_trades if t["win"]) / self.n_train
        m = OnlineLogit(base_rate=max(0.05, min(0.95, base)))
        rows = [(_feat(t), 1 if t["win"] else 0) for t in train_trades]
        for _ in range(self.c["epochs"]):
            order = list(range(len(rows)))
            rng.shuffle(order)
            for j in order:
                x, y = rows[j]
                m.update(x, y, lr=self.c["lr"])
        self.model = m
        # isotonic recalibration on TRAIN raw probs
        self.iso = Isotonic().fit([(m.predict(x), y) for x, y in rows])
        # pick threshold: max TRAIN expectancy over the grid (never OOS)
        if self.c["threshold_mode"] == "train_expectancy":
            best_thr, best_e = self.c["threshold_fixed"], -9.0
            for thr in self.c["threshold_grid"]:
                kept = [t for t in train_trades if self.p_win(t) >= thr]
                if len(kept) < max(20, self.c["min_train_trades"] // 3):
                    continue
                e = sum(t["r_multiple"] for t in kept) / len(kept)
                if e > best_e:
                    best_e, best_thr = e, thr
            self.threshold = best_thr
        self.status = "OK"
        return self

    def p_win(self, t: dict) -> float:
        if self.status != "OK" or self.model is None:
            return 1.0                       # INSUFFICIENT -> never filters
        raw = self.model.predict(_feat(t))
        return self.iso.predict(raw) if self.iso else raw

    def confirm(self, t: dict) -> bool:
        return self.p_win(t) >= self.threshold

    def filter(self, trades: list[dict]) -> list[dict]:
        return [dict(t, ann_p_win=round(self.p_win(t), 4)) for t in trades if self.confirm(t)]

    def info(self) -> dict:
        return {"status": self.status, "n_train": self.n_train,
                "threshold": self.threshold,
                "top_weights": (sorted(self.model.w.items(), key=lambda kv: -abs(kv[1]))[:6]
                                if self.model else [])}
