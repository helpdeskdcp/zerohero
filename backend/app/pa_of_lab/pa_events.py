"""Price-action chart events for the research lab. Every event is {id,type,t,price,rule,status,confirmed_t,...}; `t` = the bar the event refers to,
`confirmed_t` = the bar CLOSE at which it first became knowable. Nothing is plotted as known before confirmed_t (as_of filter + prefix-invariance test).
Signals come from price_action.signal_at (single source of truth) with the same fill/exit rule as scripts/pa_lab_backtest.py (kept unchanged).
Rules (documented):
  Swing high/low (K=3): bar j is a pivot if its high/low beats K bars on the left (strict) and K on the right (>= / <=); confirmed at bar j+K close.
  HH/LH, HL/LL: pivot vs the previous confirmed pivot of the same kind.
  BOS: close beyond the last confirmed swing IN the direction of the prior trend. CHoCH: close beyond the last confirmed swing AGAINST the prior trend.
       prior trend = price_action.trend() at the previous bar; each pivot can be broken once; unknown (range) prior trend => no label.
  EQH/EQL: two confirmed same-kind pivots within EQ_BPS of each other, >=3 bars apart, level not exceeded in between. Confirmed at the later pivot's confirmation.
  SWEEP: a bar whose wick trades through a confirmed swing / EQ level (known BEFORE the bar) and CLOSES back on the original side.
  REJECTION: price_action.rejection() candle (hammer / shooting star / engulfing) on a closed bar.
  RETEST: within RETEST_BARS after a BOS/CHoCH, a bar that touches the broken level (+-RETEST_BPS) and closes on the break side.
  OPENING RANGE: high/low of the first 15 minutes, valid only after those 15 minutes complete.  VWAP: only if bars carry real traded volume."""
from __future__ import annotations

from datetime import datetime, timezone

from . import price_action as pa
from .schema import IST_SEC

K, EQ_BPS, RETEST_BPS, RETEST_BARS, OR_MIN = 3, 5.0, 5.0, 12, 15
COST_NOTE = "markers show GROSS price outcome only; the validated baseline (scripts/pa_lab_backtest.py) is net -5.99 bps/trade at 6.3 bps cost"


def _mod(t: int) -> int: return ((t + IST_SEC) // 60) % 1440
def _day(t: int) -> str: return datetime.fromtimestamp(t + IST_SEC, tz=timezone.utc).strftime("%Y-%m-%d")


def with_day(bars: list[dict]) -> list[dict]:
    for b in bars: b["day"] = _day(b["t"])
    return bars


def eid(kind: str, sym: str, tf: int, t: int, extra: str = "") -> str: return f"{kind}-{sym}-{tf}-{t}{('-' + extra) if extra else ''}"


def pivots(bars: list[dict]) -> list[dict]:
    out = []
    for j in range(K, len(bars) - K):
        h, l = bars[j]["h"], bars[j]["l"]
        if all(h > bars[j - m]["h"] and h >= bars[j + m]["h"] for m in range(1, K + 1)): out.append(("SH", j, h))
        if all(l < bars[j - m]["l"] and l <= bars[j + m]["l"] for m in range(1, K + 1)): out.append(("SL", j, l))
    res, last = [], {"SH": None, "SL": None}
    for kind, j, p in sorted(out, key=lambda x: (x[1] + K, x[0])):
        prev = last[kind]
        lab = None if prev is None else (("HH" if p > prev else "LH") if kind == "SH" else ("HL" if p > prev else "LL"))
        e = {"id": eid(kind, bars[j].get("symbol", ""), bars[j]["tf_sec"], bars[j]["t"]), "type": kind, "label": lab, "idx": j, "t": bars[j]["t"], "price": p,
             "rule": f"swing K={K}", "status": "CONFIRMED", "confirmed_idx": j + K, "confirmed_t": bars[j + K]["t"] + bars[j + K]["tf_sec"]}
        last[kind] = p; res.append(e)
    return res


def provisional_pivots(bars: list[dict]) -> list[dict]:
    """Candidate pivots among the last K bars that already beat K bars on the left — NOT yet confirmed, drawn hollow."""
    n = len(bars); out = []
    for j in range(max(K, n - K), n):
        for kind, key, cmp_ in (("SH", "h", lambda a, b: a > b), ("SL", "l", lambda a, b: a < b)):
            if all(cmp_(bars[j][key], bars[j - m][key]) for m in range(1, K + 1)) and all(not cmp_(bars[j + m][key], bars[j][key]) for m in range(1, n - j)):
                out.append({"id": eid(kind + "P", bars[j].get("symbol", ""), bars[j]["tf_sec"], bars[j]["t"]), "type": kind, "label": None, "idx": j, "t": bars[j]["t"],
                            "price": bars[j][key], "rule": f"swing K={K} (needs {K - (n - 1 - j)} more bar(s))", "status": "PROVISIONAL", "confirmed_idx": None, "confirmed_t": None})
    return out


def structure(bars: list[dict], pv: list[dict]) -> dict:
    """BOS/CHoCH, EQH/EQL, sweeps, rejections, retests — single causal pass; pivot usable at bar i only if confirmed_idx <= i-? (confirmed at close of confirmed_idx)."""
    sym, tf = (bars[0].get("symbol", ""), bars[0]["tf_sec"]) if bars else ("", 0)
    ev, broken, swept, eq_seen = [], set(), set(), set()
    avail = {"SH": [], "SL": []}
    byconf = {}
    for p in pv: byconf.setdefault(p["confirmed_idx"], []).append(p)
    brk = []
    for i, b in enumerate(bars):
        # pivots confirmed AT bar i close become usable for bar i+1 onward; but structure on bar i itself may use those confirmed at i-1 or earlier
        prior_tr = pa.trend(bars, i - 1, K) if i > 4 * K + 2 else 0
        shs, sls = avail["SH"], avail["SL"]
        # sweeps (before BOS so a wick-and-close-back is not a break)
        for lst, side in ((shs, "SH"), (sls, "SL")):
            for p in lst:
                if p["id"] in swept or p["idx"] >= i: continue
                if side == "SH" and b["h"] > p["price"] and b["c"] <= p["price"]:
                    swept.add(p["id"]); ev.append({"id": eid("SWEEPH", sym, tf, b["t"], p["id"][-10:]), "type": "SWEEP_HIGH", "t": b["t"], "price": p["price"], "level_t": p["t"], "status": "CONFIRMED", "confirmed_t": b["t"] + tf,
                                                    "rule": "wick through confirmed swing high, close back below"})
                if side == "SL" and b["l"] < p["price"] and b["c"] >= p["price"]:
                    swept.add(p["id"]); ev.append({"id": eid("SWEEPL", sym, tf, b["t"], p["id"][-10:]), "type": "SWEEP_LOW", "t": b["t"], "price": p["price"], "level_t": p["t"], "status": "CONFIRMED", "confirmed_t": b["t"] + tf,
                                                    "rule": "wick through confirmed swing low, close back above"})
        if shs and prior_tr != 0 and b["c"] > shs[-1]["price"] and shs[-1]["id"] not in broken:
            p = shs[-1]; broken.add(p["id"]); typ = "BOS_UP" if prior_tr > 0 else "CHOCH_UP"
            e = {"id": eid(typ, sym, tf, b["t"]), "type": typ, "t": b["t"], "price": p["price"], "level_t": p["t"], "status": "CONFIRMED", "confirmed_t": b["t"] + tf,
                 "rule": f"close above last confirmed swing high; prior trend {'up' if prior_tr > 0 else 'down'}"}
            ev.append(e); brk.append((i, e, 1, p["price"]))
        if sls and prior_tr != 0 and b["c"] < sls[-1]["price"] and sls[-1]["id"] not in broken:
            p = sls[-1]; broken.add(p["id"]); typ = "BOS_DN" if prior_tr < 0 else "CHOCH_DN"
            e = {"id": eid(typ, sym, tf, b["t"]), "type": typ, "t": b["t"], "price": p["price"], "level_t": p["t"], "status": "CONFIRMED", "confirmed_t": b["t"] + tf,
                 "rule": f"close below last confirmed swing low; prior trend {'down' if prior_tr < 0 else 'up'}"}
            ev.append(e); brk.append((i, e, -1, p["price"]))
        for i0, e0, sd, lv in brk:                                   # retest
            if e0.get("retested") or not (i0 < i <= i0 + RETEST_BARS): continue
            tol = lv * RETEST_BPS / 1e4
            if sd > 0 and b["l"] <= lv + tol and b["c"] > lv or sd < 0 and b["h"] >= lv - tol and b["c"] < lv:
                e0["retested"] = True
                ev.append({"id": eid("RETEST", sym, tf, b["t"]), "type": "RETEST_UP" if sd > 0 else "RETEST_DN", "t": b["t"], "price": lv, "level_t": e0["t"], "status": "CONFIRMED", "confirmed_t": b["t"] + tf,
                           "rule": f"touch of broken level +-{RETEST_BPS}bps within {RETEST_BARS} bars, close on break side"})
        rj = pa.rejection(b, bars[i - 1] if i else None)
        if rj:
            ev.append({"id": eid("REJ", sym, tf, b["t"]), "type": "REJECTION_BULL" if rj > 0 else "REJECTION_BEAR", "t": b["t"], "price": b["l"] if rj > 0 else b["h"], "status": "CONFIRMED", "confirmed_t": b["t"] + tf,
                       "rule": "hammer/shooting-star or engulfing (price_action.rejection)"})
        for p in byconf.get(i, []): avail[p["type"]].append(p)       # usable from the NEXT bar on
        for kind in ("SH", "SL"):                                     # equal highs / lows
            lst = avail[kind]
            if len(lst) >= 2 and lst[-1]["confirmed_idx"] == i:
                a, c = lst[-2], lst[-1]
                if c["idx"] - a["idx"] >= 3 and abs(c["price"] - a["price"]) / a["price"] * 1e4 <= EQ_BPS:
                    rng = bars[a["idx"] + 1: c["idx"]]
                    if all((x["h"] <= max(a["price"], c["price"])) if kind == "SH" else (x["l"] >= min(a["price"], c["price"])) for x in rng):
                        k2 = (a["id"], c["id"])
                        if k2 not in eq_seen:
                            eq_seen.add(k2)
                            ev.append({"id": eid("EQ" + ("H" if kind == "SH" else "L"), sym, tf, c["t"]), "type": "EQH" if kind == "SH" else "EQL", "t": c["t"], "price": (a["price"] + c["price"]) / 2, "level_t": a["t"],
                                       "status": "CONFIRMED", "confirmed_t": c["confirmed_t"], "rule": f"two confirmed {kind} within {EQ_BPS}bps, >=3 bars apart, not exceeded between"})
    return {"events": ev}


def session_levels(bars: list[dict], open_min: int) -> dict:
    """Prior-day H/L, opening range (valid once 15 min complete), session VWAP (only with real volume)."""
    lv, vwap, orr = [], [], None
    days = sorted({b["day"] for b in bars})
    for d in days:
        db = [b for b in bars if b["day"] == d]
        prev = [b for b in bars if b["day"] < d]
        if prev:
            pd = max(b["day"] for b in prev); pb = [b for b in prev if b["day"] == pd]
            lv.append({"type": "PDH", "day": d, "price": max(b["h"] for b in pb), "rule": "prior session high", "status": "CONFIRMED"})
            lv.append({"type": "PDL", "day": d, "price": min(b["l"] for b in pb), "rule": "prior session low", "status": "CONFIRMED"})
        orb = [b for b in db if open_min <= _mod(b["t"]) < open_min + OR_MIN and _mod(b["t"]) + b["tf_sec"] // 60 <= open_min + OR_MIN]
        need = OR_MIN * 60 // (db[0]["tf_sec"] if db else 300)
        if db and len(orb) == need:
            orr = {"day": d, "high": max(b["h"] for b in orb), "low": min(b["l"] for b in orb), "valid_from_t": orb[-1]["t"] + orb[-1]["tf_sec"],
                   "rule": f"first {OR_MIN} min high/low, valid after completion", "status": "CONFIRMED"}
        if db and all((b.get("volume") or 0) > 0 for b in db):
            cv = cp = 0.0
            for b in db:
                cv += b["volume"]; cp += (b["h"] + b["l"] + b["c"]) / 3 * b["volume"]
                vwap.append({"t": b["t"], "v": cp / cv})
    return {"levels": lv, "opening_range": orr, "vwap": vwap or None,
            "vwap_note": None if vwap else "VWAP unavailable: candle source carries no traded volume (not proxied)"}


def simulate(bars: list[dict], s: pa.Signal) -> dict:
    """Same fill/exit semantics as scripts/pa_lab_backtest.py: entry only if bar i+1 trades through the trigger; stop-first; flat at last bar of the day."""
    i, j = s.i, s.i + 1
    sig = bars[i]
    out = {"filled": False, "entry_t": None, "entry": None, "exit_t": None, "exit": None, "reason": "NOT_FILLED", "r": None}
    if j >= len(bars) or bars[j]["day"] != sig["day"]: return out
    b = bars[j]
    if s.side > 0 and b["h"] >= s.entry: fill = max(b["o"], s.entry)
    elif s.side < 0 and b["l"] <= s.entry: fill = min(b["o"], s.entry)
    else: return out
    risk = abs(s.entry - s.stop); px, why, k = None, "EOD", j
    last = j
    while k < len(bars) and bars[k]["day"] == sig["day"]:
        x = bars[k]; last = k
        hs = x["l"] <= s.stop if s.side > 0 else x["h"] >= s.stop
        ht = x["h"] >= s.target if s.side > 0 else x["l"] <= s.target
        if hs: px, why = s.stop, "STOP"; break
        if ht: px, why = s.target, "TARGET"; break
        k += 1
    if px is None: px = bars[last]["c"]
    out.update(filled=True, entry_t=b["t"], entry=fill, exit_t=bars[last]["t"], exit=px, reason=why, r=(s.side * (px - fill) / risk) if risk else None)
    return out


def signals(bars: list[dict], open_min: int, close_min: int, rr: float = 2.0) -> list[dict]:
    """Closed bars only. Gate matches the backtest for NSE (09:45-14:30); other sessions: open+30 .. close-60."""
    lo, hi = open_min + 30, close_min - 60
    res, free = [], 0
    for i in range(len(bars)):
        b = bars[i]
        if not b.get("closed", True) or i < free or not (lo <= _mod(b["t"]) <= hi): continue
        s = pa.signal_at(bars, i, rr=rr)
        if not s: continue
        sim = simulate(bars, s)
        risk = abs(s.entry - s.stop)
        res.append({"id": eid("PA", b.get("symbol", ""), b["tf_sec"], b["t"], "L" if s.side > 0 else "S"), "source": "price_action", "t": b["t"], "idx": i, "side": s.side,
                    "entry": s.entry, "stop": s.stop, "t1": s.entry + s.side * risk, "t2": s.target, "rule": s.reason, "status": "CONFIRMED", "confirmed_t": b["t"] + b["tf_sec"], "sim": sim})
        if sim["filled"]:
            free = max(i + 1, next((k for k in range(i + 1, len(bars)) if bars[k]["t"] > sim["exit_t"]), len(bars)))
    return res


def regime(bars: list[dict]) -> dict:
    if len(bars) < 4 * K + 3: return {"label": "UNKNOWN", "no_trade": True, "reasons": ["TOO_FEW_BARS"]}
    tr = pa.trend(bars, len(bars) - 1, K)
    label = {1: "TREND_UP", -1: "TREND_DOWN", 0: "RANGE"}[tr]
    return {"label": label, "no_trade": tr == 0, "reasons": ["RANGE: no HH/HL or LH/LL structure"] if tr == 0 else []}
