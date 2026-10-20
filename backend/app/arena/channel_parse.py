"""Parser for the institution's Telegram channel posts (format seen 2026-10-20), e.g.
    #HERO_ZERO_NIFTY  _22400CE / Buy Range : 70-80 / Target : 120/170/260 / Stoploss : 05 / Horizon: Hero Zero
    #SENSEX  _71900 CE / Buy Range : 430-450 / Target : 620/710/850 / Stoploss : 300 / Horizon: Intraday
Mapping (fixed, documented): `Buy Range lo-hi` -> LIMIT buy at `hi` (filled once the ask is at/below it); `Buy above x` -> ABOVE x; expiry is not stated in the post -> the CURRENT (nearest captured) expiry at
message time; targets/stop as posted. Anything not confidently parsed is returned as None (never guessed). Pure functions; no I/O."""
from __future__ import annotations

import re

from ..signal_audit.parse import norm

ALLOWED = {"NIFTY", "BANKNIFTY", "SENSEX", "NATURALGAS", "CRUDEOIL"}
ALIASES = [("GOLDM", "GOLDM"), ("BANKNIFTY", "BANKNIFTY"), ("BANK NIFTY", "BANKNIFTY"), ("MIDCPNIFTY", "MIDCPNIFTY"), ("FINNIFTY", "FINNIFTY"), ("BANKEX", "BANKEX"), ("NIFTY", "NIFTY"), ("SENSEX", "SENSEX"),
           ("NATURALGAS", "NATURALGAS"), ("NATURAL GAS", "NATURALGAS"), ("NATGASMINI", "NATGASMINI"), ("NATGAS", "NATURALGAS"), ("CRUDEOILM", "CRUDEOILM"), ("CRUDEOIL", "CRUDEOIL"), ("CRUDE OIL", "CRUDEOIL"), ("CRUDE", "CRUDEOIL")]
NUM = r"(\d+(?:\.\d+)?)"


def underlying_of(text: str) -> str | None:
    t = text.upper()
    for k, v in ALIASES:
        if k in t: return v
    return None


def parse_signal(text: str) -> dict:
    """-> {"ok": True, "signal": {...}} or {"ok": False, "reason": ...}"""
    s = norm(text)
    m = re.search(r"#\s*([A-Z0-9_ ]*?)\s*_\s*(\d{3,6})\s*(CE|PE)\b", s) or re.search(r"#\s*([A-Z0-9_]+)\s+(\d{3,6})\s*(CE|PE)\b", s)
    if not m: return parse_mcx(s)
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
    return {"ok": True, "signal": {"underlying": und, "strike": float(m.group(2)), "option_type": m.group(3), "entry_mode": mode, "entry": entry, "entry_range": [entry, entry], "sl": sl, "targets": targets, "horizon": "POSITIONAL" if "POSITIONAL" in s else None, "expiry_month": xm.group(1) if xm else None, "tag": und, "supported": und in ALLOWED}}


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
