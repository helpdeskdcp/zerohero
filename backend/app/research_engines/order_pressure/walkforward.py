"""
Expanding-window walk-forward, chronological, with a purge + embargo gap.

Blocks = one per IST session date (config wf_block). For fold k:
  train  = blocks[0 : k]              (minus the val tail)
  val    = chronological tail (wf_val_frac) of the train span  -> early-stop / calib
  EMBARGO: drop the last `embargo` rows of train and the first `embargo` rows of
           the test block so the label horizon (1 bar) cannot leak across the cut
  test   = blocks[k]                  (out-of-sample)

No standardiser / calibrator / model ever sees a test row. Determinism: same
rows in -> same folds out.
"""
from __future__ import annotations

from .config import merged
from .calibrate import IsotonicMulticlass
from .metrics import classification, per_class_reliability
from .labels import CLASSES


def _session_spans(dates: list[str]) -> list[tuple[int, int]]:
    out, start = [], 0
    for i in range(1, len(dates) + 1):
        if i == len(dates) or dates[i] != dates[start]:
            out.append((start, i)); start = i
    return out


def _blocks(dates: list[str], sessions_per_block: int = 1) -> list[tuple[int, int]]:
    spans = _session_spans(dates)
    if sessions_per_block <= 1:
        return spans
    out = []
    for k in range(0, len(spans), sessions_per_block):
        chunk = spans[k:k + sessions_per_block]
        out.append((chunk[0][0], chunk[-1][1]))
    return out


def run(X: list[list[float]], y: list[str], dates: list[str], model_factory,
        *, cfg: dict | None = None, embargo: int | None = None, calibrate: bool = True) -> dict:
    c = cfg or merged()
    embargo = c.get("wf_embargo_bars", 2) if embargo is None else embargo
    blocks = _blocks(dates, c.get("wf_block_sessions", 1))
    if len(blocks) < c["wf_min_train_blocks"] + 1:
        return {"status": "INSUFFICIENT_SAMPLE",
                "reason": f"{len(blocks)} blocks < {c['wf_min_train_blocks']+1} needed "
                          f"(block = {c.get('wf_block_sessions',1)} sessions)",
                "n_blocks": len(blocks)}

    oos: list[tuple[dict, str]] = []
    folds = []
    first_fold = max(c["wf_min_train_blocks"], len(blocks) - c.get("wf_max_folds", 14))
    for k in range(first_fold, len(blocks)):
        tr_lo, tr_hi = 0, blocks[k - 1][1]
        te_lo, te_hi = blocks[k]
        tr_hi = max(tr_lo, tr_hi - embargo)
        te_lo = min(te_hi, te_lo + embargo)
        if tr_hi - tr_lo < c["min_rows_for_fit"] or te_hi - te_lo < 10:
            continue
        vcut = tr_hi - max(20, int((tr_hi - tr_lo) * c["wf_val_frac"]))
        Xtr, ytr = X[tr_lo:vcut], y[tr_lo:vcut]
        Xva, yva = X[vcut:tr_hi], y[vcut:tr_hi]
        Xte, yte = X[te_lo:te_hi], y[te_lo:te_hi]
        if len(Xtr) < c["min_rows_for_fit"] or min(ytr.count(cl) for cl in CLASSES) < c["min_rows_per_class"]:
            continue

        m = model_factory()
        m.fit(Xtr, ytr, X_val=Xva, y_val=yva)

        cal = None
        if calibrate and Xva:
            cal = IsotonicMulticlass().fit([(m.predict_proba(x), yy) for x, yy in zip(Xva, yva)])

        fold_pairs = []
        for x, yy in zip(Xte, yte):
            p = m.predict_proba(x)
            if cal is not None:
                p = cal.apply(p)
            fold_pairs.append((p, yy))
        oos += fold_pairs
        fm = classification(fold_pairs)
        folds.append({"test_session": dates[te_lo], "n_train": len(Xtr), "n_test": len(Xte),
                      "accuracy": fm["accuracy"], "macro_f1": fm["macro_f1"],
                      "log_loss": fm["log_loss"], "brier": fm["brier"]})

    if not oos:
        return {"status": "INSUFFICIENT_SAMPLE", "reason": "no fold met the row minimums",
                "n_blocks": len(blocks)}
    agg = classification(oos)
    agg["status"] = "OK"
    agg["n_folds"] = len(folds)
    agg["folds"] = folds
    agg["reliability"] = per_class_reliability(oos)
    return agg
