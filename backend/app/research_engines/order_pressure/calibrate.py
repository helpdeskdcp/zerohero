"""
Post-hoc calibration for the 3-class outputs.

  Isotonic      one-vs-rest PAV per class on a held-out slice, then renormalise
  Temperature   ENH: one scalar T on the val set (softmax(logits / T)); cheap,
                monotone, cannot reorder classes -- shrinks over-confidence

Reliability numbers come from metrics.classification / per_class_reliability.
"""
from __future__ import annotations

import math

from .labels import CLASSES


class _Isotonic:
    def __init__(self):
        self.xs, self.ys = [], []

    def fit(self, pairs):
        pts = sorted(((float(p), float(y)) for p, y in pairs if p is not None), key=lambda t: t[0])
        if not pts:
            return self
        blocks = [[p, y, 1] for p, y in pts]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][1] <= blocks[i + 1][1] + 1e-12:
                i += 1; continue
            xr, ym, w = blocks[i + 1]
            w0 = blocks[i][2]
            blocks[i][1] = (blocks[i][1] * w0 + ym * w) / (w0 + w)
            blocks[i][2] = w0 + w
            blocks[i][0] = xr
            del blocks[i + 1]
            i = max(0, i - 1)
        self.xs = [b[0] for b in blocks]
        self.ys = [b[1] for b in blocks]
        return self

    def predict(self, p):
        if not self.xs:
            return p
        for xr, y in zip(self.xs, self.ys):
            if p <= xr + 1e-12:
                return y
        return self.ys[-1]


class IsotonicMulticlass:
    name = "isotonic"

    def fit(self, val_pairs):
        """val_pairs: [(prob_dict, true_class)]."""
        self.iso = {}
        for cl in CLASSES:
            self.iso[cl] = _Isotonic().fit([(p.get(cl, 0.0), 1.0 if y == cl else 0.0)
                                            for p, y in val_pairs])
        return self

    def apply(self, prob: dict) -> dict:
        q = {cl: max(1e-6, self.iso[cl].predict(prob.get(cl, 0.0))) for cl in CLASSES}
        s = sum(q.values())
        return {cl: q[cl] / s for cl in CLASSES}


class Temperature:
    name = "temperature"

    def fit(self, val_logits_pairs, grid=None):
        """val_logits_pairs: [(logit_list_in_CLASSES_order, true_class)]."""
        grid = grid or [0.5, 0.7, 0.85, 1.0, 1.2, 1.5, 2.0, 3.0]
        best = (float("inf"), 1.0)
        for T in grid:
            s = 0.0
            for z, y in val_logits_pairs:
                zz = [v / T for v in z]
                m = max(zz)
                e = [math.exp(v - m) for v in zz]
                ssum = sum(e)
                idx = CLASSES.index(y)
                s += -math.log(max(1e-12, e[idx] / ssum))
            s /= max(1, len(val_logits_pairs))
            if s < best[0]:
                best = (s, T)
        self.T = best[1]
        return self

    def apply_probs(self, prob: dict) -> dict:
        z = [math.log(max(1e-9, prob.get(cl, 0.0))) / self.T for cl in CLASSES]
        m = max(z)
        e = [math.exp(v - m) for v in z]
        s = sum(e)
        return {cl: e[i] / s for i, cl in enumerate(CLASSES)}
