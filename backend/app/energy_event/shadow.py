"""Shadow runner for H-EV-NG. One call per Thursday at R+3 min: records exactly one hash-chained row (INACTIVE / NO_TRIGGER / NO_TRADE / SIGNAL) so every activation is visible, including the empty ones.
SIGNAL rows are contest-style signals (participant ZEROHERO_AI_EVENT, MARKET entry, reference ask from the first captured snapshot AFTER the decision, SL/target as % of that reference, EXIT_NOW at R+30).
Deviation from the spec text, disclosed: SL/target are computed from the reference snapshot ask, not from the later (20 s delayed) fill. Imports nothing from the contest package; touches no order path."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import datetime

from datetime import timezone

from ..forward_test import ledger as L
from . import rule as R

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LEDGER = os.path.join(HERE, "data", "energy_event", "activations.jsonl")
PARTICIPANT = "ZEROHERO_AI_EVENT"
UNDERLYING = "NATURALGAS"


def _ro(name: str):
    return sqlite3.connect(f"file:{os.path.join(HERE, 'data', name)}?mode=ro", uri=True, timeout=30)


def future_key() -> str | None:
    c = _ro("market_history.db")
    try:
        r = c.execute("SELECT instrument_key FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' AND source='ANGELONE_CANDLES' ORDER BY bar_start DESC LIMIT 1", (UNDERLYING,)).fetchone()
    finally:
        c.close()
    return r[0] if r else None


def live_bars(key: str, r_epoch: float) -> list[tuple]:
    c = _ro("platform_ticks.db")
    try:
        rows = c.execute("SELECT start_ts,o,h,l,c FROM candles WHERE instrument=? AND tf_sec=60 AND start_ts>=? AND start_ts<=? ORDER BY start_ts", (key, int(r_epoch) - 120, int(r_epoch) + 240)).fetchall()
    finally:
        c.close()
    return [tuple(x) for x in rows]


def read_rows(path: str = LEDGER) -> list[dict]:
    return [json.loads(x) for x in open(path, encoding="utf-8") if x.strip()] if os.path.exists(path) else []


MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def _iso(raw: str) -> str:
    return datetime.strptime(raw.upper(), "%d%b%Y").date().isoformat()


def captured_expiries() -> dict:
    """{iso: {'strikes': [...]}} from the latest captured option polls (own read-only query: the trading side imports nothing from the contest package)."""
    c = _ro("market_history.db")
    try:
        mx = c.execute("SELECT MAX(id) FROM quote_snapshots").fetchone()[0] or 0
        rows = c.execute("SELECT expiry, strike FROM quote_snapshots WHERE id>? AND symbol=? AND kind='OPTION' AND expiry IS NOT NULL GROUP BY expiry, strike", (mx - 30000, UNDERLYING)).fetchall()
    finally:
        c.close()
    out: dict = {}
    for raw, k in rows:
        try: out.setdefault(_iso(raw), {"strikes": set()})["strikes"].add(float(k))
        except Exception: continue
    return {i: {"strikes": sorted(v["strikes"])} for i, v in sorted(out.items())}


def option_snaps(expiry_iso: str, strike: float, ot: str, day: str) -> list[dict]:
    dd = datetime.fromisoformat(expiry_iso); raw = f"{dd.day:02d}{MON[dd.month - 1]}{dd.year}"
    c = _ro("market_history.db")
    try:
        rows = c.execute("SELECT received_ts,bid,ask FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? AND session_date_ist=? AND ltp IS NOT NULL ORDER BY received_ts",
                         (UNDERLYING, raw, strike, ot, day)).fetchall()
    finally:
        c.close()
    return [{"t": datetime.fromisoformat(r[0].replace("Z", "+00:00")).timestamp(), "bid": r[1], "ask": r[2]} for r in rows]


def _row(d, status, **kw) -> dict:
    return {"type": "EVENT_ACTIVATION", "date": d.isoformat(), "status": status, **kw}


def run_day(now_epoch: float | None = None, *, bars_fn=live_bars, key_fn=future_key, chain_fn=None, snaps_fn=None, ledger: str = LEDGER, sleep=time.sleep, calendar_fn=None) -> dict:
    now = now_epoch or time.time(); d = datetime.fromtimestamp(now, tz=R.IST).date()
    if any(r.get("date") == d.isoformat() for r in read_rows(ledger) if r.get("type") == "EVENT_ACTIVATION"): return {"status": "ALREADY_RECORDED"}      # idempotent
    slot, why = R.release_slot(d)
    if slot is None:
        if d.weekday() != 3: return {"status": "NOT_THURSDAY"}                                                                                           # silent: only Thursdays are activation days
        return L.append(ledger, _row(d, "INACTIVE", reason=why))
    r_ep = R.slot_epoch(d)
    if not (r_ep + 175 <= now <= r_ep + 900): return {"status": "OUTSIDE_WINDOW"}                                                                       # decision window R+3 .. R+15 min only
    if calendar_fn is None:
        from .. import market_calendar as mc; calendar_fn = lambda: mc.is_trading("MCX")
    if not calendar_fn(): return L.append(ledger, _row(d, "INACTIVE", reason="MCX not trading"))
    key = key_fn()
    if not key: return L.append(ledger, _row(d, "INACTIVE", reason="no futures instrument key"))
    need = [int(r_ep) + 60 * i for i in range(3)]
    for attempt in range(4):                                                                                        # the 3rd candle is written just after its minute closes
        got = {b[0]: b for b in bars_fn(key, r_ep)}
        if all(t in got for t in need) or now_epoch is not None: break
        sleep(10)
    if not all(t in got for t in need): return L.append(ledger, _row(d, "INACTIVE", reason="feed stale: release-window candles missing", have=len(got)))
    bars = [got[t] for t in need]; trig = R.trigger(bars)
    base = {"release_epoch": r_ep, "slot": why, "future_key": key, "bars": [list(b) for b in bars]}
    if trig is None: return L.append(ledger, _row(d, "NO_TRIGGER", move_pct=round(100 * (bars[2][4] / bars[0][1] - 1), 3), **base))
    ex = chain_fn() if chain_fn else captured_expiries()
    iso = next((e for e in sorted(ex) if e >= d.isoformat()), None)
    if not iso: return L.append(ledger, _row(d, "NO_TRADE", reason="no captured expiry", **base))
    strike = R.nearest_strike(ex[iso]["strikes"], bars[2][4]); ot = trig["dir"]; t_dec = r_ep + 180
    snap = None; fetch = snaps_fn or (lambda: option_snaps(iso, strike, ot, d.isoformat()))
    deadline = time.time() + R.QUOTE_WAIT_SEC if now_epoch is None else 0
    while True:
        snap = next((s for s in fetch() if s["t"] >= t_dec and s.get("ask")), None)
        if snap or time.time() >= deadline: break
        sleep(5)
    info = {**base, "move_pct": round(100 * trig["move"], 3), "dir": ot, "expiry": iso, "strike": strike}
    if not snap: return L.append(ledger, _row(d, "NO_TRADE", reason="no captured option snapshot within 90 s", **info))
    if not R.spread_ok(snap.get("bid"), snap.get("ask")): return L.append(ledger, _row(d, "NO_TRADE", reason="spread > 8% or no bid", bid=snap.get("bid"), ask=snap.get("ask"), **info))
    ref = float(snap["ask"]); pl = R.plan(ref)
    sig = {"id": f"EV-{d.isoformat()}", "participant": PARTICIPANT, "underlying": UNDERLYING, "expiry": iso, "strike": strike, "option_type": ot, "entry_mode": "MARKET", "entry": ref, "entry_hi": ref,
           "sl": pl["sl"], "targets": pl["targets"], "t": t_dec, "events": [{"t": r_ep + 60 * R.HOLD_MIN, "action": "EXIT_NOW"}]}
    return L.append(ledger, _row(d, "SIGNAL", ref_ask=ref, signal=sig, **info))
