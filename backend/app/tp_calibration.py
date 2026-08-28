"""
Turning-Point Engine calibration — deterministic, closed-form, no ML, no API.

  record(out, symbol, tf)      -> INSERT a tp_predictions row
  resolve_pending(fetch_fn)    -> for rows past their horizon, fetch actual OHLC
                                  and score DIRECTION_HIT / ZONE_HIT / BOTH / MISS
  recalibrate()                -> fit (k, b) and feature weights from resolved rows
                                  by ordinary least squares + correlation, guard-railed,
                                  and persist to app_settings.tp_calibration
  load()                       -> current {k, b, weights, resolved_n}  (engine reads this)

Same DB rows in  ->  identical coefficients out.
"""
import json
import time as _time
from datetime import datetime, timezone, timedelta

from . import db
from . import instruments

CAL_KEY = "tp_calibration"
_SEED = {"k": 3.2, "b": 0.0,
         "weights": {"stretch": 0.20, "rsi": 0.16, "sr": 0.18, "band": 0.12,
                     "wick": 0.12, "mom": 0.12, "vol": 0.06, "oi": 0.04}}
_MIN_ROWS = 50            # need this many resolved rows before any weight update
_RECAL_EVERY = 25        # recalibrate once this many new rows have resolved
_W_LO, _W_HI = 0.02, 0.35
_K_LO, _K_HI = 1.5, 6.0
_ALPHA = 0.2             # EMA blend for weights (slow, reproducible drift)

_TF_MIN = {"1m": 1, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60}
_last_run = {"resolve": 0.0}


def load() -> dict:
    try:
        d = json.loads(db.get_setting(CAL_KEY) or "{}")
    except Exception:
        d = {}
    out = {"k": d.get("k", _SEED["k"]), "b": d.get("b", _SEED["b"]),
           "weights": {**_SEED["weights"], **(d.get("weights") or {})},
           "resolved_n": int(d.get("resolved_n") or 0),
           "updated_ts": d.get("updated_ts")}
    return out


def _save(cal: dict):
    db.set_setting(CAL_KEY, json.dumps(cal))


def record(out: dict, symbol: str, timeframe: str) -> None:
    """Persist a prediction. Called by the pipelines / reversal scan when
    tp `record` is enabled. Skips flat NO_TURN with a tiny lean."""
    if not out or out.get("decision") == "DATA_UNAVAILABLE":
        return
    if abs(out.get("turn") or 0) < 0.05:
        return
    nh = out.get("next_high_zone") or {}
    nl = out.get("next_low_zone") or {}
    nh_z = nh.get("zone") or [None, None]
    nl_z = nl.get("zone") or [None, None]
    with db.db() as conn:
        conn.execute(
            "INSERT INTO tp_predictions (ts,symbol,timeframe,direction,turn,raw,p_up,confidence,"
            "close_at_pred,atr_at_pred,horizon_bars,next_hi_lo,next_hi_hi,next_lo_lo,next_lo_hi,"
            "expected_move_pts,feature_scores) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), symbol, timeframe, out.get("direction"),
             out.get("turn"), out.get("raw"), out.get("p_up"), out.get("confidence"),
             (out.get("facts") or {}).get("price"), (out.get("facts") or {}).get("atr14"),
             out.get("horizon_bars"), nh_z[0], nh_z[1], nl_z[0], nl_z[1],
             (out.get("expected_move") or {}).get("pts"),
             json.dumps(out.get("feature_scores") or {})))


def resolve_pending(fetch_fn, now: datetime | None = None) -> int:
    """fetch_fn(market, symbol, exchange, symboltoken, interval, fromdate, todate,
    timeframe, instrument) -> connector dict (i.e. angelone.fetch_candles)."""
    now = now or datetime.now(timezone.utc)
    with db.db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM tp_predictions WHERE resolved=0 ORDER BY id LIMIT 200")]
    resolved = 0
    for r in rows:
        try:
            pred_ts = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
        except Exception:
            _mark_timeout(r["id"]); resolved += 1; continue
        tf_min = _TF_MIN.get(r["timeframe"] or "5m", 5)
        horizon = timedelta(minutes=tf_min * (r["horizon_bars"] or 6))
        if now - pred_ts < horizon:
            continue
        meta = instruments.resolve(r["symbol"])
        if not meta:
            _mark_timeout(r["id"]); resolved += 1; continue
        try:
            conn = fetch_fn(market=meta.get("market"), symbol=r["symbol"],
                            exchange=meta.get("exchange"), symboltoken=meta.get("symboltoken"),
                            interval=None, fromdate=None, todate=None,
                            timeframe=r["timeframe"] or "5m", instrument="FUT")
        except Exception:
            continue
        if conn.get("data_status") != "OK":
            continue
        fwd = [c for c in conn["candles"]
               if _after(c.get("t"), pred_ts)][: (r["horizon_bars"] or 6) + 1]
        if len(fwd) < 2:
            _mark_timeout(r["id"]); resolved += 1; continue
        _score_row(r, fwd)
        resolved += 1

    if resolved:
        cal = load()
        newly = int((db.get_setting("_tp_resolved_since_cal") or 0)) + resolved
        if newly >= _RECAL_EVERY:
            recalibrate()
            db.set_setting("_tp_resolved_since_cal", "0")
        else:
            db.set_setting("_tp_resolved_since_cal", str(newly))
    return resolved


def _after(t, pred_ts):
    try:
        if isinstance(t, (int, float)):
            ct = datetime.fromtimestamp(t if t > 1e12 else t * 1000, tz=timezone.utc) \
                if t > 1e10 else datetime.fromtimestamp(t, tz=timezone.utc)
        else:
            ct = datetime.fromisoformat(str(t).replace("Z", "+00:00"))
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=timezone.utc)
        return ct > pred_ts
    except Exception:
        return False


def _mark_timeout(row_id):
    with db.db() as conn:
        conn.execute("UPDATE tp_predictions SET resolved=1, resolved_ts=?, outcome='TIMEOUT' WHERE id=?",
                     (datetime.now(timezone.utc).isoformat(), row_id))


def _score_row(r, fwd):
    c0 = r["close_at_pred"] or 0
    atr = r["atr_at_pred"] or 1e-9
    closes = [c["c"] for c in fwd]
    highs = [c["h"] for c in fwd]
    lows = [c["l"] for c in fwd]
    fwd_close = closes[-1]
    signed = (fwd_close - c0) / atr
    up = r["direction"] == "UP_TURN"
    dn = r["direction"] == "DOWN_TURN"
    dir_hit = (up and signed > 0.15) or (dn and signed < -0.15)
    zone_hit = False
    if up and r["next_hi_lo"] is not None:
        zone_hit = max(highs) >= r["next_hi_lo"]
    elif dn and r["next_lo_hi"] is not None:
        zone_hit = min(lows) <= r["next_lo_hi"]
    outcome = ("BOTH" if dir_hit and zone_hit else
               "DIRECTION_HIT" if dir_hit else
               "ZONE_HIT" if zone_hit else "MISS")
    mfe = (max(highs) - c0) / atr if up else (c0 - min(lows)) / atr
    mae = (c0 - min(lows)) / atr if up else (max(highs) - c0) / atr
    tgt = r["next_hi_lo"] if up else r["next_lo_hi"]
    err = abs((max(highs) if up else min(lows)) - tgt) if tgt is not None else None
    with db.db() as conn:
        conn.execute(
            "UPDATE tp_predictions SET resolved=1, resolved_ts=?, outcome=?, fwd_close=?, "
            "mfe_atr=?, mae_atr=?, signed_outcome=?, err_pts=? WHERE id=?",
            (datetime.now(timezone.utc).isoformat(), outcome, round(fwd_close, 2),
             round(mfe, 3), round(mae, 3), round(signed, 3),
             round(err, 2) if err is not None else None, r["id"]))


def recalibrate() -> dict:
    """Fit sigmoid (k, b) by OLS on logit(hit-rate) ~ k*turn + b across turn
    buckets, and feature weights ∝ max(0, corr(Sᵢ, signed_outcome)), EMA-blended
    with the prior. All closed-form and deterministic."""
    with db.db() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT turn, p_up, signed_outcome, feature_scores, direction "
            "FROM tp_predictions WHERE resolved=1 AND outcome!='TIMEOUT' AND signed_outcome IS NOT NULL")]
    cal = load()
    cal["resolved_n"] = len(rows)
    if len(rows) < _MIN_ROWS:
        cal["updated_ts"] = datetime.now(timezone.utc).isoformat()
        _save(cal)
        return cal

    # --- (k, b): bucket by turn, y = P(up move) per bucket, OLS on logit ---
    import math
    buckets = {}
    for r in rows:
        t = r["turn"] or 0.0
        key = round(t, 1)
        b = buckets.setdefault(key, [0, 0])
        b[0] += 1
        if (r["signed_outcome"] or 0) > 0:
            b[1] += 1
    xs, ys = [], []
    for t, (nn, up) in buckets.items():
        if nn < 5:
            continue
        rate = min(0.97, max(0.03, up / nn))
        xs.append(t)
        ys.append(math.log(rate / (1 - rate)))
    if len(xs) >= 3:
        n = len(xs)
        sx, sy = sum(xs), sum(ys)
        sxx = sum(x * x for x in xs)
        sxy = sum(x * y for x, y in zip(xs, ys))
        denom = n * sxx - sx * sx
        if abs(denom) > 1e-9:
            k = (n * sxy - sx * sy) / denom
            b = (sy - k * sx) / n
            cal["k"] = round(min(_K_HI, max(_K_LO, k)), 3)
            cal["b"] = round(min(1.0, max(-1.0, b)), 3)

    # --- feature weights ∝ max(0, corr(Sᵢ, signed_outcome)) ---
    feats = list(_SEED["weights"])
    cols = {f: [] for f in feats}
    outs = []
    for r in rows:
        try:
            fs = json.loads(r["feature_scores"] or "{}")
        except Exception:
            continue
        outs.append(r["signed_outcome"] or 0.0)
        for f in feats:
            cols[f].append(float(fs.get(f) or 0.0))
    if len(outs) >= _MIN_ROWS:
        def corr(a, b):
            m = len(a)
            if m < 2:
                return 0.0
            ma, mb = sum(a) / m, sum(b) / m
            va = sum((x - ma) ** 2 for x in a)
            vb = sum((x - mb) ** 2 for x in b)
            if va <= 0 or vb <= 0:
                return 0.0
            cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
            return cov / math.sqrt(va * vb)
        raw_w = {f: max(0.0, corr(cols[f], outs)) for f in feats}
        s = sum(raw_w.values())
        if s > 1e-9:
            target = {f: raw_w[f] / s for f in feats}
            prior = cal["weights"]
            w = {f: (1 - _ALPHA) * prior.get(f, _SEED["weights"][f]) + _ALPHA * target[f]
                 for f in feats}
            # project onto {sum==1, _W_LO<=wi<=_W_HI} by alternating renormalize/clamp;
            # deterministic and converges (feasible: 8*[0.02,0.35] brackets 1.0)
            for _ in range(24):
                s = sum(w.values()) or 1e-9
                w = {f: min(_W_HI, max(_W_LO, w[f] / s)) for f in feats}
            cal["weights"] = {f: round(w[f], 4) for f in feats}

    cal["updated_ts"] = datetime.now(timezone.utc).isoformat()
    _save(cal)
    return cal


def tick(fetch_fn, min_interval: int = 180) -> dict:
    """Throttled entry point for the runner loop. Resolves due predictions and,
    when enough have accumulated, recalibrates."""
    now = _time.time()
    if now - _last_run["resolve"] < min_interval:
        return {"skipped": True}
    _last_run["resolve"] = now
    resolved = resolve_pending(fetch_fn)
    return {"resolved": resolved, "calibration": load()}
