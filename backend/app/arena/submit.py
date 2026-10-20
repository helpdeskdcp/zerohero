"""Write-once, hash-chained submission ledger for the HUMAN side. The server stamps the time; the entry must be submitted BEFORE the trade is taken. Validated, never edited
(corrections are new entries that reference the old id). Allowed: BUY of an option on NIFTY, BANKNIFTY, SENSEX, NATURALGAS, CRUDEOIL (one lot per signal in settlement)."""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

from ..forward_test import ledger as L

ALLOWED = ("NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL")
MODES = ("ABOVE", "NEAR", "MARKET")
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
    need(strike and strike > 0, "strike > 0"); need(entry and entry > 0, "entry > 0"); need(sl is not None and 0 < sl < entry, "0 < sl < entry (long options only)")
    tg = sorted({float(x) for x in (d.get("targets") or [])}); need(tg and all(x > entry for x in tg), "at least one target, all above entry")
    need(str(d.get("participant", "")).strip(), "participant required")
    return {"participant": str(d["participant"]).strip(), "underlying": u, "expiry": norm_expiry(str(d["expiry"])), "strike": strike, "option_type": ot, "entry_mode": mode, "entry": entry, "sl": sl,
            "targets": tg, "note": str(d.get("note", ""))[:300]}


def submit(d: dict, path: str | None = None, now: float | None = None) -> dict:
    p = path or PATH; rec = validate(d)
    n = sum(1 for line in open(p) if line.strip()) if os.path.exists(p) else 0
    rec.update(type="HUMAN_SIGNAL", id=f"H{n + 1:05d}", server_ts=(now or time.time()), server_iso=datetime.fromtimestamp(now or time.time(), tz=timezone.utc).isoformat())
    return L.append(p, rec)


def load(path: str | None = None) -> list[dict]:
    import json
    p = path or PATH
    return [json.loads(x) for x in open(p) if x.strip()] if os.path.exists(p) else []
