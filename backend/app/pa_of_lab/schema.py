"""Canonical bar schema + adapters for the PA/OF research lab. RESEARCH ONLY.

Data provenance classes (never merged, never relabelled):
  ACTUAL_QUOTE      best bid/ask OBSERVED in the Dhan Full packet (dhan_parser._parse_full depth[0]); a real quote observation.
  CLASSIFIED_PROXY  aggressor side of a trade INFERRED from that quote (QUOTE_RULE) or from the previous price (TICK_RULE).
                    This is an estimate. It is NEVER broker-confirmed buy/sell volume.
  CANDLE_DERIVED    anything computed from OHLCV candles only. Cannot establish footprint, absorption or book imbalance.

Canonical bar (dict):
  symbol, kind, tf_sec, t (bar START, epoch seconds UTC), o,h,l,c, volume (sum of ltq of every accepted trade, incl. UNKNOWN side),
  buy, sell (inferred aggressor volume), unknown, delta (=buy-sell), n_ticks, quote_share, spread_mean, gap_in_bar, closed,
  levels ({price: [buy, sell]} or None), side_source, dq {status, reasons}
Timestamps: `t` is always UTC epoch seconds of the bar START; IST = t + 19800. Dhan `ltt` is corrected to real UTC in dhan_parser.
"""
from __future__ import annotations

IST_SEC = 19800
SRC_QUOTE, SRC_PROXY, SRC_CANDLE = "ACTUAL_QUOTE", "CLASSIFIED_PROXY", "CANDLE_DERIVED"
SIDE_NOTE = "aggressor side is INFERRED (quote rule / tick rule), not broker-confirmed"

# data-quality thresholds (documented, UNCALIBRATED; a bar below them is never used as order-flow evidence)
DQ_MIN_TICKS = 30
DQ_MIN_QUOTE_SHARE = 0.60
DQ_MAX_UNKNOWN_FRAC = 0.10


def dq_of(n_ticks: int, quote_share: float | None, unknown_frac: float | None, gap_in_bar: bool, closed: bool) -> dict:
    r = []
    if not closed: r.append("BAR_NOT_CLOSED")
    if gap_in_bar: r.append("FEED_GAP_IN_BAR")
    if n_ticks < DQ_MIN_TICKS: r.append(f"FEW_TICKS<{DQ_MIN_TICKS}")
    if quote_share is None or quote_share < DQ_MIN_QUOTE_SHARE: r.append(f"QUOTE_RULE_SHARE<{DQ_MIN_QUOTE_SHARE}")
    if unknown_frac is None or unknown_frac > DQ_MAX_UNKNOWN_FRAC: r.append(f"UNKNOWN_SIDE>{DQ_MAX_UNKNOWN_FRAC}")
    status = "OK" if not r else ("INSUFFICIENT" if (n_ticks < DQ_MIN_TICKS or gap_in_bar or not closed) else "DEGRADED")
    return {"status": status, "reasons": r}


def side_source(methods: dict) -> str:
    q = sum(v for k, v in methods.items() if k.startswith("QUOTE_RULE"))
    t = sum(v for k, v in methods.items() if not k.startswith("QUOTE_RULE"))
    if q and not t: return "INFERRED_QUOTE_RULE"
    if t and not q: return "INFERRED_TICK_RULE"
    return "INFERRED_MIXED" if q and t else "NONE"


def footprint_to_canonical(fp: dict, symbol: str, kind: str, *, closed: bool = True, gap_in_bar: bool = False) -> dict:
    """Adapter for app.engines.orderflow_engine.FootprintBar.to_dict() (live engine output) -> canonical bar.
    FootprintBar uses open/high/low/close/buy_vol/sell_vol; orderflow.py needs o/h/l/c/buy/sell. This is the ONLY place the rename happens.
    Fields FootprintBar.to_dict does not carry (volume of UNKNOWN side, levels, spread) are None, not invented."""
    m = fp.get("classification_methods") or {}
    n = sum(m.values())
    qs = (sum(v for k, v in m.items() if k.startswith("QUOTE_RULE")) / n) if n else None
    cl = (fp.get("buy_vol") or 0) + (fp.get("sell_vol") or 0)
    return {"symbol": symbol, "kind": kind, "tf_sec": fp["bar_sec"], "t": fp["bar_start"],
            "o": fp["open"], "h": fp["high"], "l": fp["low"], "c": fp["close"],
            "volume": None, "buy": fp["buy_vol"], "sell": fp["sell_vol"], "unknown": None, "delta": fp["buy_vol"] - fp["sell_vol"],
            "n_ticks": n, "quote_share": qs, "spread_mean": None, "gap_in_bar": gap_in_bar, "closed": closed, "levels": None,
            "side_source": side_source(m), "dq": dq_of(n, qs, None, gap_in_bar, closed), "classified_vol": cl, "provenance": SRC_PROXY}


def to_orderflow_input(b: dict) -> dict:
    """canonical bar -> the dict app/pa_of_lab/orderflow.py reads (o,h,l,c,buy,sell). Refuses bars without inferred volumes."""
    if b.get("buy") is None or b.get("sell") is None:
        raise ValueError("bar has no buy/sell volume (candle-derived bar cannot feed the order-flow engine)")
    return {"t": b["t"], "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "buy": b["buy"], "sell": b["sell"]}
