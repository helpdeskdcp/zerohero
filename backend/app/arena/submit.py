"""Write-once, hash-chained submission ledger for the HUMAN side. The server stamps the time; the entry must be submitted BEFORE the trade is taken. Validated, never edited
(corrections are new entries that reference the old id). Allowed: BUY of an option on NIFTY, BANKNIFTY, SENSEX, NATURALGAS, CRUDEOIL (one lot per signal in settlement)."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

from ..forward_test import ledger as L

ALLOWED = ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL", "GOLDM")
MODES = ("ABOVE", "NEAR", "MARKET", "LIMIT")
ACTIONS = ("AMEND_SL", "AMEND_TARGETS", "TRAIL_SL", "EXIT_NOW", "CANCEL", "AMEND_LOTS")
MAX_LOTS = 10
MAX_EVENTS_PER_SIGNAL = 20
PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "human_signals.jsonl")


class SubmissionError(ValueError): pass


def norm_expiry(s: str) -> str:
    """'27OCT2026' | '27-Oct-2026' | '2026-10-27' -> ISO date"""
    s = s.strip().upper()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s): datetime.fromisoformat(s); return s
    m = re.fullmatch(r"(\d{1,2})[- ]?([A-Z]{3})[- ]?(\d{4})", s)
    if not m: raise SubmissionError(f"bad expiry {s!r}")
    return datetime.strptime(f"{m.group(1)}{m.group(2)}{m.group(3)}", "%d%b%Y").date().isoformat()


def validate(d: dict) -> dict:
    need = lambda c, m: (_ for _ in ()).throw(SubmissionError(m)) if not c else None
    u = str(d.get("underlying", "")).upper(); need(u in ALLOWED, f"underlying must be one of {ALLOWED}")
    ot = str(d.get("option_type", "")).upper(); need(ot in ("CE", "PE"), "option_type CE|PE")
    mode = str(d.get("entry_mode", "NEAR")).upper(); need(mode in MODES, f"entry_mode {MODES}")
    num = lambda k: float(d[k]) if d.get(k) is not None else None
    strike, entry, sl = num("strike"), num("entry"), num("sl")
    need(strike and strike > 0, "strike > 0"); need(entry and entry > 0, "entry > 0"); need(sl is None or 0 < sl < entry, "stop-loss is optional; if given it must be below the entry (long options only)")
    tg = sorted({float(x) for x in (d.get("targets") or [])}); need(all(x > entry for x in tg), "targets are optional; if given every target must be above the entry")
    need(str(d.get("participant", "")).strip(), "participant required")
    out = {"participant": str(d["participant"]).strip(), "underlying": u, "expiry": norm_expiry(str(d["expiry"])), "strike": strike, "option_type": ot, "entry_mode": mode, "entry": entry, "sl": sl,
           "targets": tg, "note": str(d.get("note", ""))[:300]}
    if d.get("entry_hi") is not None:                                                    # "Above 570 - 575": trigger at 570, never buy above 575 (the zone)
        eh = float(d["entry_hi"]); need(eh >= entry, "entry zone upper end must be at or above the entry")
        if eh > entry: out["entry_hi"] = eh
    if d.get("lots") is not None:
        try: lots = int(d["lots"])
        except (TypeError, ValueError): lots = 0
        need(1 <= lots <= MAX_LOTS and lots == float(d["lots"]), f"quantity must be a whole number of lots, 1 to {MAX_LOTS}"); out["lots"] = lots
    if d.get("valid_sec") is not None:
        vs = int(d["valid_sec"]); need(60 <= vs <= 28800, "valid_sec between 60 and 28800"); out["valid_sec"] = vs
    return out


def submit(d: dict, path: str | None = None, now: float | None = None, practice: bool = False) -> dict:
    p = path or PATH; rec = validate(d)
    n = sum(1 for r in load(p) if r.get("type") == "HUMAN_SIGNAL")
    rec.update(type="HUMAN_SIGNAL", practice=bool(practice), id=f"H{n + 1:05d}", server_ts=(now or time.time()), server_iso=datetime.fromtimestamp(now or time.time(), tz=timezone.utc).isoformat())
    return L.append(p, rec)


def load(path: str | None = None) -> list[dict]:
    import json
    p = path or PATH
    return [json.loads(x) for x in open(p) if x.strip()] if os.path.exists(p) else []


def validate_event(d: dict) -> dict:
    """A management event for an already submitted signal. Effective from its SERVER timestamp (+ execution delay); never retroactive."""
    need = lambda c, m: (_ for _ in ()).throw(SubmissionError(m)) if not c else None
    a = str(d.get("action", "")).upper(); need(a in ACTIONS, f"action must be one of {ACTIONS}")
    out = {"signal_id": str(d.get("signal_id", "")).strip(), "action": a}; need(out["signal_id"], "signal_id required")
    if a == "AMEND_SL": sl = float(d.get("sl") or 0); need(sl > 0, "sl > 0"); out["sl"] = sl
    elif a == "AMEND_LOTS":
        try: lots = int(d.get("lots"))
        except (TypeError, ValueError): lots = 0
        need(1 <= lots <= MAX_LOTS and lots == float(d["lots"]), f"quantity must be a whole number of lots, 1 to {MAX_LOTS}"); out["lots"] = lots
    elif a == "AMEND_TARGETS":
        tg = sorted({float(x) for x in (d.get("targets") or [])}); need(tg and all(x > 0 for x in tg), "targets: at least one value > 0"); out["targets"] = tg
    elif a == "TRAIL_SL":
        pts, pct = d.get("trail_points"), d.get("trail_pct"); need((pts is None) != (pct is None), "give exactly one of trail_points or trail_pct")
        if pts is not None: need(float(pts) > 0, "trail_points > 0"); out["trail_points"] = float(pts)
        else: need(0 < float(pct) < 100, "0 < trail_pct < 100"); out["trail_pct"] = float(pct)
    out["note"] = str(d.get("note", ""))[:200]
    return out


def submit_event(d: dict, participant: str, path: str | None = None, now: float | None = None) -> dict:
    p = path or PATH; ev = validate_event(d); recs = load(p)
    pm = participant_map(recs)
    sig = next((r for r in recs if r.get("type") == "HUMAN_SIGNAL" and r["id"] == ev["signal_id"] and pm.get(r["id"]) == participant), None)
    if sig is None: raise SubmissionError("unknown signal_id for this participant")
    nev = [r for r in recs if r.get("type") == "HUMAN_EVENT" and r["signal_id"] == ev["signal_id"]]
    if len(nev) >= MAX_EVENTS_PER_SIGNAL: raise SubmissionError(f"at most {MAX_EVENTS_PER_SIGNAL} management events per signal")
    t = now or time.time()
    ev.update(type="HUMAN_EVENT", participant=participant, practice=bool(sig.get("practice")), id=f"E{sum(1 for r in recs if r.get('type') == 'HUMAN_EVENT') + 1:05d}", server_ts=t,
              server_iso=datetime.fromtimestamp(t, tz=timezone.utc).isoformat())
    return L.append(p, ev)


def submit_ts_correction(signal_id: str, effective_ts: float, reason: str, path: str | None = None) -> dict:
    """Corrects the effective instant of a CHANNEL-INGESTED signal to the Telegram post time. EARLIER only (an infrastructure-latency fix: a late reader must not penalise the poster); it can never move a signal later."""
    p = path or PATH; recs = load(p)
    sig = next((r for r in recs if r.get("type") == "HUMAN_SIGNAL" and r["id"] == signal_id), None)
    if sig is None: raise SubmissionError("unknown signal_id")
    if "via channel post" not in (sig.get("note") or ""): raise SubmissionError("only channel-ingested signals can be corrected")
    if not (effective_ts < sig["server_ts"]): raise SubmissionError("the corrected time must be earlier than the recorded server time")
    n = sum(1 for r in recs if r.get("type") == "HUMAN_TS_CORRECTION")
    return L.append(p, {"type": "HUMAN_TS_CORRECTION", "id": f"C{n + 1:05d}", "signal_id": signal_id, "effective_ts": float(effective_ts), "reason": reason[:300], "server_ts": time.time()})


def participant_map(recs: list[dict]) -> dict:
    """signal id -> effective participant (the original, unless a HUMAN_PARTICIPANT_CORRECTION re-attributed it)."""
    m = {r["id"]: r["participant"] for r in recs if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL"}
    for r in recs:
        if r.get("type") == "HUMAN_PARTICIPANT_CORRECTION" and r["signal_id"] in m: m[r["signal_id"]] = r["participant"]
    return m


def submit_participant_correction(signal_id: str, participant: str, reason: str, path: str | None = None) -> dict:
    """Re-attributes a manually submitted signal to the participant who really made it (the ledger row itself stays immutable). Channel-ingested signals cannot be re-attributed."""
    p = path or PATH; recs = load(p)
    sig = next((r for r in recs if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL" and r["id"] == signal_id), None)
    if sig is None: raise SubmissionError("unknown signal_id")
    if "via channel post" in (sig.get("note") or ""): raise SubmissionError("channel-ingested signals cannot be re-attributed")
    if not str(participant).strip(): raise SubmissionError("participant required")
    n = sum(1 for r in recs if r.get("type") == "HUMAN_PARTICIPANT_CORRECTION")
    return L.append(p, {"type": "HUMAN_PARTICIPANT_CORRECTION", "id": f"P{n + 1:05d}", "signal_id": signal_id, "participant": str(participant).strip().upper(), "reason": reason[:300], "server_ts": time.time()})


def submit_validity_correction(signal_id: str, valid_sec: int, reason: str, path: str | None = None) -> dict:
    """Applies the fill window (valid_sec) to a CHANNEL-INGESTED signal that was ingested before the window existed (pre-competition rehearsal). Channel signals only; never overrides a validity the row already has."""
    p = path or PATH; recs = load(p)
    sig = next((r for r in recs if r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL" and r["id"] == signal_id), None)
    if sig is None: raise SubmissionError("unknown signal_id")
    if "via channel post" not in (sig.get("note") or ""): raise SubmissionError("only channel-ingested signals")
    if sig.get("valid_sec"): raise SubmissionError("this signal already has a validity window")
    if not (60 <= int(valid_sec) <= 28800): raise SubmissionError("valid_sec between 60 and 28800")
    n = sum(1 for r in recs if r.get("type") == "HUMAN_VALIDITY_CORRECTION")
    return L.append(p, {"type": "HUMAN_VALIDITY_CORRECTION", "id": f"V{n + 1:05d}", "signal_id": signal_id, "valid_sec": int(valid_sec), "reason": reason[:300], "server_ts": time.time()})


def submit_status_correction(signal_id: str, status: str, reason: str, path: str | None = None) -> dict:
    """Voids a signal after the fact (the original row is never touched): status DATA_GAP (no quote around the execution instant) or INVALID_LEVELS (levels built from a stale entry)."""
    p = path or PATH; recs = load(p)
    if status not in ("DATA_GAP", "INVALID_LEVELS"): raise SubmissionError("status must be DATA_GAP or INVALID_LEVELS")
    if not any(r.get("type", "HUMAN_SIGNAL") == "HUMAN_SIGNAL" and r["id"] == signal_id for r in recs): raise SubmissionError("unknown signal_id")
    if any(r.get("type") == "HUMAN_STATUS_CORRECTION" and r["signal_id"] == signal_id for r in recs): raise SubmissionError("signal already voided")
    n = sum(1 for r in recs if r.get("type") == "HUMAN_STATUS_CORRECTION")
    return L.append(p, {"type": "HUMAN_STATUS_CORRECTION", "id": f"S{n + 1:05d}", "signal_id": signal_id, "status": status, "reason": reason[:400], "server_ts": time.time()})


def default_lots(participant: str, participants_file: str | None = None) -> int:
    """Default quantity (lots) of a profile: `default_lots` in data/arena/participants.json, else 1."""
    import json as _j
    f = participants_file or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "arena", "participants.json")
    try: n = int((_j.load(open(f, encoding="utf-8")).get(str(participant).upper(), {}) or {}).get("default_lots", 1))
    except Exception: n = 1
    return n if 1 <= n <= MAX_LOTS else 1
