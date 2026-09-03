"""
PHASE 3 + PHASE 7 — explicit data-quality contract for a live market snapshot,
and a NO_TRADE reason classifier.

Pure functions, read-only. Nothing here fabricates a value or turns MISSING into
0 — a field with no real data reports its status and stays None upstream.

Status vocabulary (per field group):
  AVAILABLE   - a real, fresh value is present
  MISSING     - the value should exist for this instrument but is absent
  STALE       - a value exists but is older than the freshness budget
  UNSUPPORTED - the broker/instrument cannot provide this (e.g. MCX greeks)
  INVALID     - a value exists but failed a sanity check
  DERIVED     - computed & consumed inside the state/regime sub-engines,
                not surfaced as a standalone snapshot field
"""
from __future__ import annotations

import json

# groups that the state-classifier / regime engines compute internally from the
# candle series but the runner does not persist as individual columns.
_DERIVED_GROUPS = ("EMA_SMA", "RSI", "MACD", "ADX", "BOLLINGER", "PRICE_ACTION")

_FRESH_BUDGET_SEC = 20.0


def _status_from_value(v):
    if v is None:
        return "MISSING"
    try:
        f = float(v)
        return "INVALID" if f != f else "AVAILABLE"
    except (TypeError, ValueError):
        return "AVAILABLE"          # non-numeric but present (e.g. a label)


def snapshot_data_quality(sig: dict, chain: list | None, oi_quality: dict | None,
                          feed_age_sec: float | None, *, greeks_capability: str | None = None) -> dict:
    """Return {"groups": {GROUP: STATUS}, "score": 0..1, "greeks_source": ...}."""
    sig = sig or {}
    chain = chain or []
    oiq = oi_quality or {}
    g: dict[str, str] = {}

    # --- price / structure -------------------------------------------------
    g["PRICE"] = _status_from_value(sig.get("index_ltp") if "index_ltp" in sig else sig.get("spot"))
    g["OHLC"] = "AVAILABLE" if (sig.get("atr") is not None or sig.get("state_score") is not None) else "MISSING"
    vs = sig.get("vwap_status")
    g["VWAP"] = ("AVAILABLE" if vs == "available"
                 else "INVALID" if vs == "invalid_volume"
                 else "MISSING" if sig.get("vwap") is None else "AVAILABLE")
    g["ATR"] = _status_from_value(sig.get("atr"))
    g["MOMENTUM"] = _status_from_value(sig.get("momentum"))
    g["SUPPORT"] = _status_from_value(sig.get("support"))
    g["RESISTANCE"] = _status_from_value(sig.get("resistance"))
    g["MTF"] = _status_from_value(sig.get("mtf_alignment"))
    for grp in _DERIVED_GROUPS:
        g[grp] = "DERIVED"

    # --- feed freshness colours OHLC/PRICE -------------------------------
    if feed_age_sec is not None and feed_age_sec > _FRESH_BUDGET_SEC:
        for grp in ("PRICE", "OHLC", "MOMENTUM"):
            if g.get(grp) == "AVAILABLE":
                g[grp] = "STALE"

    # --- OI family ------------------------------------------------------
    legs = [l for r in chain for l in ((r.get("ce") or {}), (r.get("pe") or {})) if l]
    oi_present = sum(1 for l in legs if l.get("oi") is not None)
    oichg_present = sum(1 for l in legs if l.get("oi_chg") is not None or l.get("oi_change") is not None)
    g["VOLUME"] = "AVAILABLE" if any(l.get("vol_delta") not in (None, 0) or l.get("volume") not in (None, 0)
                                     for l in legs) else "MISSING"
    if not legs:
        g["OI"] = g["OI_CHANGE"] = "MISSING"
    else:
        cov = oi_present / len(legs)
        g["OI"] = "AVAILABLE" if cov >= 0.8 else ("MISSING" if cov < 0.5 else "STALE")
        g["OI_CHANGE"] = "AVAILABLE" if oichg_present / len(legs) >= 0.8 else "MISSING"

    qs = oiq.get("quality_status")
    g["PCR"] = ("AVAILABLE" if qs == "GOOD" else "STALE" if qs == "PARTIAL"
                else "MISSING" if qs in ("INSUFFICIENT_OI", "NO_CHAIN", None) else "MISSING")
    g["MAX_PAIN"] = g["PCR"]

    # --- greeks family ------------------------------------------------
    srcs = {l.get("greeks_source") for l in legs}
    if greeks_capability == "UNAVAILABLE" or srcs == {"UNAVAILABLE"}:
        greeks_group = "UNSUPPORTED"
        rollup = "UNAVAILABLE"
    elif "BROKER" in srcs:
        broker_legs = sum(1 for l in legs if l.get("greeks_source") == "BROKER"
                          and l.get("delta") is not None)
        greeks_group = "AVAILABLE" if broker_legs >= max(2, len(legs) * 0.6) else "STALE"
        rollup = "BROKER" if greeks_group == "AVAILABLE" else "PARTIAL"
    elif not legs:
        greeks_group, rollup = "MISSING", "NONE"
    else:
        greeks_group, rollup = "MISSING", "NONE"
    for grp in ("IV", "DELTA", "GAMMA", "THETA", "VEGA"):
        g[grp] = greeks_group

    # --- score: fraction of non-DERIVED, non-UNSUPPORTED groups that are AVAILABLE
    scored = [v for v in g.values() if v not in ("DERIVED", "UNSUPPORTED")]
    score = round(sum(1 for v in scored if v == "AVAILABLE") / len(scored), 3) if scored else None

    return {"groups": g, "score": score, "greeks_source": rollup}


def classify_no_trade_reason(sig: dict) -> str:
    """PHASE 7 — map the free-text decide_from_context reason to a stable class:
      DATA_UNAVAILABLE | MARKET_NEUTRAL | NO_SIGNAL | CONFLICTING_SIGNAL |
      FILTER | GATE | OK
    """
    dec = str(sig.get("decision") or "").upper()
    if dec in ("BUY_CE", "BUY_PE"):
        return "OK"
    reason = str(sig.get("reason") or "").lower()
    regime = str(sig.get("regime") or "").upper()

    if regime == "MARKET_CLOSED" or "market closed" in reason or "closed" in reason and "market" in reason:
        return "DATA_UNAVAILABLE"
    if any(s in reason for s in ("s/r unavailable", "unavailable", "no candle", "insufficient",
                                 "thin chain", "thin_chain", "no chain", "spot unavailable",
                                 "not enough bars", "no data", "stale")):
        return "DATA_UNAVAILABLE"
    if any(s in reason for s in ("conflict", "opposing", "ce/pe confirmation")):
        return "CONFLICTING_SIGNAL"
    if any(s in reason for s in ("no clean state", "no state", "market neutral", "range", "neutral")):
        return "MARKET_NEUTRAL"
    if reason.startswith("filter:") or "blocked" in reason:
        return "FILTER"
    if any(s in reason for s in ("ev gate", "ev_gate", "rr ", "probability", "confidence low",
                                 "quality", "no tradeable contract", "min ")):
        return "GATE"
    if dec == "WATCH":
        return "GATE"
    return "NO_SIGNAL"


def dumps(quality: dict) -> str:
    """Compact JSON for the data_quality column."""
    try:
        return json.dumps(quality, separators=(",", ":"))[:4000]
    except Exception:
        return "{}"
