"""HistoricalDataQualityReport (spec section 3). Pure function over normalized
rows; reports what is present and what is missing, never fills gaps."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import json

_IST = timezone(timedelta(hours=5, minutes=30))
_FIELDS = ("spot", "volume", "oi", "iv", "open", "high", "low", "close")
_IDENTITY = ("exchange", "underlying", "expiry", "strike", "option_type", "timeframe", "source")


def _dt(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def build_report(rows: list[dict], *, interval_min: int = 1) -> dict:
    if not rows:
        return {"rows": 0, "status": "EMPTY"}
    by_day = defaultdict(list)
    keys = Counter()
    reasons = Counter()
    for r in rows:
        identity = tuple(r.get(k) for k in _IDENTITY)
        ts = _dt(r["timestamp"])
        keys[(identity, ts)] += 1
        by_day[(ts.astimezone(_IST).date(), identity)].append(ts)
        inputs = r.get("greek_inputs") or {}
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except ValueError:
                inputs = {}
        if isinstance(inputs, dict) and inputs.get("unavailable_reason"):
            reasons[inputs["unavailable_reason"]] += 1
    dupes = sum(c - 1 for c in keys.values() if c > 1)
    days, gaps, missing_bars = [], [], 0
    for (d, identity), tss in sorted(by_day.items(), key=lambda item: (item[0][0], repr(item[0][1]))):
        tss = sorted(set(tss))
        series = dict(zip(_IDENTITY, identity))
        tf = series["timeframe"] or ""
        minutes = int(tf[:-1]) if tf.endswith("m") and tf[:-1].isdigit() else interval_min
        if minutes <= 0:
            raise ValueError("positive timeframe required")
        step = timedelta(minutes=minutes)
        for a, b in zip(tss, tss[1:]):
            if b - a > step:
                gaps.append({"day": d.isoformat(), "series": series, "from": a.isoformat(), "to": b.isoformat(),
                             "missing_bars": int((b - a) / step) - 1})
        # expected = every bar between the day's first and last observed bar.
        # Session length is deliberately NOT assumed (live NIFTY data runs
        # 09:15-15:39 IST = 385 bars, not 375), so missing = interior gaps only;
        # a truncated day (data ending early) cannot be detected from the rows alone.
        expected = int((tss[-1] - tss[0]) / step) + 1
        miss = max(0, expected - len(tss))
        missing_bars += miss
        days.append({"day": d.isoformat(), "series": series, "bars": len(tss), "expected": expected, "missing": miss,
                     "first": tss[0].astimezone(_IST).strftime("%H:%M"), "last": tss[-1].astimezone(_IST).strftime("%H:%M")})
    n = len(rows)
    field_missing = {f: sum(1 for r in rows if r.get(f) in (None,)) for f in _FIELDS}
    greek_src = Counter(r.get("greek_source") for r in rows)
    total_expected = sum(d["expected"] for d in days)
    return {
        "rows": n, "sources": sorted({r["source"] for r in rows}),
        "instruments": sorted({(r["underlying"], r["option_type"]) for r in rows}),
        "date_coverage": {"from": days[0]["day"], "to": days[-1]["day"], "days": len({d["day"] for d in days})},
        "per_day": days, "missing_bars_total": missing_bars, "timestamp_gaps": gaps[:200],
        "duplicate_records": dupes, "missing_fields": field_missing,
        "missing_greeks": greek_src.get("UNAVAILABLE", 0), "greek_source_counts": dict(greek_src),
        "unavailable_reasons": dict(reasons),
        "completeness_pct": round(100.0 * min(1.0, sum(d["bars"] for d in days) / total_expected), 2)
        if total_expected else None,
        "note": "missing = interior gaps per contract/provider/timeframe/session; wholly absent series and session edges need external evidence",
    }
