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
HEARTBEAT_SEC = 15                                # an unchanged quote is stored again every 15 s (light row) so a settlement can see that it was still standing
MAX_TOKENS = 50                                  # one batched broker request carries up to 50 tokens
_lock = threading.Lock()


def enabled() -> bool:
    return os.environ.get("PRIORITY_CAPTURE", "1").strip().lower() not in ("0", "false", "off", "no")


def _load(path: str | None = None) -> list[dict]:
    try: return json.load(open(path or WATCH, encoding="utf-8"))
    except Exception: return []


def request_watch(symbol: str, expiry_iso: str, strike: float, option_type: str, *, now: float | None = None, path: str | None = None, ttl_sec: float | None = None) -> dict:
    """Register a contract. Without ttl_sec (a signal's contract) it stays until its exchange closes today; with ttl_sec (a screen that is showing the contract) only that long, refreshed whenever it is viewed again.
    Idempotent; an existing longer registration is never shortened. Never raises into the caller's flow if the file cannot be written (returns the entry anyway)."""
    path = path or WATCH; t = now or time.time(); d = date.fromisoformat(expiry_iso); raw = f"{d.day:02d}{MON[d.month - 1]}{d.year}"
    h, m = CLOSE["MCX" if symbol.upper() in MCX_SYMS else "NSE"]; day = datetime.fromtimestamp(t, tz=IST)
    close = datetime(day.year, day.month, day.day, h, m, tzinfo=IST).timestamp()
    until = close if ttl_sec is None else min(close, t + float(ttl_sec))
    e = {"symbol": symbol.upper(), "expiry": raw, "strike": float(strike), "option_type": option_type.upper(), "until": until, "added": t, "kind": "signal" if ttl_sec is None else "view"}
    key = lambda x: (x["symbol"], x["expiry"], x["strike"], x["option_type"])
    try:
        with _lock:
            items = [x for x in _load(path) if x.get("until", 0) > t]; old = next((x for x in items if key(x) == key(e)), None)
            if old is not None and old["until"] >= until - 30 and (old.get("kind") == "signal" or e["kind"] == "view"): return old        # already covered: no file write (many viewers, one entry)
            if old is not None: e["until"] = max(until, old["until"]); e["kind"] = "signal" if "signal" in (old.get("kind"), e["kind"]) else "view"
            items = [x for x in items if key(x) != key(e)] + [e]; os.makedirs(os.path.dirname(path), exist_ok=True); tmp = path + ".tmp"; json.dump(items, open(tmp, "w")); os.replace(tmp, path)
    except Exception:
        pass
    return e


class PriorityCapture:
    def __init__(self, store=None, sdk_provider=None, meta_fn=None, interval: float | None = None, path: str | None = None):
        self.store = store; self._sdk_provider = sdk_provider; self._meta_fn = meta_fn or self._meta_from_db
        self.interval = float(interval if interval is not None else os.environ.get("PRIORITY_POLL_SEC", "3")); self.path = path or WATCH
        self._meta_cache: dict = {}; self._run_id = None; self._run_day = None; self.last_error = None; self.last_note = None; self._auth_ts = 0.0; self.written = 0; self.cycles = 0; self._seen: dict = {}; self.last_cycle = None

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
        """Token / exchange of the contract from its LATEST captured row (any day: a contract's token does not change), via the (symbol, expiry, strike, option_type, exch_ts) index."""
        st = self._store()
        with st._conn() as c:
            r = c.execute("SELECT exchange, token, instrument_key FROM quote_snapshots WHERE symbol=? AND expiry=? AND strike=? AND option_type=? AND kind='OPTION' AND source='ANGELONE_QUOTE_FULL' ORDER BY exch_ts DESC LIMIT 1",
                          (e["symbol"], e["expiry"], e["strike"], e["option_type"])).fetchone()
        return {"instrument_key": r[2], "symbol": e["symbol"], "kind": "OPTION", "exchange": r[0], "token": str(r[1]), "expiry": e["expiry"], "strike": e["strike"], "option_type": e["option_type"]} if r else None

    def _meta(self, e: dict) -> dict | None:
        k = (e["symbol"], e["expiry"], e["strike"], e["option_type"])
        if k not in self._meta_cache: self._meta_cache[k] = self._meta_fn(e)                  # a contract the normal capture has never seen cannot be resolved: it is skipped (and retried next cycle if None)
        if self._meta_cache[k] is None: self._meta_cache.pop(k, None); return None
        return self._meta_cache[k]

    def step(self, now: float | None = None) -> int:
        t = now or time.time(); active = [e for e in _load(self.path) if e.get("until", 0) > t]
        if not active: self.last_note = "idle: empty watch list"; return 0
        active.sort(key=lambda e: (e.get("kind") != "signal", -e.get("added", 0)))                        # signal contracts first, then the most recently viewed
        metas = [m for m in (self._meta(e) for e in active[:MAX_TOKENS]) if m]
        if not metas: self.last_note = "no watched contract resolvable from the captured chain yet"; return 0
        sdk = self._sdk()
        if sdk is None: self.last_note = "no market SDK"; return 0
        if t - self._auth_ts > 60:                                                              # same session check the normal capture does, at most once a minute (cached login, no new session)
            self._auth_ts = t
            try: ok = bool(sdk.authenticate())
            except Exception: ok = False
            if not ok: self.last_note = "broker session not authenticated"; return 0
        by_ex: dict[str, list] = {}
        for m in metas: by_ex.setdefault(m["exchange"], []).append(m["token"])
        quotes = sdk.get_quotes_batch(by_ex, mode="FULL"); recv = N.now_utc_iso(); rows = []
        for m in metas:
            q = quotes.get(m["token"]) if isinstance(quotes, dict) else None
            if not isinstance(q, dict): continue
            nq = N.norm_quote(q, m, recv)
            if nq.get("ltp") is not None: rows.append(nq)
        keep = []
        for nq in rows:                                                                              # the table's dedup key drops a row whose exchange timestamp did not change: store a light heartbeat every HEARTBEAT_SEC instead
            k = nq["instrument_key"]; last = self._seen.get(k); ex = nq.get("exch_ts")
            if last and last[0] == ex:
                if t - last[1] < HEARTBEAT_SEC: continue
                nq = {**nq, "snap_key": "HB|" + str(nq["received_ts"])[:19], "depth_json": None}
            keep.append(nq); self._seen[k] = (ex, t)
        rows = keep
        if not rows: self.last_note = f"{len(metas)} contract(s) polled, no new quote to store"; self.last_cycle = t; return 0
        st = self._store(); day = datetime.now(IST).strftime("%Y-%m-%d")
        if self._run_id is None or self._run_day != day: self._run_id = st.start_run("PRIORITY_WATCH", {}, True); self._run_day = day
        with st.transaction() as conn: n = st.write_quotes(conn, rows, self._run_id, {"issues": [], "rejected": [], "flagged": 0})
        self.written += n; self.cycles += 1; self.last_cycle = t; self.last_note = f"ok: {n} row(s) from {len(metas)} contract(s)"; return n

    def run_forever(self) -> None:                                                              # pragma: no cover (thread)
        last_log = 0.0
        while True:
            try:
                t0 = time.time(); self.step(); dt = time.time() - t0
                if dt > 10 and t0 - last_log > 60: print(f"[priority-capture] slow step {dt:.1f}s ({self.last_note})", flush=True); last_log = t0           # a step is normally well under a second: anything slow is visible in the journal
                self.last_error = None; time.sleep(self.interval)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {str(e)[:120]}"
                if time.time() - last_log > 60: print(f"[priority-capture] error {self.last_error}", flush=True); last_log = time.time()
                time.sleep(max(self.interval * 5, 15))      # back off on any broker error (rate limit etc.)

    def status(self) -> dict:
        return {"enabled": enabled(), "watching": len([e for e in _load(self.path) if e.get("until", 0) > time.time()]), "cycles": self.cycles, "rows_written": self.written, "last_cycle_age_s": (round(time.time() - self.last_cycle, 1) if self.last_cycle else None), "last_error": self.last_error, "last_note": self.last_note, "interval_sec": self.interval}


_instance: PriorityCapture | None = None


def start_background() -> PriorityCapture | None:
    global _instance
    if not enabled() or _instance is not None: return _instance
    _instance = PriorityCapture(); threading.Thread(target=_instance.run_forever, name="priority-capture", daemon=True).start(); return _instance
