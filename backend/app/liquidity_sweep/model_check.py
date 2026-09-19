"""
Phase 2 (index-first brief) -- leakage-safe JOINT feature importance.

feature_analysis.py tests each feature INDEPENDENTLY (univariate: Cohen's
d / Mann-Whitney / Cramer's V per feature). That's necessary but not
sufficient -- a real predictive combination can exist even when every
individual feature looks like noise (classic example: XOR of two features
is perfectly predictive while each feature alone has zero marginal
association). This module runs the complementary, decisive check: can ANY
model, given ALL features jointly, beat a trivial baseline on a
never-touched, chronologically-LATER holdout -- exactly the same
TRAIN -> VALIDATION -> OOS split discipline used everywhere else in this
package (see backtest._split_chronological), never a random/shuffled split
(which would itself be a look-ahead leak on a time series).

Two candidate models, both standard and unremarkable (not hand-tuned to
this data): L2-regularized logistic regression (linear combinations) and a
shallow random forest (nonlinear interactions, e.g. an XOR-like effect a
linear model would miss). Model SELECTION (which of the two to trust) is
done on VALIDATION only; OOS is touched exactly once, to report the final
number -- the same discipline `run_stage1_backtest` already follows for
threshold selection.

A model "passing" this check means it beats BOTH a majority-class baseline
AND the train-set base rate on OOS by a real margin -- a bar deliberately
set above "technically nonzero AUC," because with ~30 features and ~1,000
graded train samples, a model can fit train-set noise trivially; the whole
point of touching OOS only once is to catch that.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from .feature_analysis import CATEGORICAL_FEATURES, CONTINUOUS_FEATURES


def _design_matrix(rows: list[dict]) -> tuple[np.ndarray, list[str]]:
    """One-hot encodes categorical features, passes continuous features
    through (missing -> column median, computed on TRAIN ONLY by the
    caller to avoid leaking validation/OOS statistics into imputation)."""
    columns: list[str] = []
    data: list[list[float]] = []
    for row in rows:
        vec = []
        for name in CONTINUOUS_FEATURES:
            vec.append(row.get(name))
        data.append(vec)
    arr = np.array(data, dtype=object)
    columns.extend(CONTINUOUS_FEATURES)

    cat_columns: dict[str, set] = {name: set() for name in CATEGORICAL_FEATURES}
    for row in rows:
        for name in CATEGORICAL_FEATURES:
            v = row.get(name)
            if v is not None:
                cat_columns[name].add(str(v))
    onehot_cols = []
    onehot_data = []
    for name in CATEGORICAL_FEATURES:
        cats = sorted(cat_columns[name])
        for c in cats:
            onehot_cols.append(f"{name}={c}")
            onehot_data.append([1.0 if str(row.get(name)) == c else 0.0 for row in rows])
    columns.extend(onehot_cols)

    n = len(rows)
    full = np.zeros((n, len(columns)), dtype=float)
    for j, name in enumerate(CONTINUOUS_FEATURES):
        col = [row.get(name) for row in rows]
        vals = [v for v in col if v is not None]
        fill = float(np.median(vals)) if vals else 0.0
        for i, v in enumerate(col):
            full[i, j] = float(v) if v is not None else fill
    offset = len(CONTINUOUS_FEATURES)
    for k, col_data in enumerate(onehot_data):
        full[:, offset + k] = col_data
    return full, columns


@dataclass
class ModelCheckResult:
    n_train: int
    n_val: int
    n_oos: int
    train_base_rate: float
    val_base_rate: float
    oos_base_rate: float
    logistic_val_auc: float | None
    logistic_oos_auc: float | None
    logistic_oos_accuracy: float | None
    logistic_oos_brier: float | None
    forest_val_auc: float | None
    forest_oos_auc: float | None
    forest_oos_accuracy: float | None
    forest_oos_brier: float | None
    selected_model: str
    passes_bar: bool
    bar_rationale: str
    top_logistic_coefficients: list
    top_forest_importances: list

    def to_dict(self) -> dict:
        return asdict(self)


def _majority_baseline_accuracy(y_train, y_eval) -> float:
    majority = 1 if sum(y_train) >= len(y_train) / 2 else 0
    return float(np.mean(np.array(y_eval) == majority))


def run_leakage_safe_model_check(samples: list, *, train_frac: float = 0.5, val_frac: float = 0.25,
                                  auc_pass_margin: float = 0.07) -> dict:
    """`samples`: Stage1FeatureSample list, chronologically ordered (as
    `_walk_raw_signals_with_features` already returns them). TIMEOUT
    excluded (neither WIN nor LOSS -- same convention as feature_analysis).
    `auc_pass_margin`: how far above 0.5 OOS AUC must be to call this a real
    pass -- 0.07 is a plain, documented, round choice (AUC 0.57+), not fitted
    to this dataset's own result."""
    graded = [s for s in samples if s.outcome in ("WIN", "LOSS")]
    rows = [s.to_dict() for s in graded]
    y = [1 if r["outcome"] == "WIN" else 0 for r in rows]
    n = len(rows)
    a, b = int(n * train_frac), int(n * (train_frac + val_frac))
    train_idx, val_idx, oos_idx = list(range(a)), list(range(a, b)), list(range(b, n))

    X, columns = _design_matrix(rows)
    y_arr = np.array(y)
    X_train, X_val, X_oos = X[train_idx], X[val_idx], X[oos_idx]
    y_train, y_val, y_oos = y_arr[train_idx], y_arr[val_idx], y_arr[oos_idx]

    if len(set(y_train.tolist())) < 2 or len(train_idx) < 20:
        return {"status": "INSUFFICIENT_DATA", "n_train": len(train_idx)}

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s = scaler.transform(X_val)
    X_oos_s = scaler.transform(X_oos)

    logit = LogisticRegression(max_iter=2000, C=0.5, class_weight="balanced")
    logit.fit(X_train_s, y_train)

    forest = RandomForestClassifier(n_estimators=200, max_depth=4, min_samples_leaf=20,
                                    class_weight="balanced", random_state=0)
    forest.fit(X_train, y_train)

    def _auc(model, X_eval, y_eval, scaled=None):
        if len(set(y_eval.tolist())) < 2:
            return None
        proba = model.predict_proba(scaled if scaled is not None else X_eval)[:, 1]
        return float(roc_auc_score(y_eval, proba))

    logit_val_auc = _auc(logit, X_val, y_val, X_val_s)
    logit_oos_auc = _auc(logit, X_oos, y_oos, X_oos_s)
    forest_val_auc = _auc(forest, X_val, y_val)
    forest_oos_auc = _auc(forest, X_oos, y_oos)

    selected = "logistic" if (logit_val_auc or 0) >= (forest_val_auc or 0) else "forest"
    if selected == "logistic":
        oos_proba = logit.predict_proba(X_oos_s)[:, 1]
        oos_auc = logit_oos_auc
    else:
        oos_proba = forest.predict_proba(X_oos)[:, 1]
        oos_auc = forest_oos_auc
    oos_pred = (oos_proba >= 0.5).astype(int)
    oos_accuracy = float(np.mean(oos_pred == y_oos)) if len(y_oos) else None
    oos_brier = float(brier_score_loss(y_oos, oos_proba)) if len(set(y_oos.tolist())) >= 1 else None
    baseline_acc = _majority_baseline_accuracy(y_train, y_oos) if len(y_oos) else None

    passes = bool(oos_auc is not None and oos_auc >= 0.5 + auc_pass_margin)
    if oos_auc is None:
        rationale = "OOS split has only one class -- cannot compute AUC"
    elif passes:
        rationale = (f"{selected} OOS AUC {oos_auc:.3f} clears the 0.5+{auc_pass_margin} bar -- "
                    f"a real joint-feature effect the univariate tests missed, worth re-testing on "
                    f"a fresh period before trusting it")
    else:
        rationale = (f"{selected} OOS AUC {oos_auc:.3f} does not clear 0.5+{auc_pass_margin} "
                    f"({0.5 + auc_pass_margin:.2f}) -- no combination of these features, jointly, "
                    f"predicts WIN/LOSS on held-out data either")

    top_logit = sorted(zip(columns, logit.coef_[0].tolist()), key=lambda kv: abs(kv[1]), reverse=True)[:10]
    top_forest = sorted(zip(columns, forest.feature_importances_.tolist()), key=lambda kv: kv[1], reverse=True)[:10]

    result = ModelCheckResult(
        n_train=len(train_idx), n_val=len(val_idx), n_oos=len(oos_idx),
        train_base_rate=round(float(np.mean(y_train)), 4),
        val_base_rate=round(float(np.mean(y_val)), 4) if len(y_val) else float("nan"),
        oos_base_rate=round(float(np.mean(y_oos)), 4) if len(y_oos) else float("nan"),
        logistic_val_auc=round(logit_val_auc, 4) if logit_val_auc is not None else None,
        logistic_oos_auc=round(logit_oos_auc, 4) if logit_oos_auc is not None else None,
        logistic_oos_accuracy=round(float(np.mean((logit.predict_proba(X_oos_s)[:, 1] >= 0.5).astype(int) == y_oos)), 4) if len(y_oos) else None,
        logistic_oos_brier=round(float(brier_score_loss(y_oos, logit.predict_proba(X_oos_s)[:, 1])), 4) if len(y_oos) else None,
        forest_val_auc=round(forest_val_auc, 4) if forest_val_auc is not None else None,
        forest_oos_auc=round(forest_oos_auc, 4) if forest_oos_auc is not None else None,
        forest_oos_accuracy=round(float(np.mean((forest.predict_proba(X_oos)[:, 1] >= 0.5).astype(int) == y_oos)), 4) if len(y_oos) else None,
        forest_oos_brier=round(float(brier_score_loss(y_oos, forest.predict_proba(X_oos)[:, 1])), 4) if len(y_oos) else None,
        selected_model=selected, passes_bar=passes, bar_rationale=rationale,
        top_logistic_coefficients=[{"feature": f, "coefficient": round(c, 4)} for f, c in top_logit],
        top_forest_importances=[{"feature": f, "importance": round(i, 4)} for f, i in top_forest],
    )
    out = result.to_dict()
    out["status"] = "OK"
    out["oos_majority_baseline_accuracy"] = round(baseline_acc, 4) if baseline_acc is not None else None
    return out
