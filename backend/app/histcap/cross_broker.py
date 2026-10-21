"""Cross-broker validation of CAPTURED rows (MCP 3.3 priority #5, item 4):
real, measured statistics comparing the most-recently-captured
ANGELONE_QUOTE_FULL vs DHAN_CHAIN rows for the same symbol/expiry/strike/
option_type, read directly from quote_snapshots. Never manufactures
equivalence -- a strike present in only one provider is reported as such,
never silently dropped or assumed equal to the other side.

This is a POST-CAPTURE, read-only comparison of what each provider's own
periodic capture cycle already wrote (app.histcap.worker._capture_quotes /
_capture_dhan) -- a different, complementary check from
app.optionchain.resolve.check_data_conflict(), which does a live, on-
demand fetch-both-right-now spot comparison. This one measures actual
capture-cycle agreement over real stored history.
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean

from .store import HistStore, hist_store

_ANGEL_SOURCE = "ANGELONE_QUOTE_FULL"
_DHAN_SOURCE = "DHAN_CHAIN"


def _diff(a, b):
    if a is None or b is None:
        return None
    return round(b - a, 4)


def _diff_pct(a, b):
    if a is None or b is None or a == 0:
        return None
    return round((b - a) / a * 100.0, 4)


def _ts_drift_sec(a_ts, d_ts):
    if not a_ts or not d_ts:
        return None
    try:
        a_dt = datetime.fromisoformat(str(a_ts).replace("Z", "+00:00"))
        d_dt = datetime.fromisoformat(str(d_ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return round((d_dt - a_dt).total_seconds(), 1)


DEFAULT_LOOKBACK_MIN = 15.0


def compare_captured_quotes(symbol: str, expiry: str, *, since: str | None = None,
                            limit: int = 5000, store: HistStore | None = None,
                            as_of: str | None = None) -> dict:
    """{"matched": N, "angelone_only": N, "dhan_only": N, "pairs": [...],
    "summary": {avg/max abs diffs for ltp/oi/volume/bid/ask, avg/max
    timestamp drift}} -- every number here is a real measurement of rows
    already in quote_snapshots, or None when there's nothing to measure
    (never a fabricated 0).

    `since` defaults to DEFAULT_LOOKBACK_MIN minutes before as_of (now if
    omitted). Latest rows are selected per provider/exchange/leg before
    applying limit; both capture and market timestamps must be <= as_of.
    Equal timestamps are resolved by row ID, never arbitrary query order.
    """
    store = store or hist_store()
    u = str(symbol or "").upper()
    from datetime import datetime, timedelta, timezone
    from .read_contract import instant
    cutoff = instant(as_of) if as_of else datetime.now(timezone.utc)
    if since is None:
        since = (cutoff - timedelta(minutes=DEFAULT_LOOKBACK_MIN)).isoformat()
    rows = store.latest_provider_quotes(u, expiry, since=since, as_of=cutoff.isoformat(), limit=limit)

    latest: dict[tuple, dict] = {}
    for r in rows:
        if r.get("source") not in (_ANGEL_SOURCE, _DHAN_SOURCE):
            continue
        key = (r["exchange"], r["strike"], r["option_type"], r["source"])
        latest[key] = r  # the storage query already deterministically selected rank 1

    strikes = sorted({k[:3] for k in latest}, key=repr)
    pairs, angel_only, dhan_only = [], [], []
    for exchange, strike, ot in strikes:
        a = latest.get((exchange, strike, ot, _ANGEL_SOURCE))
        d = latest.get((exchange, strike, ot, _DHAN_SOURCE))
        if a and d:
            pairs.append({
                "strike": strike, "option_type": ot, "exchange": exchange,
                "angelone_ltp": a.get("ltp"), "dhan_ltp": d.get("ltp"),
                "ltp_diff_pct": _diff_pct(a.get("ltp"), d.get("ltp")),
                "angelone_oi": a.get("oi"), "dhan_oi": d.get("oi"),
                "oi_diff": _diff(a.get("oi"), d.get("oi")),
                "angelone_volume": a.get("volume"), "dhan_volume": d.get("volume"),
                "volume_diff": _diff(a.get("volume"), d.get("volume")),
                "angelone_bid": a.get("bid"), "dhan_bid": d.get("bid"),
                "bid_diff": _diff(a.get("bid"), d.get("bid")),
                "angelone_ask": a.get("ask"), "dhan_ask": d.get("ask"),
                "ask_diff": _diff(a.get("ask"), d.get("ask")),
                "timestamp_drift_sec": _ts_drift_sec(
                    a.get("exch_ts") or a.get("received_ts"), d.get("exch_ts") or d.get("received_ts")),
                "angelone_received_ts": a.get("received_ts"), "dhan_received_ts": d.get("received_ts"),
            })
        elif a and not d:
            angel_only.append({"strike": strike, "option_type": ot})
        elif d and not a:
            dhan_only.append({"strike": strike, "option_type": ot})

    def _stats(field):
        vals = [p[field] for p in pairs if p.get(field) is not None]
        if not vals:
            return {"avg": None, "max_abs": None, "n": 0}
        return {"avg": round(mean(vals), 4), "max_abs": round(max(abs(v) for v in vals), 4), "n": len(vals)}

    return {
        "symbol": u, "expiry": expiry, "as_of": cutoff.isoformat(),
        "matched": len(pairs), "angelone_only": len(angel_only), "dhan_only": len(dhan_only),
        "pairs": pairs, "angelone_only_strikes": angel_only, "dhan_only_strikes": dhan_only,
        "summary": {
            "ltp_diff_pct": _stats("ltp_diff_pct"), "oi_diff": _stats("oi_diff"),
            "volume_diff": _stats("volume_diff"), "bid_diff": _stats("bid_diff"),
            "ask_diff": _stats("ask_diff"), "timestamp_drift_sec": _stats("timestamp_drift_sec"),
        },
    }
