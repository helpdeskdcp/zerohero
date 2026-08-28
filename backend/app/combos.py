"""
Position combos — treat a multi-leg options position (a long strangle:
bought CE + bought PE) as ONE unit for exit purposes.

You cannot get "profit whichever way the market moves" with per-leg price
stops — a per-leg stop on the losing side just locks the loss. What works
is a *combined* exit: watch (CE_ltp + PE_ltp) vs the combined debit paid,
and flatten BOTH legs when the pair is up by the target, or cut both if it
decays to the stop (the flat-market / theta scenario).

Combos live in app_settings['position_combos'] (JSON). Alert + paper-close
only — no broker order is ever sent.
"""
import json
import time
import uuid
from datetime import datetime, timezone

from . import db

KEY = "position_combos"


def _round_tick(x, tick=0.05):
    if x is None:
        return None
    return round(round(x / tick) * tick, 2)


def load() -> dict:
    try:
        raw = db.get_setting(KEY)
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def save(combos: dict):
    db.set_setting(KEY, json.dumps(combos))


def _leg_mark(t: dict):
    """Implied per-unit mark for a leg from its last marked pnl."""
    entry = t.get("entry") or 0
    qty = t.get("quantity") or 0
    if not qty:
        return entry
    sign = 1 if t.get("direction") == "BUY" else -1
    return round(entry + sign * ((t.get("pnl") or 0) / qty), 2)


def create(legs: list[str], kind="STRANGLE", target_combined=None, stop_combined=None,
           trail_combined=None) -> dict:
    rows = [db.get_trade(tid) for tid in legs]
    rows = [r for r in rows if r and r.get("status") == "OPEN"]
    if len(rows) < 2:
        raise ValueError("need >= 2 open legs")
    entry_combined = _round_tick(sum(r.get("entry") or 0 for r in rows))
    if target_combined is None:
        target_combined = _round_tick(entry_combined * 1.30)
    if stop_combined is None:
        stop_combined = _round_tick(entry_combined * 0.80)
    if trail_combined is None:
        trail_combined = _round_tick(entry_combined * 0.15)
    cid = kind[:3].lower() + "-" + uuid.uuid4().hex[:8]
    combos = load()
    combos[cid] = {
        "combo_id": cid, "kind": kind, "legs": [r["trade_id"] for r in rows],
        "entry_combined": entry_combined, "target_combined": target_combined,
        "stop_combined": stop_combined, "trail_combined": trail_combined,
        "peak_combined": entry_combined, "status": "OPEN",
        "created_ts": datetime.now(timezone.utc).isoformat(),
    }
    save(combos)
    return combos[cid]


def set_levels(cid: str, **kw) -> dict:
    combos = load()
    c = combos.get(cid)
    if not c:
        raise KeyError(cid)
    for k in ("target_combined", "stop_combined", "trail_combined"):
        if kw.get(k) is not None:
            c[k] = _round_tick(kw[k])
    save(combos)
    return c


def auto_detect_and_create():
    """If open MANUAL legs form an un-comboed CE+PE pair on one underlying,
    create a STRANGLE combo automatically. Returns list of new combos."""
    combos = load()
    covered = {tid for c in combos.values() if c.get("status") == "OPEN" for tid in c["legs"]}
    by_ul = {}
    for t in db.list_trades(status="OPEN", limit=300, strategy="MANUAL"):
        if t["trade_id"] in covered:
            continue
        by_ul.setdefault(t.get("underlying") or "", []).append(t)
    new = []
    for ul, legs in by_ul.items():
        has_ce = [x for x in legs if (x.get("option_type") or "").upper() == "CE"]
        has_pe = [x for x in legs if (x.get("option_type") or "").upper() == "PE"]
        if has_ce and has_pe:
            picks = [has_ce[0]["trade_id"], has_pe[0]["trade_id"]]
            try:
                new.append(create(picks, kind="STRANGLE"))
            except Exception:
                pass
    return new


def snapshot() -> list[dict]:
    """Live combined figures for every combo, for /api/monitor and the page."""
    out = []
    combos = load()
    for cid, c in combos.items():
        if c.get("status") not in ("OPEN", "BROKEN"):
            continue
        rows = [db.get_trade(t) for t in c["legs"]]
        rows = [r for r in rows if r]
        legs_open = [r for r in rows if r.get("status") == "OPEN"]
        combined_mark = _round_tick(sum(_leg_mark(r) for r in (legs_open or rows)))
        combined_pnl = round(sum(r.get("pnl") or 0 for r in rows), 2)
        entry_c = c["entry_combined"]
        tgt, stp = c.get("target_combined"), c.get("stop_combined")
        # expiry break-evens for a long strangle
        ce = next((r for r in rows if (r.get("option_type") or "").upper() == "CE"), None)
        pe = next((r for r in rows if (r.get("option_type") or "").upper() == "PE"), None)
        be_up = (ce["strike"] + entry_c) if ce and ce.get("strike") else None
        be_dn = (pe["strike"] - entry_c) if pe and pe.get("strike") else None
        out.append({
            "combo_id": cid, "kind": c["kind"], "status": c["status"],
            "legs": [f"{r.get('option_type','')}{int(r.get('strike') or 0)}" for r in rows],
            "leg_ids": c["legs"],
            "entry_combined": entry_c, "combined_mark": combined_mark,
            "combined_pnl": combined_pnl,
            "target_combined": tgt, "stop_combined": stp,
            "trail_combined": c.get("trail_combined"), "peak_combined": c.get("peak_combined"),
            "dist_to_target": _round_tick(tgt - combined_mark) if (tgt and combined_mark is not None) else None,
            "dist_to_stop": _round_tick(combined_mark - stp) if (stp and combined_mark is not None) else None,
            "be_upper": _round_tick(be_up), "be_lower": _round_tick(be_dn),
        })
    return out


def evaluate(notify_fn=None):
    """Monitor-only. Check every OPEN combo; when the combined mark crosses the
    target / stop / trail, fire notify_fn(payload) ONCE per level and record it
    in the combo. Never closes a leg — the app can't square a real position;
    it tells you to. A combo auto-closes only when its legs leave the broker.
    Returns list of freshly-triggered payloads."""
    combos = load()
    triggered = []
    changed = False
    for cid, c in list(combos.items()):
        if c.get("status") != "OPEN":
            continue
        rows = [db.get_trade(t) for t in c["legs"]]
        rows = [r for r in rows if r]
        open_rows = [r for r in rows if r.get("status") == "OPEN"]
        # a strangle needs BOTH legs live — if one closed, the combined
        # target/stop math is meaningless; retire the combo, don't mis-alert
        if len(open_rows) < 2:
            c["status"] = "CLOSED" if not open_rows else "BROKEN"
            c["alerted"] = None
            changed = True
            continue
        combined_mark = round(sum(_leg_mark(r) for r in open_rows), 2)
        peak = max(c.get("peak_combined") or 0, combined_mark)
        if peak != c.get("peak_combined"):
            c["peak_combined"] = round(peak, 2)
            changed = True

        tgt, stp, trail = c.get("target_combined"), c.get("stop_combined"), c.get("trail_combined")
        reason = None
        if tgt and combined_mark >= tgt:
            reason = "COMBO_TARGET"
        elif stp and combined_mark <= stp:
            reason = "COMBO_STOP"
        elif trail and tgt and peak >= tgt and combined_mark <= peak - trail:
            reason = "COMBO_TRAIL"

        # clear the alert latch once back in the neutral band, so a re-cross re-alerts
        if reason is None and c.get("alerted"):
            c["alerted"] = None
            changed = True

        if reason and c.get("alerted") != reason:
            c["alerted"] = reason
            changed = True
            payload = {
                "combo_id": cid, "kind": c["kind"], "reason": reason,
                "entry_combined": c["entry_combined"], "combined_mark": combined_mark,
                "combined_pnl": round(sum(r.get("pnl") or 0 for r in rows), 2),
                "legs": [f"{r.get('option_type','')}{int(r.get('strike') or 0)}" for r in rows],
            }
            triggered.append(payload)
            if notify_fn:
                try:
                    notify_fn(payload)
                except Exception:
                    pass
    if changed:
        save(combos)
    return triggered
