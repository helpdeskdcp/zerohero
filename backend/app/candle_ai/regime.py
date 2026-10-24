"""Causal volatility / market regime layer: GARCH(1,1) next-bar volatility and a 3-state Gaussian HMM with FILTERED posteriors.
Nothing here uses data after bar t when producing the value of row t: GARCH parameters are fixed (fitted on a train span) and its variance recursion runs forward; the HMM posterior is the forward algorithm, never smoothing/Viterbi."""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from scipy.special import logsumexp

from . import levels as L


def intraday_returns_bps(df: pd.DataFrame) -> np.ndarray:
    """5m return in bps; the first bar of a session uses close/open (the overnight gap is not a 5-minute return)."""
    c = df["c"].values.astype(float); o = df["o"].values.astype(float); date = L.session_date(df["t"]).values
    prev = np.roll(c, 1); first = np.r_[True, date[1:] != date[:-1]]; base = np.where(first, o, prev)
    return (c / base - 1.0) * 1e4


def hmm_features(df: pd.DataFrame) -> np.ndarray:
    h = df["h"].values.astype(float); l = df["l"].values.astype(float); c = df["c"].values.astype(float)
    return np.column_stack([intraday_returns_bps(df), np.log1p((h - l) / c * 1e4)])


class GarchRegime:
    def fit(self, df_train: pd.DataFrame):
        from arch import arch_model
        r = intraday_returns_bps(df_train); r = r[np.isfinite(r)]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); self.res = arch_model(r, mean="Zero", vol="GARCH", p=1, q=1, dist="normal", rescale=False).fit(disp="off", options={"maxiter": 200})
        self.params = self.res.params; self.sigma_med = float(np.median(self.res.conditional_volatility)); return self

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """sigma_next[t] = volatility forecast for bar t+1 from information up to t (omega + alpha*eps_t^2 + beta*sigma_t^2), fixed parameters, forward recursion."""
        om, al, be = (float(self.params[k]) for k in ("omega", "alpha[1]", "beta[1]"))
        r = np.nan_to_num(intraday_returns_bps(df)); n = len(r); s2 = np.empty(n); s2[0] = self.res.conditional_volatility.var() if False else self.sigma_med ** 2
        nxt = np.empty(n)
        for t in range(n):
            nxt[t] = om + al * r[t] ** 2 + be * s2[t]
            if t + 1 < n: s2[t + 1] = nxt[t]
        sig = np.sqrt(np.maximum(nxt, 1e-12)); return pd.DataFrame({"sigma_next": sig, "sigma_ratio": sig / self.sigma_med})


class HMMRegime:
    def __init__(self, n_states: int = 3, seed: int = 0): self.n, self.seed = n_states, seed

    def fit(self, df_train: pd.DataFrame):
        from hmmlearn.hmm import GaussianHMM
        X = hmm_features(df_train); X = X[np.isfinite(X).all(axis=1)]; self.mu, self.sd = X.mean(axis=0), X.std(axis=0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore"); self.m = GaussianHMM(self.n, covariance_type="full", n_iter=50, tol=1e-3, random_state=self.seed).fit((X - self.mu) / self.sd)
        self.order = np.argsort(self.m.means_[:, 1]); return self                                          # state 0 = calmest (smallest mean log range) .. n-1 = most turbulent

    def filtered(self, df: pd.DataFrame) -> pd.DataFrame:
        X = np.nan_to_num((hmm_features(df) - self.mu) / self.sd); logB = self.m._compute_log_likelihood(X); logA = np.log(self.m.transmat_ + 1e-300); n = len(X)
        la = np.empty((n, self.n)); la[0] = np.log(self.m.startprob_ + 1e-300) + logB[0]; la[0] -= logsumexp(la[0])
        for t in range(1, n):
            la[t] = logsumexp(la[t - 1][:, None] + logA, axis=0) + logB[t]; la[t] -= logsumexp(la[t])
        p = np.exp(la)[:, self.order]
        return pd.DataFrame({f"hmm_p{k}": p[:, k] for k in range(self.n)} | {"hmm_state": p.argmax(axis=1)})


def regime_frame(df: pd.DataFrame, garch: GarchRegime, hmm: HMMRegime) -> pd.DataFrame:
    return pd.concat([garch.transform(df), hmm.filtered(df)], axis=1)
