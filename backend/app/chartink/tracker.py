"""Forward price tracker: fills the marks of the (alert, symbol) pairs with Angel One LTP in batches, read-only. Runs as a daemon thread inside the app process (one broker session)."""
from __future__ import annotations

import logging
import threading
import time

from . import store

_log = logging.getLogger("chartink.tracker")
LATE_REF_SEC = 120            # reference price must be taken within this many seconds of the alert
LATE_MARK_SEC = 180           # a later mark taken more than this after its due time is flagged LATE (kept, but excluded from validation)
MAX_AGE_SEC = 900             # give up on a mark this long after it was due
NIFTY = ("NSE", "99926000")
_tokens: dict = {}


def token_map() -> dict:
    global _tokens
    if _tokens: return _tokens
    from .. import instruments
    _tokens = {r["name"].upper(): r["token"] for r in instruments.master_rows() if r.get("exch_seg") == "NSE" and str(r.get("symbol", "")).endswith("-EQ")}
    return _tokens


def angel_ltp(tokens: list[str]) -> dict:
    """token -> ltp for NSE tokens (plus NIFTY), one REST call of <= 50 tokens, read-only."""
    from ..connectors import angelone as A
    status, jwt, err = A._get_jwt()
    if status != "OK": raise RuntimeError(f"angel auth {status}: {err}")
    c = A._creds(); headers = dict(A._HEADERS_BASE); headers["X-PrivateKey"] = c["api_key"]; headers["Authorization"] = f"Bearer {jwt}"
    resp = A._http("POST", A.QUOTE_URL, json={"mode": "LTP", "exchangeTokens": {"NSE": list(tokens)}}, headers=headers)
    body = resp.json() if resp.content else {}
    rows = ((body.get("data") or {}).get("fetched")) if isinstance(body, dict) else None
    if not isinstance(rows, list): raise RuntimeError("angel quote: no data")
    return {str(r.get("symbolToken")): float(r["ltp"]) for r in rows if r.get("ltp") is not None}


def tick(now: float | None = None, quote_fn=None, path: str | None = None, token_fn=None) -> dict:
    """One pass: attach tokens to new pairs, then fill every due mark with the latest LTP. Returns counters."""
    now = now or time.time(); quote_fn = quote_fn or angel_ltp; tmap = (token_fn or token_map)(); out = {"due": 0, "filled": 0, "late": 0, "missed": 0, "no_token": 0}
    with store.conn(path) as c:
        for r in c.execute("SELECT id, symbol FROM pairs WHERE token IS NULL AND status='PENDING'").fetchall():
            t = tmap.get(r["symbol"])
            if t: c.execute("UPDATE pairs SET token=? WHERE id=?", (t, r["id"]))
            else: c.execute("UPDATE pairs SET status='NO_TOKEN' WHERE id=?", (r["id"],)); c.execute("UPDATE marks SET status='NO_TOKEN' WHERE pair_id=? AND status='PENDING'", (r["id"],)); out["no_token"] += 1
        due = c.execute("SELECT m.pair_id, m.horizon, m.due_ts, p.token FROM marks m JOIN pairs p ON p.id=m.pair_id WHERE m.status='PENDING' AND m.due_ts<=? AND p.token IS NOT NULL ORDER BY m.due_ts", (now,)).fetchall()
    out["due"] = len(due)
    if not due: return out
    toks = sorted({r["token"] for r in due}); px: dict = {}
    for i in range(0, len(toks), 49):
        try: px.update(quote_fn(toks[i:i + 49] + ([NIFTY[1]] if i == 0 else [])))
        except Exception as e: _log.warning("quote batch failed: %r", e)
        if i + 49 < len(toks): time.sleep(1.1)
    nifty = px.get(NIFTY[1])
    with store.conn(path) as c:
        for r in due:
            p = px.get(r["token"]); late = now - r["due_ts"]
            lim = LATE_REF_SEC if r["horizon"] == "0" else LATE_MARK_SEC
            if p is None:
                if late > MAX_AGE_SEC: c.execute("UPDATE marks SET status='MISSED' WHERE pair_id=? AND horizon=?", (r["pair_id"], r["horizon"])); out["missed"] += 1
                continue
            st = "OK" if late <= lim else "LATE"
            c.execute("UPDATE marks SET ts=?, price=?, nifty=?, status=? WHERE pair_id=? AND horizon=?", (now, p, nifty, st, r["pair_id"], r["horizon"]))
            out["filled" if st == "OK" else "late"] += 1
    return out


class Runner:
    def __init__(self, period: float = 15.0): self.period, self._t, self._stop = period, None, threading.Event()

    def _loop(self):
        while not self._stop.is_set():
            try:
                from .. import market_calendar as mc
                if mc.is_trading("NSE"): tick()
            except Exception as e: _log.warning("tracker tick failed: %r", e)
            self._stop.wait(self.period)

    def start(self):
        if self._t is None: self._t = threading.Thread(target=self._loop, name="chartink-tracker", daemon=True); self._t.start()
