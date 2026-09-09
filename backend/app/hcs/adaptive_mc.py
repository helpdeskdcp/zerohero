"""
HCS adaptive model -- Tier A: multinomial (softmax) logit + isotonic-calibrated
P(win) + expected-R head.  SHADOW / ADVISORY.  Dependency-free, deterministic.

This EXTENDS `app/hcs/adaptive.py` (the single-sigmoid v1 unit), it does not
replace it:
  * v1 stays the incumbent comparator.
  * feature vector is v1's `adaptive.features()` verbatim (with mtf_alignment
    rescaled to [-1,1] and an ev_r estimate injected -- both documented below).
  * trains on the SAME `scalp_signals` rows the closed-form calibration uses.

Three heads, one shared linear feature space:
  1. 3-class softmax     P(UP) / P(DOWN) / P(NO_MOVE)   -- labels from hcs/labels.py
  2. binary win logit    P(win)  (WIN vs LOSS only)     -- reuses adaptive.OnlineLogit
     -> isotonic (PAV) recalibration layer, fit TRAIN-only per walk-forward fold
  3. ridge regression    E[R]  (winsorised r_multiple)

Outputs are SHADOW: surfaced in the HCS payload / dashboard, compared side-by-
side against `calibrated_probability`, and NEVER fed into decide_from_context,
the runner, confidence, or any gate. `live_trading` stays false.

Sample reality (2026-09): ~80 resolved WIN/LOSS + 12 FLAT over 6 sessions, one
dominant regime. Heavy L2 + base-rate priors + few deterministic epochs make
this a shrinkage estimator, appropriate for that volume. A hidden layer / GBT /
per-regime models are Tier B/C and gated on sample size -- see
`adaptive_model_design_review.md` sections E and H.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3

from . import adaptive as _adp
from . import labels as _lab

# DB path honours TEST_DATABASE_URL / CHANAKYA_DB_PATH so tests never even
# read the live data/chanakya.db (reads here are mode=ro regardless).
from app.db import _resolve_db_path as _resolve_db_path  # noqa: E402
_DB = _resolve_db_path()
_STATE = os.path.join(os.path.dirname(_DB), "hcs_adaptive_mc_model.json")

MODEL_VERSION = "hcs-mc-logit-v1"
_LR = 0.05
_L2 = 3e-3                  # a touch heavier than v1 (3 heads, same tiny sample)
_EPOCHS = 40
_MIN_ROWS = 30             # matches v1: enough to fit a shrinkage model at all
_MIN_TRAIN_SESSIONS = 3    # expanding-window walk-forward minimum
_RANK_P_UP = 0.60          # "model says UP" threshold for the trading metric


# --------------------------------------------------------------------------- #
# feature adapter -- reuse v1's features(), fix two scale issues on the way in
# --------------------------------------------------------------------------- #
def _row_for_features(row: dict) -> dict:
    r = dict(row)
    mtf = r.get("mtf_alignment")
    try:
        m = float(mtf)
        if abs(m) > 1.5:                      # scalp_signals stores ~[-100,100]
            r["mtf_alignment"] = m / 100.0
    except (TypeError, ValueError):
        pass
    # ev_r is null on most scalp_signals rows; reconstruct from ev / entry-risk
    if r.get("ev_r") in (None, "") and r.get("ev") is not None:
        risk = _lab._risk(r)
        try:
            if risk:
                r["ev_r"] = float(r["ev"]) / risk
        except (TypeError, ValueError):
            pass
    return r


def features(row: dict) -> dict:
    return _adp.features(_row_for_features(row))


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
def _softmax(zs: dict) -> dict:
    mx = max(zs.values())
    ex = {k: math.exp(min(30.0, v - mx)) for k, v in zs.items()}
    s = sum(ex.values()) or 1e-12
    return {k: v / s for k, v in ex.items()}


class MultinomialLogit:
    """C independent weight vectors + softmax. Over-parameterised on purpose --
    L2 keeps it identifiable; symmetric and simplest to reason about."""

    def __init__(self, priors: dict | None = None):
        self.classes = list(_lab.CLASSES)
        self.w = {c: {} for c in self.classes}
        pr = priors or {c: 1.0 / len(self.classes) for c in self.classes}
        self.b = {c: math.log(max(1e-6, min(1 - 1e-6, pr.get(c, 1e-6)))) for c in self.classes}
        self.n_seen = 0
        self.version = MODEL_VERSION

    def predict(self, x: dict) -> dict:
        z = {c: self.b[c] + sum(self.w[c].get(k, 0.0) * v for k, v in x.items())
             for c in self.classes}
        return _softmax(z)

    def update(self, x: dict, y_cls: str, lr: float = _LR):
        p = self.predict(x)
        for c in self.classes:
            g = p[c] - (1.0 if c == y_cls else 0.0)
            wc = self.w[c]
            for k, v in x.items():
                wc[k] = wc.get(k, 0.0) - lr * (g * v + _L2 * wc.get(k, 0.0))
            self.b[c] -= lr * g
        self.n_seen += 1

    def to_dict(self) -> dict:
        return {"version": self.version, "n_seen": self.n_seen,
                "b": {c: round(v, 6) for c, v in self.b.items()},
                "w": {c: {k: round(v, 6) for k, v in sorted(d.items())}
                      for c, d in self.w.items()}}


class Ridge:
    """Linear E[R] head. SGD on squared error with L2. Deterministic."""

    def __init__(self, mean: float = 0.0):
        self.w: dict = {}
        self.b = float(mean)
        self.n_seen = 0

    def predict(self, x: dict) -> float:
        return self.b + sum(self.w.get(k, 0.0) * v for k, v in x.items())

    def update(self, x: dict, y: float, lr: float = _LR):
        e = self.predict(x) - y
        for k, v in x.items():
            self.w[k] = self.w.get(k, 0.0) - lr * (e * v + _L2 * self.w.get(k, 0.0))
        self.b -= lr * e
        self.n_seen += 1


class Isotonic:
    """Pool-adjacent-violators monotone recalibration for P(win). Fit on
    (raw_prob, y) from the TRAIN fold only; apply to raw probs at score time."""

    def __init__(self):
        self.xs: list[float] = []            # block right-edges (sorted)
        self.ys: list[float] = []            # block values (non-decreasing)

    def fit(self, pairs) -> "Isotonic":
        pts = sorted(((float(p), float(y)) for p, y in pairs if p is not None),
                     key=lambda t: t[0])
        if not pts:
            self.xs, self.ys = [], []
            return self
        blocks = [[p, y, 1] for p, y in pts]        # [x_right, mean_y, weight]
        i = 0
        while i < len(blocks) - 1:
            if blocks[i][1] <= blocks[i + 1][1] + 1e-12:
                i += 1
                continue
            x_r, y_m, w = blocks[i + 1]
            w0 = blocks[i][2]
            blocks[i][1] = (blocks[i][1] * w0 + y_m * w) / (w0 + w)
            blocks[i][2] = w0 + w
            blocks[i][0] = x_r
            del blocks[i + 1]
            if i > 0:
                i -= 1
        self.xs = [b[0] for b in blocks]
        self.ys = [b[1] for b in blocks]
        return self

    def predict(self, p: float) -> float:
        if not self.xs or p is None:
            return p if p is not None else 0.5
        for x_r, y in zip(self.xs, self.ys):
            if p <= x_r + 1e-12:
                return y
        return self.ys[-1]


class Bundle:
    """One fitted Tier-A model: the 3 heads + the isotonic layer + metadata."""

    def __init__(self, mc: MultinomialLogit, win: "_adp.OnlineLogit",
                 iso: Isotonic, rr: Ridge, n_rows: int, through: str | None):
        self.mc, self.win, self.iso, self.rr = mc, win, iso, rr
        self.n_rows = n_rows
        self.trained_through = through
        self.version = MODEL_VERSION

    def score(self, row: dict) -> dict:
        x = features(row)
        p3 = self.mc.predict(x)
        p_win_raw = self.win.predict(x)
        p_win_cal = round(self.iso.predict(p_win_raw), 4)
        exp_r = round(self.rr.predict(x), 4)
        return {
            "p_up": round(p3["UP"], 4), "p_down": round(p3["DOWN"], 4),
            "p_no_move": round(p3["NO_MOVE"], 4),
            "p_win_raw": round(p_win_raw, 4), "p_win_cal": p_win_cal,
            "exp_r": exp_r,
            "setup_rank_score": round(p_win_cal * max(0.0, exp_r), 4),
        }

    def to_dict(self) -> dict:
        return {"version": self.version, "n_rows": self.n_rows,
                "trained_through": self.trained_through,
                "mc": self.mc.to_dict(), "win": self.win.to_dict(),
                "isotonic": {"xs": [round(v, 6) for v in self.iso.xs],
                             "ys": [round(v, 6) for v in self.iso.ys]},
                "ridge": {"b": round(self.rr.b, 6),
                          "w": {k: round(v, 6) for k, v in sorted(self.rr.w.items())}}}


# --------------------------------------------------------------------------- #
# data
# --------------------------------------------------------------------------- #
_COLS = ("signal_score, momentum, mtf_alignment, regime, signal_type, tod_bucket, "
         "rr, ev, ev_r, confidence, probability, entry, stop_loss, points, r_multiple, "
         "mfe, mae, exit_reason, outcome, created_ts, session_date")


def _resolved_rows() -> list[dict]:
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            f"SELECT {_COLS} FROM scalp_signals "
            "WHERE source='LIVE' AND status='CLOSED' AND outcome IN ('WIN','LOSS','FLAT') "
            "AND signal_score IS NOT NULL ORDER BY created_ts, id").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _priors(rows) -> dict:
    n = len(rows) or 1
    cnt = {c: 0 for c in _lab.CLASSES}
    for r in rows:
        y = _lab.three_class(r)
        if y:
            cnt[y] += 1
    tot = sum(cnt.values()) or 1
    return {c: max(1e-3, cnt[c] / tot) for c in _lab.CLASSES}


def _train(rows) -> Bundle:
    """Deterministic: fixed row order, fixed epoch count, gentle lr decay."""
    lab = [(r, _lab.label_row(r)) for r in rows]
    three = [(features(r), L["y3"]) for r, L in lab if L["y3"] is not None]
    winrows = [(features(r), L["y_win"]) for r, L in lab if L["y_win"] is not None]
    qrows = [(features(r), L["yq"]) for r, L in lab if L["yq"] is not None]

    mc = MultinomialLogit(_priors(rows))
    base = (sum(1 for _, y in winrows if y == 1) / len(winrows)) if winrows else 0.5
    win = _adp.OnlineLogit(base_rate=base)
    qmean = (sum(y for _, y in qrows) / len(qrows)) if qrows else 0.0
    rr = Ridge(mean=qmean)

    for ep in range(_EPOCHS):
        lr = _LR * (0.5 + 0.5 * (1 - ep / max(1, _EPOCHS)))
        for x, y in three:
            mc.update(x, y, lr)
        for x, y in winrows:
            win.update(x, y, lr)
        for x, y in qrows:
            rr.update(x, y, lr)

    iso = Isotonic().fit([(win.predict(x), y) for x, y in winrows])
    mc.n_seen = len(three)
    through = rows[-1]["created_ts"] if rows else None
    return Bundle(mc, win, iso, rr, len(rows), through)


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #
def _mc_logloss(pairs) -> float | None:
    if not pairs:
        return None
    s = 0.0
    for p3, y in pairs:
        s += -math.log(max(1e-12, p3.get(y, 1e-12)))
    return round(s / len(pairs), 4)


def _class_brier(pairs) -> dict:
    out = {}
    for c in _lab.CLASSES:
        v = [(p3.get(c, 0.0) - (1.0 if y == c else 0.0)) ** 2 for p3, y in pairs]
        out[c] = round(sum(v) / len(v), 4) if v else None
    return out


def _macro_f1(pairs) -> float | None:
    if not pairs:
        return None
    f1s = []
    for c in _lab.CLASSES:
        tp = sum(1 for p3, y in pairs if max(p3, key=p3.get) == c and y == c)
        fp = sum(1 for p3, y in pairs if max(p3, key=p3.get) == c and y != c)
        fn = sum(1 for p3, y in pairs if max(p3, key=p3.get) != c and y == c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if (prec + rec) else 0.0)
    return round(sum(f1s) / len(f1s), 4)


# --------------------------------------------------------------------------- #
# walk-forward report  (expanding window, by session)
# --------------------------------------------------------------------------- #
def refit_and_report(persist: bool = False) -> dict:
    from ..backtest import calibration as _cal

    rows = _resolved_rows()
    n = len(rows)
    wl = [r for r in rows if str(r["outcome"]).upper() in ("WIN", "LOSS")]
    if n < _MIN_ROWS:
        return {"available": False, "n": n, "n_win_loss": len(wl),
                "min_rows": _MIN_ROWS,
                "reason": f"< {_MIN_ROWS} resolved outcomes -- not enough to fit Tier A"}

    sessions = sorted({r["session_date"] for r in rows})
    folds = []
    mc_pairs, cal_pairs, base_pairs, rank_rs, all_rs = [], [], [], [], []
    for i in range(_MIN_TRAIN_SESSIONS, len(sessions)):
        tr_s, te_s = set(sessions[:i]), sessions[i]
        tr = [r for r in rows if r["session_date"] in tr_s]
        te = [r for r in rows if r["session_date"] == te_s]
        if len(tr) < _MIN_ROWS or not te:
            continue
        b = _train(tr)
        # baseline: closed-form logistic, fitted TRAIN-only, same rows
        c_tr = _cal.fit([{"score": r["signal_score"], "regime": r["regime"],
                          "signal_type": r["signal_type"],
                          "win": str(r["outcome"]).upper() == "WIN"}
                         for r in tr if str(r["outcome"]).upper() in ("WIN", "LOSS")])
        f_mc, f_cal, f_base = [], [], []
        for r in te:
            y3 = _lab.three_class(r)
            s = b.score(r)
            p3 = {"UP": s["p_up"], "DOWN": s["p_down"], "NO_MOVE": s["p_no_move"]}
            if y3 is not None:
                f_mc.append((p3, y3)); mc_pairs.append((p3, y3))
            yw = _lab.y_win(r)
            if yw is not None:
                f_cal.append((s["p_win_cal"], yw)); cal_pairs.append((s["p_win_cal"], yw))
                pbase = _cal.predict(c_tr, r["signal_score"], regime=r["regime"] or "?",
                                     signal_type=r["signal_type"] or "?")
                f_base.append((pbase, yw)); base_pairs.append((pbase, yw))
            qr = _lab.quality_target(r)
            if qr is not None:
                all_rs.append(qr)
                if s["p_up"] >= _RANK_P_UP:
                    rank_rs.append(qr)
        folds.append({
            "test_session": te_s, "n_train": len(tr), "n_test": len(te),
            "mc_logloss": _mc_logloss(f_mc),
            "cal_brier": _cal.reliability_curve(f_cal)["brier"] if f_cal else None,
            "baseline_brier": _cal.reliability_curve(f_base)["brier"] if f_base else None,
        })

    agg_cal = _cal.reliability_curve(cal_pairs) if cal_pairs else {"brier": None, "ece": None}
    agg_base = _cal.reliability_curve(base_pairs) if base_pairs else {"brier": None, "ece": None}
    e_r_rank = round(sum(rank_rs) / len(rank_rs), 3) if rank_rs else None
    e_r_all = round(sum(all_rs) / len(all_rs), 3) if all_rs else None

    # full-history fit -> served model + optional persist
    full = _train(rows)
    if persist:
        try:
            with open(_STATE, "w") as fh:
                json.dump({**full.to_dict(), "generated": "refit_and_report"}, fh, indent=2)
        except OSError:
            pass

    winner = None
    if agg_cal["brier"] is not None and agg_base["brier"] is not None:
        d = agg_base["brier"] - agg_cal["brier"]
        winner = ("adaptive_mc" if d > 1e-4 else
                  "baseline_logistic" if d < -1e-4 else "tie")

    return {
        "available": True, "version": MODEL_VERSION,
        "n_rows": n, "n_win_loss": len(wl), "sessions": sessions,
        "class_counts": {c: sum(1 for r in rows if _lab.three_class(r) == c)
                         for c in _lab.CLASSES},
        "walk_forward": {
            "scheme": "expanding window by session; min_train_sessions="
                      f"{_MIN_TRAIN_SESSIONS}; isotonic + baseline fitted TRAIN-only per fold",
            "folds": folds,
            "aggregate": {
                "mc_logloss": _mc_logloss(mc_pairs),
                "mc_class_brier": _class_brier(mc_pairs),
                "mc_macro_f1": _macro_f1(mc_pairs),
                "p_win_cal": {"brier": agg_cal["brier"], "ece": agg_cal.get("ece")},
                "baseline_logistic": {"brier": agg_base["brier"], "ece": agg_base.get("ece")},
                "winner_on_p_win": winner,
                "expected_R": {"threshold_p_up": _RANK_P_UP,
                               "subset_mean_R": e_r_rank, "overall_mean_R": e_r_all,
                               "n_subset": len(rank_rs), "n_all": len(all_rs)},
            },
        },
        "served_model": {
            "trained_rows": full.n_rows, "trained_through": full.trained_through,
            "mc_bias": {c: round(v, 4) for c, v in full.mc.b.items()},
            "mc_top_weights": _top_mc_weights(full.mc),
            "win_top_weights": [{"feature": k, "weight": round(v, 4)}
                                for k, v in sorted(full.win.w.items(),
                                                   key=lambda kv: -abs(kv[1]))[:8]],
            "isotonic_points": list(zip([round(v, 3) for v in full.iso.xs],
                                        [round(v, 3) for v in full.iso.ys])),
        },
        "verdict": (
            "SHADOW / ADVISORY -- Tier A multinomial logit. Extends adaptive.py; "
            "NOT wired into any gate. Trained on "
            f"{n} resolved rows over {len(sessions)} sessions / one dominant regime. "
            "NOT VALIDATED -- expanding-window folds are 1-3 sessions each. Re-fits "
            "whenever new outcomes resolve. GBT / hidden-layer / per-regime models "
            "remain gated on sample size (design review sections E, H)."
        ),
        "persisted": bool(persist),
    }


def _top_mc_weights(mc: MultinomialLogit, k: int = 6) -> dict:
    out = {}
    for c in mc.classes:
        out[c] = [{"feature": f, "weight": round(w, 4)}
                  for f, w in sorted(mc.w[c].items(), key=lambda kv: -abs(kv[1]))[:k]]
    return out


# --------------------------------------------------------------------------- #
# served inference  (cache keyed on resolved-row count, like adaptive.py)
# --------------------------------------------------------------------------- #
_CACHE = {"bundle": None, "rows": -1}


def _model() -> Bundle | None:
    rows = _resolved_rows()
    if len(rows) < _MIN_ROWS:
        _CACHE["rows"] = len(rows)
        return None
    if _CACHE["bundle"] is None or _CACHE["rows"] != len(rows):
        _CACHE["bundle"] = _train(rows)
        _CACHE["rows"] = len(rows)
    return _CACHE["bundle"]


def score(row: dict) -> dict:
    """Advisory Tier-A scores for one live decision row. SHADOW."""
    try:
        b = _model()
    except Exception as e:                    # never let the shadow model raise
        return {"status": f"ERROR: {type(e).__name__}: {e}"}
    if b is None:
        return {"status": "INSUFFICIENT",
                "n_trained": _CACHE["rows"] if _CACHE["rows"] > 0 else 0,
                "min_rows": _MIN_ROWS}
    out = b.score(row)
    return {"status": "OK", "model_id": MODEL_VERSION,
            "n_trained": b.n_rows, **out,
            "note": "advisory only -- not wired into decide_from_context / runner / any gate"}
