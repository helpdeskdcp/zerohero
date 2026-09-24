from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from .. import db

MIN_SAMPLE = 3
_IST = timezone(timedelta(hours=5, minutes=30))
_STOP_REASONS = ("STOP", "SL_HIT_RECONSTRUCTED", "SL_HIT_ESTIMATED_NO_NATGASMINI_DATA")


def _closed_rows() -> list[dict]:
    with db.db() as conn:
        rows = conn.execute("SELECT * FROM ai_paper_trades WHERE status='CLOSED'").fetchall()
    return [dict(r) for r in rows]


def _hour_ist(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_IST).hour
    except (ValueError, TypeError):
        return None


def is_stop(row: dict) -> bool:
    return str(row.get("exit_reason") or "").upper() in _STOP_REASONS


def summary(rows: list[dict] | None = None) -> dict:
    rows = _closed_rows() if rows is None else rows
    total = len(rows)
    stops = sum(1 for r in rows if is_stop(r))
    wins = sum(1 for r in rows if r.get("result") == "WIN")
    losses = sum(1 for r in rows if r.get("result") == "LOSS")
    flat = sum(1 for r in rows if r.get("result") == "FLAT")
    return {
        "total_closed": total, "stop_exits": stops,
        "stop_hit_rate": round(stops / total, 4) if total else None,
        "win": wins, "loss": losses, "flat": flat,
    }


def _breakdown(rows: list[dict], key_fn) -> list[dict]:
    buckets: dict = defaultdict(lambda: {"n": 0, "stops": 0})
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        buckets[k]["n"] += 1
        if is_stop(r):
            buckets[k]["stops"] += 1
    out = []
    for k, v in buckets.items():
        out.append({
            "key": k, "n": v["n"], "stop_exits": v["stops"],
            "stop_hit_rate": round(v["stops"] / v["n"], 4) if v["n"] else None,
            "sample_flag": "OK" if v["n"] >= MIN_SAMPLE else "LOW_SAMPLE",
        })
    out.sort(key=lambda x: (-x["n"]))
    return out


def by_underlying(rows: list[dict] | None = None) -> list[dict]:
    rows = _closed_rows() if rows is None else rows
    return _breakdown(rows, lambda r: r.get("underlying"))


def by_regime(rows: list[dict] | None = None) -> list[dict]:
    rows = _closed_rows() if rows is None else rows
    return _breakdown(rows, lambda r: r.get("market_regime"))


def by_strategy(rows: list[dict] | None = None) -> list[dict]:
    rows = _closed_rows() if rows is None else rows
    return _breakdown(rows, lambda r: r.get("strategy"))


def by_hour_ist(rows: list[dict] | None = None) -> list[dict]:
    rows = _closed_rows() if rows is None else rows
    return _breakdown(rows, lambda r: _hour_ist(r.get("opened_ts")))


def mae_overshoot(rows: list[dict] | None = None) -> dict:
    """For STOP exits: mae / |entry - stop_loss|. ~1.0 = stop respected
    exactly; notably >1.0 = price moved past the planned stop before this
    engine's monitor loop caught it (capture-interval slippage, a real risk
    exposure, not a modeling error)."""
    rows = _closed_rows() if rows is None else rows
    per_symbol: dict = defaultdict(list)
    for r in rows:
        if not is_stop(r):
            continue
        entry, sl, mae = r.get("entry"), r.get("stop_loss"), r.get("mae")
        if entry is None or sl is None or mae is None:
            continue
        planned_risk = abs(entry - sl)
        if planned_risk <= 0:
            continue
        per_symbol[r.get("underlying")].append(mae / planned_risk)
    out = []
    for sym, ratios in per_symbol.items():
        n = len(ratios)
        mean_ratio = round(sum(ratios) / n, 4)
        max_ratio = round(max(ratios), 4)
        out.append({
            "underlying": sym, "n": n, "mean_mae_over_planned_risk": mean_ratio,
            "max_mae_over_planned_risk": max_ratio,
            "sample_flag": "OK" if n >= MIN_SAMPLE else "LOW_SAMPLE",
        })
    out.sort(key=lambda x: (-x["n"]))
    return {"rows": out,
            "note": "ratio ~1.0 = stop respected exactly; notably >1.0 = the stop "
                    "was overshot before being caught -- capture-interval slippage exposure"}


_PROB_BUCKETS = [(0.0, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def probability_vs_stop_rate(rows: list[dict] | None = None) -> list[dict]:
    """Does a higher STATED entry probability actually mean fewer STOP exits?
    Descriptive only -- same discipline as the K8 calibration audit, scoped
    to stop-outs specifically."""
    rows = _closed_rows() if rows is None else rows
    out = []
    for lo, hi in _PROB_BUCKETS:
        bucket = [r for r in rows if r.get("probability") is not None and lo <= r["probability"] < hi]
        n = len(bucket)
        if n == 0:
            continue
        stops = sum(1 for r in bucket if is_stop(r))
        wins = sum(1 for r in bucket if r.get("result") == "WIN")
        out.append({
            "probability_bucket": f"{lo:.1f}-{hi:.1f}", "n": n,
            "stop_hit_rate": round(stops / n, 4), "actual_win_rate": round(wins / n, 4),
            "sample_flag": "OK" if n >= MIN_SAMPLE else "LOW_SAMPLE",
        })
    return out


def full_report() -> dict:
    rows = _closed_rows()
    return {
        "summary": summary(rows),
        "by_underlying": by_underlying(rows),
        "by_regime": by_regime(rows),
        "by_strategy": by_strategy(rows),
        "by_hour_ist": by_hour_ist(rows),
        "mae_overshoot": mae_overshoot(rows),
        "probability_vs_stop_rate": probability_vs_stop_rate(rows),
    }
