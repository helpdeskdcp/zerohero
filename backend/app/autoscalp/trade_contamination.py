"""
Phase F -- retrospective data-quality classification for CLOSED historical
paper trades. Distinct from app.autoscalp.data_quality (which grades a
LIVE signal's inputs at entry time); this module grades an already-closed
trade ROW after the fact, to decide whether it's valid evidence for
performance/calibration analysis.

Read-only: never mutates or deletes a row, only classifies it. Contaminated
rows are reported with their reason, never silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

# The exact moment the second (and, per the app/runtime.py::_autoscalp_chain
# git-history audit, final) SENSEX/BANKEX exchange-routing bug was fixed
# (commit a910e1c). Any BSE-segment trade opened before this is PRE_FIX and
# must not be pooled with anything collected after it.
BSE_ROUTING_FIX_TS = datetime(2026, 9, 18, 17, 19, 57, tzinfo=timezone.utc)   # 22:49:57 IST


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@dataclass
class ContaminationResult:
    valid: bool
    reasons: list            # empty if valid
    epoch: str                # "PRE_FIX" | "POST_FIX" | "N/A" (non-BSE symbols)


def classify_trade(t: dict) -> ContaminationResult:
    """Classify one CLOSED ai_paper_trades row. Checks, in order:
      - TIME_NODATA exit reason (the WS feed never marked this position)
      - entry_price == exit_price (the exact fingerprint of a phantom fill)
      - missing/None exit_price on a CLOSED row (should never happen; flag if it does)
      - invalid timestamp ordering (closed before/at open)
      - invalid exchange routing (BSE-segment leg subscribed under NFO(2) --
        inferred from the TIME_NODATA+zero-movement combination pre-fix,
        since the raw WS exchange_type used at subscribe time isn't itself
        stored on the trade row)
    """
    reasons = []
    market = str(t.get("market") or "").upper()
    exit_reason = t.get("exit_reason")
    entry = t.get("entry")
    exit_price = t.get("exit_price")
    opened = _parse_ts(t.get("opened_ts"))
    closed = _parse_ts(t.get("closed_ts"))

    if exit_reason == "TIME_NODATA":
        reasons.append("TIME_NODATA")
    if entry is not None and exit_price is not None and entry == exit_price:
        reasons.append("entry_price_equals_exit_price")
    if t.get("status") == "CLOSED" and exit_price is None:
        reasons.append("missing_exit_price_on_closed_row")
    if opened and closed and closed <= opened:
        reasons.append("invalid_timestamp_ordering")
    if exit_reason == "TIME_NODATA" and market == "BSE":
        reasons.append("bse_exchange_routing_contamination")

    epoch = "N/A"
    if market == "BSE" and opened is not None:
        epoch = "PRE_FIX" if opened < BSE_ROUTING_FIX_TS else "POST_FIX"

    return ContaminationResult(valid=not reasons, reasons=reasons, epoch=epoch)


def data_quality_report(trades: list) -> dict:
    """Aggregate report over a list of CLOSED trade rows -- never silently
    discards anything; every excluded row's reason is enumerated."""
    total = len(trades)
    valid_rows, contaminated_rows = [], []
    reason_counts: dict = {}
    pre_fix = post_fix = 0
    for t in trades:
        r = classify_trade(t)
        if r.valid:
            valid_rows.append(t)
        else:
            contaminated_rows.append({"trade_id": t.get("trade_id"), "reasons": r.reasons,
                                      "epoch": r.epoch})
            for reason in r.reasons:
                reason_counts[reason] = reason_counts.get(reason, 0) + 1
        if r.epoch == "PRE_FIX":
            pre_fix += 1
        elif r.epoch == "POST_FIX":
            post_fix += 1

    return {
        "total_rows": total,
        "valid_rows": len(valid_rows),
        "contaminated_rows": len(contaminated_rows),
        "excluded_trade_ids": [c["trade_id"] for c in contaminated_rows],
        "exclusion_reasons": reason_counts,
        "bse_pre_fix_count": pre_fix,
        "bse_post_fix_count": post_fix,
        "detail": contaminated_rows,
    }
