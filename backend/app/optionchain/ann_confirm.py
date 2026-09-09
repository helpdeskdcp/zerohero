"""
Layer 4 -- ANN CONFIRMATION for the Option Structure Engine.

SEPARATE, TRAIN-ONLY, NO FUTURE LEAKAGE. Not a new model: it reuses the
project's small L2 online logistic (`hcs.adaptive.OnlineLogit`) + PAV isotonic
(`hcs.adaptive_mc.Isotonic`), fit on an OOS-fold's TRAIN records only, to score
P(win) for a structure-derived directional call.

  A = structure-only            (take every non-NEUTRAL structure signal)
  B = structure + ANN           (keep signals with P(win) >= threshold)

The threshold is picked on TRAIN (max TRAIN expectancy over a grid) and frozen.
Features come only from the `OptionStructureState` at the signal snapshot -- the
snapshot's own future never enters its feature vector; the win/loss label is
used only for TRAIN fitting. Kept only on a demonstrated OOS improvement
(`ann_backtest._verdict`); the ANN can never rescue a non-positive base.
"""
from __future__ import annotations

import random
from datetime import datetime

from app.hcs.adaptive import OnlineLogit
from app.hcs.adaptive_mc import Isotonic

DEFAULT_ANN_CFG = {
    "epochs": 40,
    "lr": 0.03,
    "min_train": 80,                 # below -> INSUFFICIENT, never filters
    "threshold_mode": "train_expectancy",
    "threshold_fixed": 0.50,
    "threshold_grid": [0.40, 0.45, 0.50, 0.55, 0.60, 0.65],
    "min_keep_train": 25,            # a grid threshold must keep >= this many TRAIN signals
    "seed": 20260910,
}

_MP_PULL = {"AT_MAGNET": 0.0, "MODERATE": 0.5, "STRONG": 1.0}
_GEX_SIGN = {-1: 0.0, 0: 0.5, 1: 1.0}


def _n(v, lo=-1.0, hi=3.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    if v != v:                        # NaN
        return 0.0
    return lo if v < lo else hi if v > hi else v


def _minute_of_day(ts) -> float:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return _n((dt.hour * 60 + dt.minute) / 1440.0, 0.0, 1.0)
    except (ValueError, AttributeError):
        return 0.5


def oc_feat(state, direction: str, ts=None) -> dict:
    """Bounded, snapshot-only feature vector from an OptionStructureState-shaped
    dict (`state` may be the dataclass or its `.to_dict()`)."""
    g = state.to_dict() if hasattr(state, "to_dict") else dict(state or {})
    mp = g.get("max_pain") or {}
    pr = g.get("pcr_regime") or {}
    ow = g.get("oi_walls") or {}
    sk = g.get("iv_skew_bias") or {}
    ivr = g.get("iv_vs_realized") or {}
    gx = g.get("gex_regime") or {}
    ec = g.get("expiry_context") or {}
    cap = g.get("capability") or {}
    dqs = (g.get("quality") or {}).get("dqs") if isinstance(g.get("quality"), dict) else None
    return {
        "bias": 1.0,
        "long": 1.0 if str(direction).upper() == "LONG" else 0.0,
        "mp_dist": _n((mp.get("distance_pct") or 0.0) / 3.0),
        "mp_pull": _MP_PULL.get(mp.get("magnet_pull"), 0.3),
        "pcr_oi": _n(((pr.get("pcr_oi") or 1.0) - 1.0) / 1.5),
        "pcr_vol": _n(((pr.get("pcr_vol") or 1.0) - 1.0) / 1.5),
        "skew_slope": _n(sk.get("skew_slope") or 0.0),
        "rr25": _n((sk.get("rr_25") or 0.0) * 50.0),
        "atm_iv": _n((sk.get("atm_iv") or 0.0) / 0.5, 0.0, 3.0),
        "iv_rich": {"IV_RICH": 1.0, "FAIR": 0.5, "IV_CHEAP": 0.0}.get(ivr.get("state"), 0.5),
        "gex_sign": _GEX_SIGN.get(gx.get("regime_sign"), 0.5),
        "spot_vs_flip": {"ABOVE": 1.0, "BELOW": 0.0}.get(gx.get("spot_vs_flip"), 0.5),
        "wall_res": _n((ow.get("dist_res_pct") or 3.0) / 3.0, 0.0, 3.0),
        "wall_sup": _n(abs(ow.get("dist_sup_pct") or 3.0) / 3.0, 0.0, 3.0),
        "boxed": 1.0 if ow.get("boxed") else 0.0,
        "dqs": _n((dqs or 0.0) / 100.0, 0.0, 1.0),
        "dte": _n(min(int(ec.get("dte") or 10), 10) / 10.0, 0.0, 1.0),
        "blocks_ok": _n((cap.get("structure_blocks_ok") or 0) / 5.0, 0.0, 1.0),
        "minute": _minute_of_day(ts),
    }


class OcAnnConfirm:
    """Fit on a fold's TRAIN records: each is {feat: dict, win: bool,
    r_multiple: float, direction: str}."""

    def __init__(self, cfg: dict | None = None):
        self.c = {**DEFAULT_ANN_CFG, **(cfg or {})}
        self.model: OnlineLogit | None = None
        self.iso: Isotonic | None = None
        self.threshold: float = self.c["threshold_fixed"]
        self.status = "UNFIT"
        self.n_train = 0
        self.train_base = None

    def fit(self, train: list[dict]) -> "OcAnnConfirm":
        self.n_train = len(train)
        if self.n_train < self.c["min_train"]:
            self.status = "INSUFFICIENT"
            return self
        rng = random.Random(self.c["seed"])
        base = sum(1 for t in train if t["win"]) / self.n_train
        self.train_base = round(base, 4)
        m = OnlineLogit(base_rate=max(0.05, min(0.95, base)))
        rows = [(t["feat"], 1 if t["win"] else 0) for t in train]
        for _ in range(self.c["epochs"]):
            order = list(range(len(rows)))
            rng.shuffle(order)
            for j in order:
                x, y = rows[j]
                m.update(x, y, lr=self.c["lr"])
        self.model = m
        self.iso = Isotonic().fit([(m.predict(x), y) for x, y in rows])

        if self.c["threshold_mode"] == "train_expectancy":
            best_thr, best_e = self.c["threshold_fixed"], -9.0
            for thr in self.c["threshold_grid"]:
                kept = [t for t in train if self._p(t["feat"]) >= thr]
                if len(kept) < self.c["min_keep_train"]:
                    continue
                e = sum(t["r_multiple"] for t in kept) / len(kept)
                if e > best_e:
                    best_e, best_thr = e, thr
            self.threshold = best_thr
        self.status = "OK"
        return self

    def _p(self, feat: dict) -> float:
        raw = self.model.predict(feat)
        return self.iso.predict(raw) if self.iso else raw

    def p_win(self, rec: dict) -> float:
        if self.status != "OK" or self.model is None:
            return 1.0                    # INSUFFICIENT -> never filters
        return self._p(rec["feat"])

    def confirm(self, rec: dict) -> bool:
        return self.p_win(rec) >= self.threshold

    def filter(self, recs: list[dict]) -> list[dict]:
        return [dict(r, ann_p_win=round(self.p_win(r), 4)) for r in recs if self.confirm(r)]

    def info(self) -> dict:
        return {
            "status": self.status, "n_train": self.n_train,
            "train_base_rate": self.train_base, "threshold": self.threshold,
            "top_weights": (sorted(self.model.w.items(), key=lambda kv: -abs(kv[1]))[:8]
                            if self.model else []),
        }
