"""Next-candle models. M1 empirical-Bayes table, M2 gradient boosting + isotonic calibration, B1 tiny logistic baseline."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

PRIOR = 50.0


class EBTable:
    """P(up | pattern, level context, trend) shrunk hierarchically: full key -> (pattern, trend) -> base rate."""
    def __init__(self, prior: float = PRIOR): self.prior = prior

    @staticmethod
    def _k1(f): return list(zip(f["dom"].values, f["trend"].values))

    @staticmethod
    def _k2(f): return list(zip(f["dom"].values, f["near_fam"].values, f["near_side"].values, f["near_zone"].values, f["trend"].values))

    def fit(self, f: pd.DataFrame, y: np.ndarray):
        self.base = float(np.mean(y)); self.c1, self.c2 = {}, {}
        for k, yy in zip(self._k1(f), y): a = self.c1.setdefault(k, [0, 0]); a[0] += yy; a[1] += 1
        for k, yy in zip(self._k2(f), y): a = self.c2.setdefault(k, [0, 0]); a[0] += yy; a[1] += 1
        return self

    def predict(self, f: pd.DataFrame) -> np.ndarray:
        out = np.empty(len(f))
        for i, (k1, k2) in enumerate(zip(self._k1(f), self._k2(f))):
            s1, n1 = self.c1.get(k1, (0, 0)); p1 = (s1 + self.prior * self.base) / (n1 + self.prior)
            s2, n2 = self.c2.get(k2, (0, 0)); out[i] = (s2 + self.prior * p1) / (n2 + self.prior)
        return out


class GBM:
    """HistGradientBoosting on the feature columns, probabilities calibrated by isotonic regression fitted on the LAST 20% (chronological) of the training window."""
    def __init__(self, cols, cal_frac: float = 0.2, seed: int = 0):
        self.cols, self.cal_frac, self.seed = cols, cal_frac, seed

    def fit(self, f: pd.DataFrame, y: np.ndarray):
        X = f[self.cols].astype(float).values; k = int(len(X) * (1 - self.cal_frac))
        self.m = HistGradientBoostingClassifier(max_depth=4, learning_rate=0.05, max_iter=200, l2_regularization=1.0, min_samples_leaf=200, early_stopping=False, random_state=self.seed)
        self.m.fit(X[:k], y[:k]); raw = self.m.predict_proba(X[k:])[:, 1]
        self.iso = IsotonicRegression(out_of_bounds="clip", y_min=0.01, y_max=0.99).fit(raw, y[k:])
        self.cal_p = self.iso.predict(raw); return self

    def predict(self, f: pd.DataFrame) -> np.ndarray:
        return self.iso.predict(self.m.predict_proba(f[self.cols].astype(float).values)[:, 1])


class Baseline:
    """B1: logistic regression on the last two bar returns and the time-of-day bucket only."""
    def _x(self, f):
        b = (f["tod"].values // 30).clip(0, 12); oh = np.eye(13)[b]
        return np.column_stack([np.nan_to_num(f["ret1_atr"].values), np.nan_to_num(f["ret2_atr"].values), oh])

    def fit(self, f, y): self.m = LogisticRegression(C=1.0, max_iter=300).fit(self._x(f), y); return self
    def predict(self, f): return self.m.predict_proba(self._x(f))[:, 1]
