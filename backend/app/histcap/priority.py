"""Priority quote capture: while a contract is on the watch list (a signal was registered for it) its full quote is fetched every PRIORITY_POLL_SEC (default 3 s) in ONE batched request together with all other watched
contracts, and written to the same append-only `quote_snapshots` table the normal capture uses (same schema, same integrity checks). Everything that settles trades from captured quotes therefore gets ~3 s resolution
instead of the normal 1-3 minutes, for exactly the contracts that matter, and only until that exchange's close. Read-only broker calls through the shared market SDK (no second login, no websocket, no orders).
Kill switch: PRIORITY_CAPTURE=0. Watch list: data/priority_watch.json."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import normalize as N

HERE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WATCH = os.path.join(HERE, "data", "priority_watch.json")
IST = timezone(timedelta(hours=5, minutes=30))
MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MCX_SYMS = {"NATURALGAS", "CRUDEOIL", "CRUDEOILM", "NATGASMINI", "GOLDM", "GOLD", "SILVER", "SILVERM", "COPPER", "ZINC"}
CLOSE = {"NSE": (15, 35), "MCX": (23, 35)}
MAX_TOKENS = 40
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("PRIORITY_CAPTURE", "1").strip().lower() not in ("0", "false", "off", "no")


def _load(path: str | None = None) -> list[dict]:
    try: return json.load(open(path or WATCH, encoding="utf-8"))
    except Exception: return []


def request_watch(symbol: str, expiry_iso: str, strike: float, option_type: str, *, now: float | None = None, path: str | None = None) -> dict:
    """Register a contract; it stays on the list until its exchange closes today. Idempotent. Never raises into the caller's flow if the file cannot be written (returns the entry anyway)."""
    path = path or WATCH; t = now or time.time(); d = date.fromisoformat(expiry_iso); raw = f"{d.day:02d}{MON[d.month - 1]}{d.year}"
    h, m = CLOSE["MCX" if symbol.upper() in MCX_SYMS else "NSE"]; day = datetime.fromtimestamp(t, tz=IST)
    until = datetime(day.year, day.month, day.day, h, m, tzinfo=IST).timestamp()
    e = {"symbol": symbol.upper(), "expiry": raw, "strike": float(strike), "option_type": option_type.upper(), "until": until, "added": t}
    try:
        with _lock:
            items = [x for x in _load(path) if x.get("until", 0) > t and not (x["symbol"], x["expiry"], x["strike"], x["option_type"]) == (e["symbol"], e["expiry"], e["strike"], e["option_type"])]
            items.append(e); os.makedirs(os.path.dirname(path), exist_ok=True); tmp = path + ".tmp"; json.dump(items, open(tmp, "w")); os.replace(tmp, path)
    except Exception:
        pass
    return e


class PriorityCapture:
    def __init__(self, store=None, sdk_provider=None, meta_fn=None, interval: float | None = None, path: str | None = None):
        self.store = store; self._sdk_provider = sdk_provider; self._meta_fn = meta_fn or self._meta_from_db
        self.interval = float(interval if interval is not None else os.environ.get("PRIORITY_POLL_SEC", "3")); self.path = path or WATCH
        self._meta_cache: dict = {}; self._run_id = None; self._run_day = None; self.last_error = None; self.written = 0; self.cycles = 0; self.last_cycle = None

    def _store(self):
        if self.store is None:
            from .store import hist_store
            self.store = hist_store()
        return self.store

    def _sdk(self):
        if self._sdk_provider: return self._sdk_provider()
        from .worker import CaptureWorker
        return CaptureWorker._default_sdk()

    def _meta_from_db(self, e: dict) -> dict | None:
        st = self._store(); day = datetime.now(IST).strftime("%Y-%m-%d")
        with st._conn() as c:
            r = c.execute("SELECT exchange, token, instrument_key FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND option_type=? LIMIT 1",
                          (day, e["symbol"], e["expiry"], e["strike"], e["option_type"])).fetchone()
        return {"instrument_key": r[2], "symbol": e["symbol"], "kind": "OPTION", "exchange": r[0], "token": str(r[1]), "expiry": e["expiry"], "strike": e["strike"], "option_type": e["option_type"]} if r else None

    def _meta(self, e: dict) -> dict | None:
        k = (e["symbol"], e["expiry"], e["strike"], e["option_type"])
        if k not in self._meta_cache: self._meta_cache[k] = self._meta_fn(e)                  # a contract the normal capture has never seen cannot be resolved: it is skipped (and retried next cycle if None)
        if self._meta_cache[k] is None: self._meta_cache.pop(k, None); return None
        return self._meta_cache[k]

    def step(self, now: float | None = None) -> int:
        t = now or time.time(); active = [e for e in _load(self.path) if e.get("until", 0) > t]
        if not active: return 0
        metas = [m for m in (self._meta(e) for e in active[:MAX_TOKENS]) if m]
        if not metas: return 0
        sdk = self._sdk()
        if sdk is None: return 0
        by_ex: dict[str, list] = {}
        for m in metas: by_ex.setdefault(m["exchange"], []).append(m["token"])
        quotes = sdk.get_quotes_batch(by_ex, mode="FULL"); recv = N.now_utc_iso(); rows = []
        for m in metas:
            q = quotes.get(m["token"]) if isinstance(quotes, dict) else None
            if not isinstance(q, dict): continue
            nq = N.norm_quote(q, m, recv)
            if nq.get("ltp") is not None: rows.append(nq)
        if not rows: return 0
        st = self._store(); day = datetime.now(IST).strftime("%Y-%m-%d")
        if self._run_id is None or self._run_day != day: self._run_id = st.start_run("PRIORITY_WATCH", {}, True); self._run_day = day
        with st.transaction() as conn: n = st.write_quotes(conn, rows, self._run_id, {"issues": [], "rejected": [], "flagged": 0})
        self.written += n; self.cycles += 1; self.last_cycle = t; return n

    def run_forever(self) -> None:                                                              # pragma: no cover (thread)
        while True:
            try:
                self.step(); self.last_error = None; time.sleep(self.interval)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:120]}"; time.sleep(max(self.interval * 5, 15))      # back off on any broker error (rate limit etc.)

    def status(self) -> dict:
        return {"enabled": enabled(), "watching": len([e for e in _load(self.path) if e.get("until", 0) > time.time()]), "cycles": self.cycles, "rows_written": self.written, "last_error": self.last_error, "interval_sec": self.interval}


_instance: PriorityCapture | None = None


def start_background() -> PriorityCapture | None:
    global _instance
    if not enabled() or _instance is not None: return _instance
    _instance = PriorityCapture(); threading.Thread(target=_instance.run_forever, name="priority-capture", daemon=True).start(); return _instance
