"""
Phase 10 -- shadow replay harness. Compares up to 4 named "engines"
(baseline/confirmation/xgboost/groq, passed in as plain callables so this
module never imports any of them directly) over a chronological list of
research events.

MINIMUM SAMPLE: below `_MIN_EVENTS` events, this returns
{"status": "INSUFFICIENT_SAMPLE"} rather than a metrics dict -- a
precision/recall number computed from a handful of events looks
authoritative but isn't, and this project's own rules (Phase 10 of the
spec, and this session's repeated overfitting findings) require saying so
plainly instead of publishing it.
"""
from __future__ import annotations

_MIN_EVENTS = 30

_TRADE_STATES = {"CONFIRMED"}
_NO_TRADE_STATES = {"NO_TRADE", "WATCH", "SETUP_FORMING", "INVALIDATED", "REVIEW"}


def _engine_called_trade(state: str | None) -> bool:
    return state in _TRADE_STATES


def _outcome_was_win(event: dict) -> bool | None:
    outcome = (event.get("outcome") or "").upper()
    if outcome in ("TARGET_ACHIEVED", "PARTIAL_BOOKED"):
        return True
    if outcome in ("SL_HIT", "COST_TO_COST"):
        return False
    return None   # unknown/unlabelled outcome -- never guessed as a win or loss


def replay_engine(events: list[dict], engine_fn) -> dict:
    """`engine_fn(event) -> {"state": ...}` for one engine. Returns
    INSUFFICIENT_SAMPLE below _MIN_EVENTS, else the metrics dict."""
    if len(events) < _MIN_EVENTS:
        return {"status": "INSUFFICIENT_SAMPLE", "n_events": len(events), "required": _MIN_EVENTS}

    tp = fp = tn = fn = 0
    no_trade_count = invalidated_count = 0
    wins_r = []
    maes, mfes, times_to_target = [], [], []

    for event in events:
        result = engine_fn(event) or {}
        state = result.get("state")
        called_trade = _engine_called_trade(state)
        if state == "INVALIDATED":
            invalidated_count += 1
        if state in _NO_TRADE_STATES:
            no_trade_count += 1

        actual_win = _outcome_was_win(event)
        if called_trade and actual_win is True:
            tp += 1
        elif called_trade and actual_win is False:
            fp += 1
        elif not called_trade and actual_win is False:
            tn += 1
        elif not called_trade and actual_win is True:
            fn += 1
        # actual_win is None (unlabelled outcome) contributes to none of
        # the 4 buckets -- it cannot be scored either way.

        if called_trade:
            entry, exit_ = event.get("entry"), event.get("exit")
            if entry is not None and exit_ is not None:
                wins_r.append(exit_ - entry)
            if event.get("mae") is not None:
                maes.append(event["mae"])
            if event.get("mfe") is not None:
                mfes.append(event["mfe"])

    n_scored = tp + fp + tn + fn
    precision = round(tp / (tp + fp), 4) if (tp + fp) else None
    recall = round(tp / (tp + fn), 4) if (tp + fn) else None
    expectancy = round(sum(wins_r) / len(wins_r), 4) if wins_r else None

    return {
        "status": "OK", "n_events": len(events), "n_scored": n_scored,
        "true_positive": tp, "false_positive": fp, "true_negative": tn, "false_negative": fn,
        "precision": precision, "recall": recall, "expectancy": expectancy,
        "avg_mae": round(sum(maes) / len(maes), 4) if maes else None,
        "avg_mfe": round(sum(mfes) / len(mfes), 4) if mfes else None,
        "no_trade_rate": round(no_trade_count / len(events), 4),
        "invalidation_rate": round(invalidated_count / len(events), 4),
        "time_to_target": None,  # requires intraday MFE-timestamp data not in schema today
    }


def replay_compare(events: list[dict], engines: dict) -> dict:
    """`engines`: {"baseline": fn, "confirmation": fn, "xgboost": fn,
    "groq": fn} -- any subset. Each engine is replayed independently;
    the whole comparison is gated by the same _MIN_EVENTS floor."""
    if len(events) < _MIN_EVENTS:
        return {"status": "INSUFFICIENT_SAMPLE", "n_events": len(events), "required": _MIN_EVENTS}
    return {"status": "OK", "n_events": len(events),
           "results": {name: replay_engine(events, fn) for name, fn in engines.items()}}
