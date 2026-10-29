"""Parser for the institution's Telegram channel posts (format seen 2026-10-20), e.g.
    #HERO_ZERO_NIFTY  _22400CE / Buy Range : 70-80 / Target : 120/170/260 / Stoploss : 05 / Horizon: Hero Zero
    #SENSEX  _71900 CE / Buy Range : 430-450 / Target : 620/710/850 / Stoploss : 300 / Horizon: Intraday
Mapping (fixed, documented): `Buy Range lo-hi` -> LIMIT buy at `hi` (filled once the ask is at/below it); `Buy above x` -> ABOVE x; expiry is not stated in the post -> the CURRENT (nearest captured) expiry at
message time; targets/stop as posted. Anything not confidently parsed is returned as None (never guessed). Pure functions; no I/O."""
from __future__ import annotations

import re

from ..signal_audit.parse import norm

from .markets import ALLOWED as _MARKET_ALLOWED  # noqa: E402

ALLOWED = set(_MARKET_ALLOWED)
ALIASES = [("GOLDM", "GOLDM"), ("BANKNIFTY", "BANKNIFTY"), ("BANK NIFTY", "BANKNIFTY"), ("MIDCPNIFTY", "MIDCPNIFTY"), ("FINNIFTY", "FINNIFTY"), ("BANKEX", "BANKEX"), ("NIFTY", "NIFTY"), ("SENSEX", "SENSEX"),
           ("NATURALGAS", "NATURALGAS"), ("NATURAL GAS", "NATURALGAS"), ("NATGASMINI", "NATGASMINI"), ("NATGAS", "NATURALGAS"), ("CRUDEOILM", "CRUDEOILM"), ("CRUDEOIL", "CRUDEOIL"), ("CRUDE OIL", "CRUDEOIL"), ("CRUDE", "CRUDEOIL")]
NUM = r"(\d+(?:\.\d+)?)"


CLAIM_POST = re.compile(r"\b(RESULTS?|ACHIEVED|REACHED|REPORT|SL\s*HIT|STOP.?LOSS\s*HIT|TARGETS?\s*HIT|BOOKED|POINTS?\s*MOVE|AFTER\s*CONFIRMATION)\b")


CLAIM_POST2 = re.compile(r"PROFIT\s*DONE|\bCALL\s*NO\s*\d+|HIGH\s*-+\s*\d")


def underlying_of(text: str) -> str | None:
    t = text.upper()
    for k, v in ALIASES:
        if k in t: return v
    return None


def parse_signal(text: str) -> dict:
    """-> {"ok": True, "signal": {...}} or {"ok": False, "reason": ...}"""
    s = norm(text)
    if CLAIM_POST.search(s) or CLAIM_POST2.search(s): return {"ok": False, "reason": "result / claim post (report, achieved, stop-loss hit ...), not an entry signal"}
    m = re.search(r"#\s*([A-Z0-9_ ]*?)\s*_\s*(\d{3,6})\s*(CE|PE)\b", s) or re.search(r"#\s*([A-Z0-9_]+)\s+(\d{3,6})\s*(CE|PE)\b", s)
    if not m:
        r = parse_expiry_header(s)
        if r is not None: return r
        r = parse_mcx(s)
        if r["ok"]: return r
        r2 = parse_loose(s)                                                                  # entry-only / own-stop styles (Neeraj Joshi's channel)
        return r2 if r2 is not None else r
    tag, strike, ot = m.group(1), float(m.group(2)), m.group(3)
    und = underlying_of(tag)
    if und is None: return {"ok": False, "reason": f"unknown underlying in tag {tag!r}"}
    rest = s[m.end():]
    em = re.search(rf"BUY\s*(RANGE|ABOVE|NEAR|AT|ON)?\s*[:\-=]?\s*{NUM}(?:\s*(?:-|TO|/)\s*{NUM})?", rest)
    if not em: return {"ok": False, "reason": "no 'Buy Range' / 'Buy above' line"}
    lo = float(em.group(2)); hi = float(em.group(3)) if em.group(3) else lo
    if hi < lo: lo, hi = hi, lo
    kind = em.group(1) or ""
    mode, entry = ("ABOVE", lo) if kind == "ABOVE" else ("LIMIT", hi)
    tm = re.search(r"TARGETS?\s*[:\-=]?\s*((?:\d+(?:\.\d+)?\s*[/,&]?\s*)+)", rest)
    targets = sorted({float(x) for x in re.findall(r"\d+(?:\.\d+)?", tm.group(1))}) if tm else []
    sm = re.search(rf"(?:STOP\s*LOSS|STOPLOSS|\bSL\b)\s*[:\-=]?\s*{NUM}", rest)
    if not sm: return {"ok": False, "reason": "no stop-loss line"}
    sl = float(sm.group(1))
    hm = re.search(r"HORIZON\s*[:\-=]?\s*([A-Z ]+)", rest); horizon = hm.group(1).strip() if hm else None
    if not (0 < sl < entry): return {"ok": False, "reason": f"inconsistent: stop-loss {sl:g} must be below the entry {entry:g}"}
    targets = [t for t in targets if t > entry]
    if not targets: return {"ok": False, "reason": "no target above the entry"}
    return {"ok": True, "signal": {"underlying": und, "strike": strike, "option_type": ot, "entry_mode": mode, "entry": entry, "entry_range": [lo, hi], "sl": sl, "targets": targets, "horizon": horizon, "tag": tag.strip("_ "), "supported": und in ALLOWED}}


MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6, "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


def parse_expiry_header(s: str) -> dict | None:
    """Header carrying its own expiry, e.g. `NIFTY 17 JULY 25350 PE` (sample from Neeraj Joshi's channel). Entry / stop / targets must be given with explicit keywords
    (ABOVE|BUY|NEAR|ENTRY [lo-hi], SL|STOPLOSS x, TARGET|TGT|TP a/b/c); otherwise the post is refused (-> quarantine), never guessed. The expiry YEAR is not in the post:
    the ingest layer picks the captured expiry with that day and month on/after the post date."""
    m = re.search(r"\b([A-Z]{3,12})\s+(\d{1,2})\s*(?:ST|ND|RD|TH)?\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\s+(\d{3,6})\s*(CE|PE)\b", s)
    if not m: return None
    und = underlying_of(m.group(1))
    if und is None: return {"ok": False, "reason": f"unknown underlying in {m.group(1)!r}"}
    day, mon, strike, ot = int(m.group(2)), MONTHS[m.group(3)], float(m.group(4)), m.group(5)
    rest = s[m.end():]
    em = re.search(rf"(ABOVE|BUY|NEAR|ENTRY|CMP)\s*(?:RANGE|LEVEL|PRICE|AT|ON)?\s*[:\-=]*\s*{NUM}(?:\s*(?:-|TO|/)\s*{NUM})?", rest)
    if not em: return {"ok": False, "reason": "no explicit entry keyword (ABOVE|BUY|NEAR|ENTRY)"}
    lo = float(em.group(2)); hi = float(em.group(3)) if em.group(3) else lo
    if hi < lo: lo, hi = hi, lo
    mode, entry = ("ABOVE", lo) if em.group(1) == "ABOVE" else (("NEAR", lo) if em.group(1) in ("NEAR", "CMP") else ("LIMIT", hi))
    tm = re.search(r"(?:TARGETS?|TGT|TP)\s*[:\-=]*\s*((?:\d+(?:\.\d+)?\s*[/,&\-]?\s*)+)", rest)
    targets = sorted({float(x) for x in re.findall(r"\d+(?:\.\d+)?", tm.group(1))}) if tm else []
    sm = re.search(rf"(?:STOP\s*LOSS|STOPLOSS|\bSL\b)\s*[:\-=]*\s*(?:{NUM}|(VIP|PREMIUM|PAID|PRIVATE|HIDDEN))", rest)
    sl_published = bool(sm and sm.group(1) is not None)          # absent line or "STOPLOSS :- VIP" = not published: the risk module sets it (entry-only channels)
    sl = float(sm.group(1)) if sl_published else None
    if sl_published and not (0 < sl < entry): return {"ok": False, "reason": f"inconsistent: stop-loss {sl:g} must be below the entry {entry:g}"}
    if tm and targets and not [t for t in targets if t > entry]: return {"ok": False, "reason": "no target above the entry"}          # a target line that exists must make sense
    targets = [t for t in targets if t > entry]
    return {"ok": True, "signal": {"underlying": und, "strike": strike, "option_type": ot, "entry_mode": mode, "entry": entry, "entry_range": [lo, hi], "sl": sl, "targets": targets, "horizon": None, "sl_published": sl_published, "targets_published": bool(targets),
                                  "expiry_hint": {"day": day, "month": mon}, "tag": und, "supported": und in ALLOWED}}


def parse_loose(s: str) -> dict | None:
    """Header without '#' and without an expiry date, e.g. `BUY NIFTY 24150 PE`, `SENSEX 74600 CE`, `SENSEX 77600 PE`. The expiry is the current captured one (or the month in an `EXPIRY - OCT` line).
    Entry needs an explicit keyword (ABOVE | BUY RANGE | BUY | NEAR | ENTRY | CMP); stop and targets are optional: `SL - TGT OWN`, `STOPLOSS :- VIP` or no line at all mean "not published" (the
    risk module sets them); `TG - 280 300 320` / `TARGET : 330/350/400` are read when present. Anything inconsistent is refused, never guessed."""
    m = re.search(r"\b(?:BUY\s+)?(NIFTY|BANKNIFTY|FINNIFTY|MIDCPNIFTY|BANKEX|SENSEX|NATURALGAS|NATURAL\s*GAS|CRUDEOILM|CRUDEOIL|CRUDE\s*OIL|GOLDM)\s+(\d{3,6})\s*(CE|PE|CALL|PUT)\b", s)
    if not m: return None
    und = underlying_of(m.group(1)); strike, ot = float(m.group(2)), {"CALL": "CE", "PUT": "PE"}.get(m.group(3), m.group(3))
    if und is None: return {"ok": False, "reason": f"unknown underlying in {m.group(1)!r}"}
    rest = s[m.end():]
    em = re.search(rf"(ABOVE|BUY\s*RANGE|BUY|NEAR|ENTRY|CMP)\s*(?:LEVEL|PRICE|AT|ON)?\s*[:\-=]*\s*{NUM}(?:\s*(?:-|TO|/)\s*{NUM})?", rest)
    if not em: return {"ok": False, "reason": "no explicit entry keyword (ABOVE|BUY RANGE|NEAR|ENTRY)"}
    lo = float(em.group(2)); hi = float(em.group(3)) if em.group(3) else lo
    if hi < lo and em.group(3) and "." not in em.group(2) and "." not in em.group(3) and len(em.group(3)) < len(em.group(2)):          # shortened upper end: "570 - 75" means 570-575
        full = float(em.group(2)[:len(em.group(2)) - len(em.group(3))] + em.group(3)); hi = full if full >= lo else hi
    if hi < lo: lo, hi = hi, lo
    kw = re.sub(r"\s+", " ", em.group(1)); mode, entry = ("ABOVE", lo) if kw == "ABOVE" else (("NEAR", lo) if kw in ("NEAR", "CMP") else ("LIMIT", hi))
    numbered = re.findall(rf"(?:TARGET|TGT|TG|TP|T)\s*[1-9]\s*[:\-=]+\s*{NUM}", rest)                                      # "Target 1: 600 / Target 2: 630 / T3: 650++"
    tm = None if numbered else re.search(r"(?:TARGETS?|TGT|TG|TP)\b\s*[:\-=]*\s*((?:\d+(?:\.\d+)?\s*[/,&\-]?\s*)+)", rest)
    targets = sorted({float(x) for x in numbered}) if numbered else (sorted({float(x) for x in re.findall(r"\d+(?:\.\d+)?", tm.group(1))}) if tm else [])
    if (tm or numbered) and targets and not [t for t in targets if t > entry]: return {"ok": False, "reason": "no target above the entry"}
    targets = [t for t in targets if t > entry]
    sm = re.search(rf"(?:STOP\s*LOSS|STOPLOSS|\bSL\b)\s*[:\-=]*\s*{NUM}", rest)
    sl = float(sm.group(1)) if sm else None                                                  # "SL - TGT OWN", "VIP", "PREMIUM", no line: not published
    if sl is not None and not (0 < sl < entry): return {"ok": False, "reason": f"inconsistent: stop-loss {sl:g} must be below the entry {entry:g}"}
    xm = re.search(r"EXPIRY\s*[:\-=]*\s*([A-Z]{3,9})", rest); hint = {"month": MONTHS[xm.group(1)[:3]]} if xm and xm.group(1)[:3] in MONTHS else None
    return {"ok": True, "signal": {"underlying": und, "strike": strike, "option_type": ot, "entry_mode": mode, "entry": entry, "entry_range": [lo, hi], "sl": sl, "targets": targets, "horizon": None, "sl_published": sl is not None,
                                  "targets_published": bool(targets), **({"expiry_hint": hint} if hint else {}), "tag": und, "supported": und in ALLOWED}}


def parse_mcx(s: str) -> dict:
    """`POSITIONAL GOLDM 147000 PE / NEAR LEVEL 2550 / TARGET -3050/3250/3650 / STOPLOSS - 2050 / EXPIRY - OCT` -> NEAR entry at the level; expiry is a MONTH name (not a date) kept as given."""
    m = re.search(r"(?:POSITIONAL|INTRADAY|BTST)?\s*([A-Z][A-Z ]{2,20}?)\s+(\d{3,6})\s*(CE|PE)\b", s)
    if not m: return {"ok": False, "reason": "no '#TAG _STRIKE CE/PE' header"}
    und = underlying_of(m.group(1))
    if und is None: return {"ok": False, "reason": f"unknown underlying in {m.group(1)!r}"}
    rest = s[m.end():]
    em = re.search(rf"(NEAR|ABOVE|BUY(?:\s*ABOVE)?)\s*(?:LEVEL|PRICE)?\s*[:\-=]?\s*{NUM}", rest)
    if not em: return {"ok": False, "reason": "no 'Near level' / 'Buy above' line"}
    entry = float(em.group(2)); mode = "ABOVE" if "ABOVE" in em.group(1) else "NEAR"
    tm = re.search(r"TARGETS?\s*[:\-=]?\s*((?:\d+(?:\.\d+)?\s*[/,&\-]?\s*)+)", rest)
    targets = sorted({float(x) for x in re.findall(r"\d+(?:\.\d+)?", tm.group(1))}) if tm else []
    sm = re.search(rf"(?:STOP\s*LOSS|STOPLOSS|\bSL\b)\s*[:\-=]?\s*{NUM}", rest)
    if not sm: return {"ok": False, "reason": "no stop-loss line"}
    sl = float(sm.group(1)); xm = re.search(r"EXPIRY\s*[:\-=]?\s*([A-Z]{3,9})", rest)
    if not (0 < sl < entry): return {"ok": False, "reason": f"inconsistent: stop-loss {sl:g} must be below the entry {entry:g}"}
    targets = [t for t in targets if t > entry]
    if not targets: return {"ok": False, "reason": "no target above the entry"}
    return {"ok": True, "signal": {"underlying": und, "strike": float(m.group(2)), "option_type": m.group(3), "entry_mode": mode, "entry": entry, "entry_range": [entry, entry], "sl": sl, "targets": targets, "horizon": "POSITIONAL" if "POSITIONAL" in s else None, "expiry_month": xm.group(1) if xm else None, "tag": und, "supported": und in ALLOWED,
                                  **({"expiry_hint": {"month": MONTHS[xm.group(1)[:3]]}} if xm and xm.group(1)[:3] in MONTHS else {})}}


UPDATES = [("CANCEL", r"\b(TRADE\s*)?CANCEL(L?ED)?\b|\bCANCEL\s*(THIS|THE)?\s*(TRADE|CALL)\b|\bIGNORE\s*(THIS)?\s*(TRADE|CALL)\b"),
           ("SL_HIT", r"\b(SL|STOP\s*LOSS|STOPLOSS)\s*(HIT|TRIGGER(ED)?)\b"),
           ("T_HIT", r"\b(TARGET|TGT|T)\s*(\d)\s*(HIT|DONE|ACHIEVED|ACHEIVED|COMPLETE(D)?)\b|\bTARGET\s*(HIT|DONE|ACHIEVED)\b|\bPOINTS?\s*(ACHIEVED|ACHEIVED)\b|\b\d+\s*POINTS?\b"),
           ("EXIT", r"\b(EXIT|BOOK\s*(ALL|FULL|PROFIT)|SQUARE\s*OFF|SQ\s*OFF)\b")]


def parse_update(text: str) -> dict | None:
    s = norm(text)
    for kind, rx in UPDATES:
        m = re.search(rx, s)
        if m:
            n = next((int(g) for g in m.groups() if g and g.isdigit() and int(g) <= 9), None) if kind == "T_HIT" else None
            return {"kind": kind, "n_target": n}
    return None
