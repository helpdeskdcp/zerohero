"""Heuristic parser for Indian option-signal messages ("BUY NIFTY 24500 CE ABOVE 120 SL 100 TGT 140/160/180") and follow-up claims ("T1 DONE", "SL HIT").
Pure functions. Anything not confidently parsed is returned as None/UNPARSED and listed in the audit report - the parser never guesses a price.
Formats vary by channel: run the audit, read `unparsed.txt`, then extend the regexes + add a test with the real message."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

UNDERLYINGS = ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTY", "SENSEX", "BANKEX")
MONTHS = {m: i + 1 for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split())}
NUM = r"(\d{1,6}(?:\.\d{1,2})?)"


@dataclass
class Signal:
    msg_id: int
    t: int                       # message epoch seconds (UTC)
    date_ist: str
    underlying: str
    strike: float
    opt_type: str                # CE | PE
    side: str                    # BUY | SELL
    entry_lo: float
    entry_hi: float
    entry_mode: str              # ABOVE (trigger) | NEAR (limit/CMP/at) | RANGE
    sl: float | None
    targets: list[float] = field(default_factory=list)
    expiry: str | None = None    # ISO date if stated
    edited: bool = False
    reply_to: int | None = None
    text: str = ""


@dataclass
class Update:
    msg_id: int
    t: int
    kind: str                    # T_HIT | SL_HIT | EXIT | OTHER
    n_target: int | None
    ref_id: int | None
    text: str = ""


def norm(text: str) -> str:
    s = (text or "").upper().replace("₹", " ").replace("RS.", " ").replace("RS", " ")
    s = re.sub(r"(?<=\d),(?=\d{3}\b)", "", s)                                   # 24,500 -> 24500
    s = re.sub(r"[^\x00-\x7F]", " ", s)                                         # emojis / arrows
    return re.sub(r"[ \t]+", " ", s)


def _nums(chunk: str) -> list[float]:
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", chunk)]


def parse_signal(msg_id: int, t: int, date_ist: str, text: str, edited: bool = False, reply_to: int | None = None) -> Signal | None:
    s = norm(text)
    und = next((u for u in UNDERLYINGS if re.search(rf"\b{u}\b|{u}(?=\d)", s.replace("BANK NIFTY", "BANKNIFTY"))), None)
    s = s.replace("BANK NIFTY", "BANKNIFTY")
    m = re.search(r"\b(\d{4,6})\s*(CE|PE|CALL|PUT)\b", s) or re.search(r"\b(\d{4,6})(CE|PE)\b", s)
    if not m:
        m2 = re.search(r"\b(CE|PE|CALL|PUT)\s*(\d{4,6})\b", s)
        if not m2: return None
        strike, ot, span = float(m2.group(2)), m2.group(1), m2.span()
    else:
        strike, ot, span = float(m.group(1)), m.group(2), m.span()
    if und is None: return None
    ot = "CE" if ot in ("CE", "CALL") else "PE"
    rest = s[:span[0]] + " " + s[span[1]:]
    side = "SELL" if re.search(r"\b(SELL|SHORT)\b", rest) else "BUY"
    sl_m = re.search(rf"(?:\bSL\b|STOP\s*LOSS|STOPLOSS|\bS/L\b)\s*[:\-=]?\s*{NUM}", rest)
    sl = float(sl_m.group(1)) if sl_m else None
    tg = []
    tm = re.search(r"(?:\bTGT\b|\bTARGETS?\b|\bTP\b)\s*[:\-=]?\s*((?:\d+(?:\.\d+)?\s*[/,\-&+]?\s*(?:AND)?\s*)+)", rest)
    if tm: tg = _nums(tm.group(1))
    for tn in re.finditer(rf"\bT(\d)\s*[:\-=]\s*{NUM}", rest): tg.append(float(tn.group(2)))
    tg = sorted(set(tg))
    em = re.search(rf"(ABOVE|NEAR|AROUND|CMP|AT|ENTRY|BUY ON|ON|@)\s*[:\-=]?\s*{NUM}(?:\s*(?:-|TO|/)\s*{NUM})?", rest)
    if not em: return None
    lo = float(em.group(2)); hi = float(em.group(3)) if em.group(3) else lo
    if hi < lo: lo, hi = hi, lo
    mode = "ABOVE" if em.group(1) == "ABOVE" else ("RANGE" if hi > lo else "NEAR")
    if sl is None and not tg: return None
    if side == "BUY":
        if sl is not None and sl >= lo: return None                              # inconsistent message: do not guess
        tg = [x for x in tg if x > hi]
    exp = None
    ex = re.search(r"\b(\d{1,2})\s*(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)[A-Z]*\b", rest)
    if ex:
        y = int(date_ist[:4]); mo = MONTHS[ex.group(2)]; d = int(ex.group(1))
        try:
            cand = date(y, mo, d)
            if (cand - date.fromisoformat(date_ist)).days < -60: cand = date(y + 1, mo, d)
            exp = cand.isoformat()
        except ValueError: exp = None
    return Signal(msg_id, t, date_ist, und, strike, ot, side, lo, hi, mode, sl, tg, exp, edited, reply_to, text)


UPD = [("SL_HIT", r"\b(SL|STOP\s*LOSS|STOPLOSS)\s*(HIT|TRIGGERED|TRIGGER|ACHIEVED)\b|\bSL\s*HIT\b"),
       ("T_HIT", r"\b(T|TGT|TARGET)\s*(\d)\s*(DONE|HIT|ACHIEVED|ACHEIVED|COMPLETE|COMPLETED)\b|\bTARGET\s*(DONE|ACHIEVED|HIT)\b|\bPROFIT\s*BOOK(ED)?\b|\bBOOK(ED)?\s*PROFIT\b"),
       ("EXIT", r"\b(EXIT|SQUARE\s*OFF|SQ\s*OFF|CLOSE[D]?)\b")]


def parse_update(msg_id: int, t: int, text: str, reply_to: int | None) -> Update | None:
    s = norm(text)
    for kind, rx in UPD:
        m = re.search(rx, s)
        if m:
            n = next((int(g) for g in m.groups() if g and g.isdigit()), None)
            return Update(msg_id, t, kind, n, reply_to, text)
    return None


def link_updates(signals: list[Signal], updates: list[Update]) -> dict[int, list[Update]]:
    """update -> signal: explicit reply id first, else the most recent earlier signal on the same IST day. Returns signal msg_id -> updates."""
    out: dict[int, list[Update]] = {s.msg_id: [] for s in signals}
    by_id = {s.msg_id: s for s in signals}
    for u in updates:
        ref = u.ref_id if u.ref_id in by_id else None
        if ref is None:
            prev = [s for s in signals if s.t <= u.t and u.t - s.t < 86400]
            ref = prev[-1].msg_id if prev else None
        if ref is not None: out[ref].append(u)
    return out
