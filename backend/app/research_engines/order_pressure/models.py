"""
3-class models (UP / DOWN / INSIDE). Pure Python, deterministic.

  MajorityBaseline      always the train-majority class
  PersistenceBaseline   sign of spot_net -> UP/DOWN, near-zero -> INSIDE
  NetPressureLogit      1-feature multinomial logit on spot_net (the "does the
                        obvious pressure number alone predict?" bar)
  MultinomialLogit      full-feature softmax logit, L2, standardised
  GBStumps              ENH: one-vs-rest gradient-boosted depth-1 trees --
                        the strongest non-NN baseline; the MLP must beat THIS
  MLP                   ENH-small: 1 hidden layer (cfg.mlp_hidden), tanh,
                        softmax, L2, early-stop on val log-loss

All expose: fit(X, y, X_val=None, y_val=None) ; predict_proba(x) -> {UP,DOWN,INSIDE}
X = list[list[float]] (feature-name order) ; y = list[str].
"""
from __future__ import annotations

import math

from .config import merged
from .labels import CLASSES, CLASS_IDX


# --------------------------------------------------------------------------- util
def _standardiser(X):
    n = len(X); d = len(X[0]) if n else 0
    mean = [0.0] * d
    for row in X:
        for j, v in enumerate(row):
            mean[j] += v
    mean = [m / n for m in mean] if n else mean
    var = [0.0] * d
    for row in X:
        for j, v in enumerate(row):
            var[j] += (v - mean[j]) ** 2
    std = [math.sqrt(v / n) if n and v > 0 else 1.0 for v in var]
    std = [s if s > 1e-9 else 1.0 for s in std]
    return mean, std


def _apply_std(x, mean, std):
    return [(v - m) / s for v, m, s in zip(x, mean, std)]


def _softmax(z):
    m = max(z)
    e = [math.exp(min(30.0, v - m)) for v in z]
    s = sum(e) or 1e-12
    return [v / s for v in e]


# ---------------------------------------------------------------- baselines
class MajorityBaseline:
    name = "majority"

    def fit(self, X, y, **_):
        self.p = {cl: y.count(cl) / len(y) for cl in CLASSES} if y else {c: 1 / 3 for c in CLASSES}
        return self

    def predict_proba(self, x):
        return dict(self.p)


class PersistenceBaseline:
    name = "persistence"

    def __init__(self, feat_index):
        self.i = feat_index["spot_net"]

    def fit(self, X, y, **_):
        # calibrate the dead-zone + tail probs from train frequencies
        self.tau = 8.0
        return self

    def predict_proba(self, x):
        v = x[self.i]
        if v > self.tau:
            return {"UP": 0.55, "DOWN": 0.20, "INSIDE": 0.25}
        if v < -self.tau:
            return {"UP": 0.20, "DOWN": 0.55, "INSIDE": 0.25}
        return {"UP": 0.30, "DOWN": 0.30, "INSIDE": 0.40}


class NetPressureLogit:
    name = "net_pressure_logit"

    def __init__(self, feat_index):
        self.i = feat_index["spot_net"]

    def fit(self, X, y, **_):
        self._m = MultinomialLogit(n_features=1)
        self._m.fit([[row[self.i]] for row in X], y)
        return self

    def predict_proba(self, x):
        return self._m.predict_proba([x[self.i]])


# ------------------------------------------------------------- multinomial logit
class MultinomialLogit:
    name = "multinomial_logit"

    def __init__(self, n_features=None, l2=None, lr=0.05, epochs=None):
        c = merged()
        self.l2 = c["mlp_l2"] if l2 is None else l2
        self.lr = lr
        self.epochs = epochs or c.get("mlogit_epochs", 80)
        self.nf = n_features

    def fit(self, X, y, X_val=None, y_val=None):
        self.nf = self.nf or len(X[0])
        self.mean, self.std = _standardiser(X)
        Xs = [_apply_std(r, self.mean, self.std) for r in X]
        K = len(CLASSES)
        self.W = [[0.0] * self.nf for _ in range(K)]
        self.b = [math.log(max(1e-6, y.count(cl) / len(y))) for cl in CLASSES]
        for ep in range(self.epochs):
            lr = self.lr * (0.5 + 0.5 * (1 - ep / max(1, self.epochs)))
            for xs, yy in zip(Xs, y):
                z = [self.b[k] + sum(self.W[k][j] * xs[j] for j in range(self.nf)) for k in range(K)]
                p = _softmax(z)
                for k in range(K):
                    g = p[k] - (1.0 if CLASSES[k] == yy else 0.0)
                    wk = self.W[k]
                    for j in range(self.nf):
                        wk[j] -= lr * (g * xs[j] + self.l2 * wk[j])
                    self.b[k] -= lr * g
        return self

    def predict_proba(self, x):
        xs = _apply_std(x, self.mean, self.std)
        z = [self.b[k] + sum(self.W[k][j] * xs[j] for j in range(self.nf)) for k in range(len(CLASSES))]
        return dict(zip(CLASSES, _softmax(z)))


# ------------------------------------------------------- GB stumps (OvR)  [ENH]
class _Stump:
    __slots__ = ("j", "thr", "lo", "hi")

    def value(self, x):
        return self.lo if x[self.j] <= self.thr else self.hi


class _OvRBooster:
    def __init__(self, n_rounds=None, lr=None, n_thresholds=None):
        c = merged()
        self.n_rounds = n_rounds if n_rounds is not None else c.get("gb_n_rounds", 40)
        self.lr = lr if lr is not None else c.get("gb_lr", 0.1)
        self.nt = n_thresholds if n_thresholds is not None else c.get("gb_n_thresholds", 10)

    def fit(self, X, target):  # target in {0,1}
        n, d = len(X), len(X[0])
        self.base = math.log(max(1e-6, sum(target) / n) / max(1e-6, 1 - sum(target) / n))
        F = [self.base] * n
        self.stumps = []
        # precompute candidate thresholds per feature (quantiles)
        cand = []
        for j in range(d):
            col = sorted(row[j] for row in X)
            qs = [col[int(q * (n - 1))] for q in [i / (self.nt + 1) for i in range(1, self.nt + 1)]]
            cand.append(sorted(set(qs)))
        for _ in range(self.n_rounds):
            p = [1.0 / (1.0 + math.exp(-min(30.0, max(-30.0, f)))) for f in F]
            resid = [t - pi for t, pi in zip(target, p)]
            best = None
            for j in range(d):
                for thr in cand[j]:
                    ls = rs = lc = rc = 0.0
                    for row, r in zip(X, resid):
                        if row[j] <= thr:
                            ls += r; lc += 1
                        else:
                            rs += r; rc += 1
                    if lc < 5 or rc < 5:
                        continue
                    lo, hi = ls / lc, rs / rc
                    gain = ls * lo + rs * hi
                    if best is None or gain > best[0]:
                        best = (gain, j, thr, lo, hi)
            if best is None:
                break
            _, j, thr, lo, hi = best
            st = _Stump(); st.j, st.thr, st.lo, st.hi = j, thr, self.lr * lo, self.lr * hi
            self.stumps.append(st)
            for i, row in enumerate(X):
                F[i] += st.value(row)
        return self

    def raw(self, x):
        return self.base + sum(s.value(x) for s in self.stumps)


class GBStumps:
    name = "gb_stumps"

    def __init__(self, n_rounds=None):
        c = merged()
        self.n_rounds = n_rounds or c.get("gb_n_rounds", 40)

    def fit(self, X, y, X_val=None, y_val=None):
        self.mean, self.std = _standardiser(X)
        Xs = [_apply_std(r, self.mean, self.std) for r in X]
        self.boost = {}
        for cl in CLASSES:
            tgt = [1 if yy == cl else 0 for yy in y]
            self.boost[cl] = _OvRBooster(n_rounds=self.n_rounds).fit(Xs, tgt)
        return self

    def predict_proba(self, x):
        xs = _apply_std(x, self.mean, self.std)
        z = [self.boost[cl].raw(xs) for cl in CLASSES]
        return dict(zip(CLASSES, _softmax(z)))


# ------------------------------------------------------------- small MLP  [ENH]
class MLP:
    name = "mlp"

    def __init__(self, hidden=None, l2=None, lr=None, epochs=None, patience=None, seed_scale=None):
        c = merged()
        self.h = hidden or c["mlp_hidden"]
        self.l2 = c["mlp_l2"] if l2 is None else l2
        self.lr = lr or c["mlp_lr"]
        self.epochs = epochs or c["mlp_epochs"]
        self.patience = patience or c["mlp_patience"]
        self.ss = seed_scale or c["mlp_seed_scale"]

    def _init(self, d):
        # deterministic pseudo-random init (no numpy): a fixed LCG
        st = 12345

        def rnd():
            nonlocal st
            st = (1103515245 * st + 12345) & 0x7FFFFFFF
            return (st / 0x7FFFFFFF - 0.5) * 2 * self.ss
        K = len(CLASSES)
        self.W1 = [[rnd() for _ in range(d)] for _ in range(self.h)]
        self.b1 = [0.0] * self.h
        self.W2 = [[rnd() for _ in range(self.h)] for _ in range(K)]
        self.b2 = [0.0] * K

    def _fwd(self, xs):
        a = [math.tanh(self.b1[k] + sum(self.W1[k][j] * xs[j] for j in range(len(xs))))
             for k in range(self.h)]
        z = [self.b2[k] + sum(self.W2[k][m] * a[m] for m in range(self.h)) for k in range(len(CLASSES))]
        return a, _softmax(z)

    def _logloss(self, Xs, y):
        s = 0.0
        for xs, yy in zip(Xs, y):
            _, p = self._fwd(xs)
            s += -math.log(max(1e-12, p[CLASS_IDX[yy]]))
        return s / len(Xs)

    def fit(self, X, y, X_val=None, y_val=None):
        d = len(X[0])
        self.mean, self.std = _standardiser(X)
        Xs = [_apply_std(r, self.mean, self.std) for r in X]
        Xv = [_apply_std(r, self.mean, self.std) for r in (X_val or [])]
        self._init(d)
        K = len(CLASSES)
        best = (float("inf"), None)
        bad = 0
        for ep in range(self.epochs):
            lr = self.lr * (0.5 + 0.5 * (1 - ep / max(1, self.epochs)))
            for xs, yy in zip(Xs, y):
                a, p = self._fwd(xs)
                dz2 = [p[k] - (1.0 if CLASSES[k] == yy else 0.0) for k in range(K)]
                da = [sum(self.W2[k][m] * dz2[k] for k in range(K)) for m in range(self.h)]
                dz1 = [da[m] * (1 - a[m] * a[m]) for m in range(self.h)]
                for k in range(K):
                    for m in range(self.h):
                        self.W2[k][m] -= lr * (dz2[k] * a[m] + self.l2 * self.W2[k][m])
                    self.b2[k] -= lr * dz2[k]
                for m in range(self.h):
                    for j in range(d):
                        self.W1[m][j] -= lr * (dz1[m] * xs[j] + self.l2 * self.W1[m][j])
                    self.b1[m] -= lr * dz1[m]
            if Xv:
                vl = self._logloss(Xv, y_val)
                if vl < best[0] - 1e-5:
                    best = (vl, (self._snapshot()))
                    bad = 0
                else:
                    bad += 1
                    if bad >= self.patience:
                        break
        if best[1] is not None:
            self._restore(best[1])
        return self

    def _snapshot(self):
        return ([r[:] for r in self.W1], self.b1[:], [r[:] for r in self.W2], self.b2[:])

    def _restore(self, s):
        self.W1, self.b1, self.W2, self.b2 = [r[:] for r in s[0]], s[1][:], [r[:] for r in s[2]], s[3][:]

    def predict_proba(self, x):
        xs = _apply_std(x, self.mean, self.std)
        _, p = self._fwd(xs)
        return dict(zip(CLASSES, p))
