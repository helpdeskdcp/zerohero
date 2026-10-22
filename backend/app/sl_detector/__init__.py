"""Stop-loss estimator experiment (spec: data/research/sl_detector/SPEC_2026-10-21.md). Rule-based and deterministic; reads captured quotes from BEFORE the post only; writes only its own ledgers.
No trading module imports this package; its output is never used by any order path."""
from __future__ import annotations

import math
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from ..forward_test import ledger as L

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PRED = os.path.join(HERE, "data", "sl_detector", "predictions.jsonl")
TRUTH = os.path.join(HERE, "data", "sl_detector", "truth.jsonl")
LOOKBACK_SEC = 3600
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def estimate(entry: float, ltps: list[float]) -> dict:
    """Pure rule (spec 'Method'). ltps = captured last prices of the same contract in the 60 min before the post, oldest first."""
    E = float(entry); prices = [p for p in ltps if p and p > 0]
    if not prices: return {"sl": round(round(0.70 * E / 0.05) * 0.05, 2), "method": "FALLBACK_70PCT", "low": None}
    low = min(prices)
    if low >= E: low = prices[-1]
    sl = min(0.85 * E, max(0.55 * E, 0.97 * low))
    return {"sl": round(round(sl / 0.05) * 0.05, 2), "method": "SWING_LOW_CLIPPED", "low": low}


def prior_ltps(underlying: str, expiry_iso: str, strike: float, ot: str, t_post: float, db: str | None = None) -> list[float]:
    d = datetime.fromisoformat(expiry_iso); raw = f"{d.day:02d}{MON[d.month - 1]}{d.year}"
    iso = lambda x: datetime.fromtimestamp(x, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    day = datetime.fromtimestamp(t_post, tz=timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    c = sqlite3.connect(f"file:{db or os.path.join(HERE, 'data', 'market_history.db')}?mode=ro", uri=True, timeout=30)
    try:
        rows = c.execute("SELECT ltp FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND session_date_ist=? AND received_ts>=? AND received_ts<? AND ltp IS NOT NULL ORDER BY received_ts",
                         (underlying, raw, strike, ot, day, iso(t_post - LOOKBACK_SEC), iso(t_post))).fetchall()
    finally:
        c.close()
    return [float(r[0]) for r in rows]


def record_prediction(signal_id: str, underlying: str, expiry_iso: str, strike: float, ot: str, entry: float, t_post: float, *, path: str = PRED, ltps: list[float] | None = None) -> dict:
    p = ltps if ltps is not None else prior_ltps(underlying, expiry_iso, strike, ot, t_post)
    e = estimate(entry, p)
    return L.append(path, {"type": "SL_PREDICTION", "signal_id": signal_id, "underlying": underlying, "expiry": expiry_iso, "strike": strike, "option_type": ot, "entry": entry, "post_ts": t_post,
                           "n_quotes": len(p), "low": e["low"], "predicted_sl": e["sl"], "method": e["method"], "recorded_ts": time.time()})


def record_truth(signal_id: str, true_sl: float, *, path: str = TRUTH, pred_path: str = PRED, now: float | None = None) -> dict:
    preds = _rows(pred_path, signal_id)
    if not preds: raise ValueError("no prediction recorded for this signal")
    t = now or time.time()
    if t <= preds[0]["recorded_ts"]: raise ValueError("truth must be entered after the prediction")
    if any(r["signal_id"] == signal_id for r in _rows(path)): raise ValueError("truth already recorded for this signal")
    if not (0 < true_sl < preds[0]["entry"]): raise ValueError("true stop must be below the entry")
    return L.append(path, {"type": "SL_TRUTH", "signal_id": signal_id, "true_sl": float(true_sl), "recorded_ts": t})


def _rows(path: str, signal_id: str | None = None) -> list[dict]:
    import json
    rows = [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()] if os.path.exists(path) else []
    return [r for r in rows if signal_id is None or r["signal_id"] == signal_id]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0: return (0.0, 1.0)
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n); a = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - a) / d, (c + a) / d)


def report(pred_path: str = PRED, truth_path: str = TRUTH) -> dict:
    preds = {r["signal_id"]: r for r in _rows(pred_path)}; truth = {r["signal_id"]: r for r in _rows(truth_path)}
    rows = []
    for sid, t in truth.items():
        p = preds.get(sid)
        if p: rows.append({"signal_id": sid, "entry": p["entry"], "predicted": p["predicted_sl"], "true": t["true_sl"], "method": p["method"], "error_pts": round(100 * (p["predicted_sl"] - t["true_sl"]) / p["entry"], 2)})
    n = len(rows); ae = sorted(abs(r["error_pts"]) for r in rows)
    w5, w10 = sum(1 for x in ae if x <= 5), sum(1 for x in ae if x <= 10)
    out = {"n_predictions": len(preds), "n_with_truth": n, "n_fallback": sum(1 for r in rows if r["method"] == "FALLBACK_70PCT"), "rows": rows,
           "verdict": "INSUFFICIENT_SAMPLE (n < 30): accuracy only, no win-probability claim" if n < 30 else "n >= 30: accuracy with intervals"}
    if n:
        out.update(median_abs_error_pts=ae[n // 2] if n % 2 else (ae[n // 2 - 1] + ae[n // 2]) / 2, within_5_pts=[w5, n, [round(x, 3) for x in wilson(w5, n)]], within_10_pts=[w10, n, [round(x, 3) for x in wilson(w10, n)]])
    return out


def plan_for(underlying: str, expiry_iso: str, strike: float, ot: str, entry: float, t_post: float, *, ltps: list[float] | None = None, db: str | None = None) -> dict:
    """The risk module for an entry-only signal: stop from the estimator rule, targets at 1R / 2R / 3R of that risk (the 1:1, 1:2, 1:3 ladder), rounded to 0.05. Read-only."""
    p = ltps if ltps is not None else prior_ltps(underlying, expiry_iso, strike, ot, t_post, db)
    e = estimate(entry, p); r = float(entry) - e["sl"]
    return {"sl": e["sl"], "targets": [round(round((float(entry) + k * r) / 0.05) * 0.05, 2) for k in (1, 2, 3)], "risk_per_unit": round(r, 2), "method": e["method"], "swing_low": e["low"], "n_quotes_60min": len(p), "last_price": (p[-1] if p else None)}
