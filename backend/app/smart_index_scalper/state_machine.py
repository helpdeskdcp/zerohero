"""
Paper-trade entry state machine (spec sections 19 & 26) — slice 4/6.

Pure decision logic. Given a scan result + profile + (optional) open position +
current mark, it returns the next state and an ACTION for the engine to carry
out. It never touches the broker and never bypasses risk controls — the engine
still calls autoscalp.safeguards.check_entry before ENTRY_CONFIRMED.

States:
  NO_TRADE          - evidence insufficient / conflicting / gated
  WATCHING          - a setup is possible but unconfirmed
  SETUP_FORMING     - confluence building, direction taking shape
  ENTRY_READY       - all pre-checks pass except the final confirmation trigger
  ENTRY_CONFIRMED   - open the paper position now
  PAPER_OPEN        - position live, monitoring
  TARGET_RUNNING    - in profit, past T1 or MFE >= 1R
  EXIT_WARNING      - profit fading near a level / momentum weakening -> protect
  EXITED            - closed at target / trail / manual
  STOPPED           - closed at stop
  INVALIDATED       - the structural premise broke before/after entry

ACTIONS: NONE | OPEN_PAPER | UPDATE_MARK | PROTECT | CLOSE
"""
from __future__ import annotations

STATES = ("NO_TRADE", "WATCHING", "SETUP_FORMING", "ENTRY_READY", "ENTRY_CONFIRMED",
          "PAPER_OPEN", "TARGET_RUNNING", "EXIT_WARNING", "EXITED", "STOPPED", "INVALIDATED")

# reason_code substrings that count as a named confirmation
_CONFIRM_KEYS = {
    "level": ("confluence zone", "high-confluence", "nearest zone strength", "pivot", "gann"),
    "oi": ("wall", "oi-action", "pcr", "put support", "call resistance"),
    "volume": ("volume", "x recent average"),
    "price_action": ("candle structure", "reversal candidate", "breakout", "breakdown",
                     "engulf", "hammer", "wick"),
}


def _confirmations_present(reason_codes: list[str]) -> set[str]:
    text = " | ".join(str(x).lower() for x in (reason_codes or []))
    return {name for name, keys in _CONFIRM_KEYS.items() if any(k in text for k in keys)}


def pre_entry_state(scan_row: dict, profile: dict) -> dict:
    """No position yet. Decide WATCHING / SETUP_FORMING / ENTRY_READY /
    ENTRY_CONFIRMED / NO_TRADE from the scan row + profile thresholds.
    ENTRY_CONFIRMED is returned only when EVERY gate passes; the engine then
    still runs safeguards.check_entry."""
    st = "NO_TRADE"
    action = "NONE"
    reasons: list[str] = []

    if scan_row.get("status") != "OK":
        return {"state": "NO_TRADE", "action": "NONE",
                "reason": f"engine status {scan_row.get('status')}: {scan_row.get('missing')}"}
    if not scan_row.get("eligible"):
        return {"state": "NO_TRADE", "action": "NONE",
                "reason": "index not eligible: " + ", ".join(scan_row.get("eligibility", {}).get("failed", []))}

    direction = scan_row.get("direction")
    sig = scan_row.get("signal_type")
    if direction not in ("CE", "PE") or sig not in ("BUY_CE", "BUY_PE"):
        # non-directional: WATCH if a WATCH-type signal, else NO_TRADE
        if sig in ("BREAKOUT_WATCH", "BREAKDOWN_WATCH", "REVERSAL_WATCH"):
            return {"state": "WATCHING", "action": "NONE", "reason": f"{sig} — awaiting direction"}
        return {"state": "NO_TRADE", "action": "NONE",
                "reason": scan_row.get("no_trade_reason") or f"signal_type {sig}"}

    conf = scan_row.get("confidence") or 0
    ssel = scan_row.get("index_selection_score") or 0
    rr = scan_row.get("risk_reward")
    rr1 = rr[0] if isinstance(rr, list) and rr else None
    opt = scan_row.get("selected_option") or {}
    opt_ok = opt.get("status") == "OK"
    opt_score = opt.get("selection_score") or 0

    gates = []
    if conf < profile.get("min_confidence", 72):
        gates.append(f"confidence {conf} < {profile['min_confidence']}")
    if ssel < profile.get("min_selection_score", 68):
        gates.append(f"index_selection_score {ssel} < {profile['min_selection_score']}")
    if rr1 is None or rr1 < profile.get("min_rr1", 1.4):
        gates.append(f"RR1 {rr1} < {profile.get('min_rr1', 1.4)}")
    if not opt_ok:
        gates.append(f"option selection {opt.get('status')} ({opt.get('reason') or opt.get('missing')})")
    elif opt_score < max(30, profile.get("min_selection_score", 68) - 25):
        gates.append(f"option selection_score {opt_score} too low")

    have = _confirmations_present(scan_row.get("reason_codes"))
    need = set(profile.get("required_confirmations", ["level", "price_action"]))
    missing_conf = need - have
    if missing_conf:
        gates.append("missing confirmations: " + ", ".join(sorted(missing_conf)))

    if not gates:
        return {"state": "ENTRY_CONFIRMED", "action": "OPEN_PAPER",
                "reason": f"all gates pass (conf {conf}, sel {ssel}, RR1 {rr1}, "
                          f"opt {opt.get('selected_strike')} {direction} score {opt_score}, "
                          f"confirmations {sorted(have)})"}
    # partial: how close are we?
    if len(gates) == 1 and gates[0].startswith("missing confirmations"):
        return {"state": "ENTRY_READY", "action": "NONE",
                "reason": "one confirmation away: " + gates[0]}
    if conf >= profile.get("min_confidence", 72) * 0.85 and (rr1 or 0) >= profile.get("min_rr1", 1.4) * 0.8:
        return {"state": "SETUP_FORMING", "action": "NONE", "reason": "; ".join(gates)}
    return {"state": "WATCHING", "action": "NONE", "reason": "; ".join(gates)}


def in_trade_state(*, position: dict, mark: float | None, engine_out: dict | None,
                   profile: dict) -> dict:
    """Position is open. Manage: UPDATE_MARK normally; PROTECT/CLOSE on profit
    fade near a level (§30) or on structural invalidation."""
    if mark is None:
        return {"state": "PAPER_OPEN", "action": "NONE", "reason": "no option mark this tick"}
    entry = position.get("entry") or 0.0
    sl = position.get("stop_loss")
    t1 = position.get("target_1")
    risk_ref = position.get("risk_ref") or (abs(entry - sl) if (entry and sl) else None)
    mfe = position.get("mfe") or 0.0
    pnl_pts = mark - entry

    # hard exits are handled by paper_trading.update_trade_price; here we add the
    # discretionary profit-protection layer.
    if sl is not None and mark <= sl:
        return {"state": "STOPPED", "action": "CLOSE", "reason": f"mark {mark} <= SL {sl}"}
    if t1 is not None and mark >= t1:
        state = "TARGET_RUNNING"
    elif risk_ref and mfe >= risk_ref:
        state = "TARGET_RUNNING"
    else:
        state = "PAPER_OPEN"

    # profit fade: was up >= 0.6R, now given back > 45% of the peak, and (if we
    # have a fresh engine read) momentum/direction no longer supports the trade.
    if risk_ref and mfe >= 0.6 * risk_ref:
        give_back = mfe - max(0.0, pnl_pts)
        weak = False
        if engine_out and engine_out.get("status") == "OK":
            want = "CE" if position.get("option_type") == "CE" else "PE"
            weak = engine_out.get("direction") not in (want,) or engine_out.get("signal_type") == "NO_TRADE"
        if give_back > 0.45 * mfe and (weak or give_back > 0.7 * mfe):
            action = "CLOSE" if give_back > 0.7 * mfe else "PROTECT"
            return {"state": "EXIT_WARNING", "action": action,
                    "reason": f"profit fade: MFE {round(mfe, 2)} -> gave back {round(give_back, 2)}"
                              + (" + engine no longer supports the side" if weak else "")}

    # structural invalidation: fresh engine says the opposite side / breakdown of our zone
    if engine_out and engine_out.get("status") == "OK":
        want = "CE" if position.get("option_type") == "CE" else "PE"
        opp = "PE" if want == "CE" else "CE"
        if engine_out.get("direction") == opp and (engine_out.get("confidence") or 0) >= 55:
            return {"state": "INVALIDATED", "action": "CLOSE",
                    "reason": f"engine flipped to {opp} at confidence {engine_out.get('confidence')}"}

    return {"state": state, "action": "UPDATE_MARK", "reason": f"mark {mark}, MFE {round(mfe, 2)}"}
