"""
HCS adaptive model -- a dependency-free ONLINE single-sigmoid unit
(= online logistic regression; the smallest "adaptive neural network").

SHADOW / ADVISORY. Pure Python, deterministic (zero-init weights, base-rate bias,
fixed epoch order). Trains on the same resolved AUTOSCALP outcomes the
calibration report uses, adapts on every new resolved outcome, and is compared
SIDE-BY-SIDE against the existing closed-form `app/backtest/calibration.py`
logistic. It does NOT gate trades -- the HCS A+ gate stays on the existing
`calibrated_probability`. Nothing frozen is touched; `live_trading` stays false.

Why not a hidden-layer NN: 80 resolved outcomes over 6 sessions / one regime
(see HCS_CALIBRATION_REPORT.md). A single L2-regularised sigmoid unit with a
base-rate prior is a shrinkage estimator -- appropriate for that volume; a
multi-layer net would overfit. Revisit when >=40 outcomes/regime across
>=2 regimes exist.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3

# DB path honours TEST_DATABASE_URL / CHANAKYA_DB_PATH so tests never even
# read the live data/chanakya.db (reads here are mode=ro regardless).
from app.db import _resolve_db_path as _resolve_db_path  # noqa: E402
_DB = _resolve_db_path()
_STATE = os.path.join(os.path.dirname(_DB), "hcs_adaptive_model.json")

MODEL_VERSION = "hcs-online-logit-v1"
_LR = 0.05
_L2 = 1e-3
_EPOCHS = 40            # deterministic passes over the chronological stream
_MIN_ROWS = 30

_REGIMES = ("TRENDING_UP", "TRENDING_DOWN", "TRENDING", "RANGE", "UNSTABLE")
_TYPES = ("SUPPORT_REVERSAL", "SUPPORT_BREAKDOWN", "RESISTANCE_REVERSAL", "RESISTANCE_BREAKOUT")
_TODS = ("OPEN", "MORNING", "MIDDAY", "AFTERNOON", "CLOSE")


def _sig(z):
    if z < -30:
        return 1e-13
    if z > 30:
        return 1 - 1e-13
    return 1.0 / (1.0 + math.exp(-z))


def _f(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def features(row: dict) -> dict:
    """row: a scalp_signals-shaped dict (or a live snapshot). Small, bounded set."""
    x: dict = {}
    ss = _f(row.get("signal_score"))
    x["signal_score"] = (ss / 100.0 - 0.5) if ss is not None else 0.0
    stt = _f(row.get("state_score"))
    x["state_score"] = (stt / 100.0 - 0.5) if stt is not None else x["signal_score"]
    mtf = _f(row.get("mtf_alignment"))
    x["mtf"] = max(-1.0, min(1.0, mtf)) if mtf is not None else 0.0
    mom = _f(row.get("momentum"))
    x["momentum"] = math.tanh(mom) if mom is not None else 0.0
    rr = _f(row.get("rr"))
    x["rr"] = (min(3.0, rr) - 1.5) if rr is not None else 0.0
    evr = _f(row.get("ev_r"))
    x["ev_r"] = max(-1.0, min(2.0, evr)) if evr is not None else 0.0
    hcs = _f(row.get("hcs_score"))
    if hcs is not None:
        x["hcs_score"] = hcs / 100.0 - 0.5
    cov = _f(row.get("evidence_coverage"))
    if cov is not None:
        x["evidence_coverage"] = cov - 0.7
    mem = _f(row.get("setup_memory_wr"))
    if mem is not None:
        x["setup_memory"] = mem - 0.5
    reg = str(row.get("regime") or "")
    for r in _REGIMES:
        x[f"reg_{r}"] = 1.0 if reg == r else 0.0
    st = str(row.get("signal_type") or "")
    for t in _TYPES:
        x[f"typ_{t}"] = 1.0 if st == t else 0.0
    tod = str(row.get("tod_bucket") or "")
    for d in _TODS:
        x[f"tod_{d}"] = 1.0 if tod == d else 0.0
    return x


class OnlineLogit:
    def __init__(self, base_rate: float = 0.5):
        self.w: dict = {}
        self.b = math.log(max(1e-6, min(1 - 1e-6, base_rate)) / (1 - max(1e-6, min(1 - 1e-6, base_rate))))
        self.n_seen = 0
        self.version = MODEL_VERSION

    def predict(self, x: dict) -> float:
        z = self.b + sum(self.w.get(k, 0.0) * v for k, v in x.items())
        return _sig(z)

    def update(self, x: dict, y: int, lr: float = _LR):
        p = self.predict(x)
        g = p - y                          # d(logloss)/dz
        for k, v in x.items():
            self.w[k] = self.w.get(k, 0.0) - lr * (g * v + _L2 * self.w.get(k, 0.0))
        self.b -= lr * g
        self.n_seen += 1

    def to_dict(self) -> dict:
        return {"version": self.version, "b": round(self.b, 6),
                "w": {k: round(v, 6) for k, v in sorted(self.w.items())},
                "n_seen": self.n_seen}

    @classmethod
    def from_dict(cls, d: dict) -> "OnlineLogit":
        m = cls()
        m.b = float(d.get("b", m.b))
        m.w = {k: float(v) for k, v in (d.get("w") or {}).items()}
        m.n_seen = int(d.get("n_seen", 0))
        m.version = d.get("version", MODEL_VERSION)
        return m


# --------------------------------------------------------------- data
def _resolved_rows():
    try:
        con = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return []
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT signal_score, momentum, regime, signal_type, tod_bucket, rr, ev_r, "
            "       outcome, created_ts, session_date "
            "FROM scalp_signals WHERE resolved=1 AND outcome IN ('WIN','LOSS') "
            "AND signal_score IS NOT NULL ORDER BY created_ts").fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()


def _train(rows, *, epochs=_EPOCHS) -> OnlineLogit:
    base = (sum(1 for r in rows if r["outcome"] == "WIN") / len(rows)) if rows else 0.5
    m = OnlineLogit(base_rate=base)
    feats = [(features(r), 1 if r["outcome"] == "WIN" else 0) for r in rows]
    for ep in range(epochs):
        lr = _LR * (0.5 + 0.5 * (1 - ep / max(1, epochs)))   # gentle decay, deterministic
        for x, y in feats:                                    # chronological order, fixed
            m.update(x, y, lr)
    m.n_seen = len(rows)
    return m


# --------------------------------------------------------------- adaptive singleton
def _brier_ece(pairs):
    if not pairs:
        return None, None
    br = sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    bins = [[] for _ in range(10)]
    for p, y in pairs:
        bins[min(9, int(p * 10))].append((p, y))
    ece = sum(len(b) / len(pairs) * abs(sum(p for p, _ in b) / len(b) - sum(y for _, y in b) / len(b))
              for b in bins if b)
    return round(br, 4), round(ece, 4)


def refit_and_report() -> dict:
    from ..backtest import calibration as _cal
    rows = _resolved_rows()
    n = len(rows)
    if n < _MIN_ROWS:
        return {"available": False, "n": n, "min_rows": _MIN_ROWS,
                "reason": f"< {_MIN_ROWS} resolved outcomes -- not enough to fit even a 1-unit model"}
    model = _train(rows)
    try:
        with open(_STATE, "w") as f:
            json.dump({**model.to_dict(), "trained_rows": n,
                       "trained_through": rows[-1]["created_ts"]}, f, indent=2)
    except OSError:
        pass

    # walk-forward: fit on first 70% (chronological), score last 30%; compare to
    # the EXISTING closed-form logistic on the same split.
    k = int(n * 0.7)
    adj, log = [], []
    if k >= _MIN_ROWS:
        m_oos = _train(rows[:k])
        samp = [{"score": r["signal_score"], "regime": r["regime"],
                 "signal_type": r["signal_type"], "win": r["outcome"] == "WIN"} for r in rows[:k]]
        c_oos = _cal.fit(samp)
        for r in rows[k:]:
            y = 1 if r["outcome"] == "WIN" else 0
            adj.append((m_oos.predict(features(r)), y))
            log.append((_cal.predict(c_oos, r["signal_score"], regime=r["regime"] or "?",
                                     signal_type=r["signal_type"] or "?"), y))
    a_br, a_ece = _brier_ece(adj)
    l_br, l_ece = _brier_ece(log)

    top = sorted(model.w.items(), key=lambda kv: -abs(kv[1]))[:8]
    return {
        "available": True, "version": MODEL_VERSION, "trained_rows": n,
        "sessions": sorted({r["session_date"] for r in rows}),
        "base_win_rate": round(sum(1 for r in rows if r["outcome"] == "WIN") / n, 4),
        "bias": round(model.b, 4),
        "top_weights": [{"feature": k2, "weight": round(v, 4)} for k2, v in top],
        "walk_forward": {
            "fit_on_first": k, "scored_last": len(adj),
            "adaptive": {"brier": a_br, "ece": a_ece},
            "existing_logistic": {"brier": l_br, "ece": l_ece},
            "winner": (None if a_br is None or l_br is None
                       else "adaptive" if a_br < l_br - 1e-6
                       else "existing_logistic" if l_br < a_br - 1e-6 else "tie"),
        },
        "verdict": (
            "SHADOW / ADVISORY. Trained on "
            f"{n} outcomes over {len(set(r['session_date'] for r in rows))} sessions / one regime -- "
            "same one-week limit as the calibration report. The A+ gate still uses the existing "
            "closed-form logistic; this model is shown side-by-side only. NOT VALIDATED; "
            "nothing PROVEN. Re-fits whenever new outcomes resolve."
        ),
    }


_CACHE = {"model": None, "rows": -1}


def _model() -> OnlineLogit | None:
    rows = _resolved_rows()
    if len(rows) < _MIN_ROWS:
        return None
    if _CACHE["model"] is None or _CACHE["rows"] != len(rows):
        _CACHE["model"] = _train(rows)
        _CACHE["rows"] = len(rows)
    return _CACHE["model"]


def score(row: dict) -> dict:
    """Advisory adaptive probability for a live decision row. row may carry
    hcs_score / evidence_coverage / setup_memory_wr for the richer features."""
    m = _model()
    if m is None:
        return {"status": "INSUFFICIENT", "adaptive_probability": None,
                "n_trained": _CACHE["rows"] if _CACHE["rows"] > 0 else 0}
    p = m.predict(features(row))
    return {"status": "OK", "adaptive_probability": round(p, 4),
            "n_trained": m.n_seen, "version": MODEL_VERSION,
            "note": "advisory only -- the A+ gate uses calibrated_probability, not this"}
