"""
CaptureWorker — a standalone REST poller that records complete timestamped
market data into `market_history.db` for backtesting.

Never subscribes a WebSocket, never imports trading/signal logic, never writes
the trading DB. One capture failure for one symbol is logged and skipped; it
never aborts the cycle or affects anything else.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from .. import db, instruments, market_calendar
from ..optionchain.sources import dhan_chain as _dhan
from ..optionchain.sources.dhan_chain import SUPPORTED as _DHAN_SUPPORTED
from . import normalize as N
from .dhan_capture import capture_dhan_chain
from .store import HistStore, hist_store

_IST = timezone(timedelta(hours=5, minutes=30))

# Cross-process singleton lease. With `uvicorn --workers N` every worker builds a
# CaptureWorker; only the lease holder polls the broker (the rest idle-loop and
# take over within _LEASE_TTL_SEC if the holder dies). Coordination metadata only
# in app_settings — no trading data is written here.
_LEASE_KEY = "histcap_lease"
_LEASE_TTL_SEC = 45          # ~2 missed 20s poll cycles before a stale lease is stealable
_INTERVAL = {"1m": "ONE_MINUTE", "3m": "THREE_MINUTE", "5m": "FIVE_MINUTE",
             "15m": "FIFTEEN_MINUTE", "30m": "THIRTY_MINUTE", "1h": "ONE_HOUR", "1d": "ONE_DAY"}
_EXCH_OF = {"NIFTY": "NSE", "BANKNIFTY": "NSE", "FINNIFTY": "NSE",
            "SENSEX": "BSE", "BANKEX": "BSE",          # options on BFO, spot on BSE
            "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "CRUDEOILM": "MCX", "GOLDM": "MCX", "GOLD": "MCX", "SILVER": "MCX"}


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# strikes each side of ATM to capture. Indices that can gap a long way between
# sessions need a wider band so the next day's ATM band still overlaps the
# prior one (keeps the derived change-in-OI populated near the money).
_WIDE_CHAIN = {"FINNIFTY": 25, "BANKEX": 25}


def _win_env(name: str, default: int) -> int:
    try:
        return max(1, min(40, int(_env(name, str(default)))))
    except ValueError:
        return default


def _cfg() -> dict:
    syms = [s.strip().upper() for s in _env("CHANAKYA_HIST_SYMBOLS", "NIFTY,NATURALGAS,CRUDEOIL").split(",") if s.strip()]
    win = _win_env("CHANAKYA_HIST_CHAIN_WINDOW", 15)
    # per-symbol override: CHANAKYA_HIST_CHAIN_WINDOW_<SYM>, else the _WIDE_CHAIN
    # default, else the global window.
    win_by_sym: dict[str, int] = {}
    for s in syms:
        if os.environ.get(f"CHANAKYA_HIST_CHAIN_WINDOW_{s}") not in (None, ""):
            win_by_sym[s] = _win_env(f"CHANAKYA_HIST_CHAIN_WINDOW_{s}", win)
        elif s in _WIDE_CHAIN:
            win_by_sym[s] = _WIDE_CHAIN[s]
    # PHASE 6 — capture every broker-supported intraday interval. 2m is NOT an
    # AngelOne getCandleData interval; if a 2m series is needed it must be
    # resampled from 1m at read time and tagged source=DERIVED (never captured).
    tfs = [t.strip() for t in _env("CHANAKYA_HIST_TFS", "1m,3m,5m,15m,30m,1h").split(",")
           if t.strip() in _INTERVAL]
    return {
        "enabled": _env("CHANAKYA_HIST_ENABLED", "1") not in ("0", "false", "no"),
        "symbols": syms, "chain_window": win, "chain_window_by_sym": win_by_sym,
        "tfs": tfs or ["1m", "5m", "15m"],
        "quote_sec": float(_env("CHANAKYA_HIST_QUOTE_SEC", "20")),
        "candle_sec": float(_env("CHANAKYA_HIST_CANDLE_SEC", "90")),
        "candle_delay_sec": float(_env("CHANAKYA_HIST_CANDLE_DELAY_SEC", "1.0")),
        "heartbeat_sec": float(_env("CHANAKYA_HIST_HEARTBEAT_SEC", "300")),
        "option_candles": _env("CHANAKYA_HIST_OPTION_CANDLES", "0") in ("1", "true", "yes"),
        # MCP 3.3 priority #5 -- parallel Dhan capture, PARALLEL to (never
        # instead of) the AngelOne capture above. On by default now that
        # Dhan is verified live and PRIMARY for on-demand reads
        # (app.optionchain.resolve) -- a same-day kill switch, matching
        # DHAN_ORDERFLOW_ENABLED's own convention, in case the parallel
        # load or an OAuth lapse needs it off quickly without a code change.
        "dhan_capture": _env("CHANAKYA_HIST_DHAN_ENABLED", "1") in ("1", "true", "yes"),
        # MCP 3.3 continuation -- raw_responses archive retention (item 12).
        # Bounded so the verbatim-payload table doesn't grow unbounded; the
        # parsed research tables (market_candles/quote_snapshots/
        # option_greeks) are never touched by this and keep accumulating.
        "raw_retention_days": int(_env("CHANAKYA_HIST_RAW_RETENTION_DAYS", "14")),
    }



class OiBaseline:
    """Previous-session closing open interest per contract, looked up LAZILY and only for the contracts actually being written.

    The old code materialised the whole baseline with one aggregation over every earlier session of the symbol (GROUP BY ... MAX(received_ts) joined back on received_ts, which has no index): on a cold
    cache that single statement made the first capture cycle of 2026-10-28 take 737 s (quotes stage 549 s, measured by the new per-stage timings) and it re-ran every 30 minutes per symbol.
    Now each contract costs ONE descending read on the (symbol, expiry, strike, option_type, exch_ts) index: the newest row of an earlier session with oi > 0. Same value (a contract's OI does not move after the
    close, so the newest-by-exchange-time row equals the newest-by-receive-time row), memoised, and read OUTSIDE the write lock via prefetch(). get() never touches the database.
    Key: (expiry or "", strike, option_type); FUTURE rows have strike / option_type None."""

    def __init__(self, open_conn, sym: str, today: str | None = None):
        """today = the IST calendar date of the session being written (default: now). The old code asked the table for MAX(session_date_ist) of the symbol, which the planner answers with a scan of every row of the
        symbol plus a sort, and which could still say 'yesterday' at the first cycle of the day (then the baseline came from two sessions back)."""
        from datetime import datetime, timedelta, timezone
        self._open = open_conn; self.sym = sym; self._memo: dict = {}; self._lock = threading.Lock()
        self.today = today or datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")

    def prefetch(self, keys) -> None:
        with self._lock:
            todo = [k for k in dict.fromkeys(keys) if k not in self._memo]
            if not todo: return
            conn = self._open()
            try:
                bound = self._day_start_utc()                                  # exchange stamps before today 00:00 IST: the index range then starts right below today's rows (a priority-watched contract has thousands of them today)
                for e, st, ot in todo:
                    a = (self.sym, e or None, st, ot, self.today)
                    r = conn.execute("SELECT oi FROM quote_snapshots WHERE symbol=? AND expiry IS ? AND strike IS ? AND option_type IS ? AND kind IN ('OPTION','FUTURE') AND session_date_ist<? "
                                     "AND exch_ts<? AND oi IS NOT NULL AND oi>0 ORDER BY exch_ts DESC, id DESC LIMIT 1", a + (bound,)).fetchone()
                    if r is None:                                                # rows without an exchange stamp sort first in the index: look only when nothing stamped was found
                        r = conn.execute("SELECT oi FROM quote_snapshots WHERE symbol=? AND expiry IS ? AND strike IS ? AND option_type IS ? AND kind IN ('OPTION','FUTURE') AND session_date_ist<? "
                                         "AND exch_ts IS NULL AND oi IS NOT NULL AND oi>0 ORDER BY id DESC LIMIT 1", a).fetchone()
                    self._memo[(e, st, ot)] = r[0] if r else None
            finally:
                conn.close()

    def set_today(self, day: str | None) -> None:
        """The session date of the rows being written (from their exchange timestamps): on a weekend or holiday the quotes still belong to the last trading day, and the baseline must be the session BEFORE that one."""
        with self._lock:
            if day and day != self.today: self.today = day; self._memo.clear()

    def _day_start_utc(self) -> str:
        """'YYYY-MM-DDTHH:MM' of today 00:00 IST in UTC (= previous calendar day 18:30 UTC); exch_ts strings start with the UTC date-time, so a string comparison is a time comparison."""
        from datetime import date, timedelta
        d = date.fromisoformat(self.today) - timedelta(days=1)
        return f"{d.isoformat()}T18:30"

    def get(self, key, default=None):
        v = self._memo.get(key); return default if v is None else v

    def __bool__(self) -> bool:
        return any(v is not None for v in self._memo.values())

    def __len__(self) -> int:
        return sum(1 for v in self._memo.values() if v is not None)


class CaptureWorker:
    def __init__(self, sdk_provider=None, store: HistStore | None = None, dhan_auth_checker=None):
        # sdk_provider() -> a broker.angelone.AngelOneClient (or None). Default:
        # the shared read-only market SDK (same JWT as the rest of the app; NO
        # second login, NO websocket).
        self._sdk_provider = sdk_provider or self._default_sdk
        # dhan_auth_checker() -> bool, whether Dhan has a currently-stored
        # server-side session. Same injectable-dependency pattern as
        # sdk_provider above, specifically so Dhan's capture pass (see
        # _capture_dhan_all()) is independently testable without touching
        # the real app.db-backed token store, and so it never depends on
        # AngelOne's own sdk/auth_ok at all.
        self._dhan_auth_checker = dhan_auth_checker or self._default_dhan_auth_check
        self.store = store or hist_store()
        self.cfg = _cfg()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ref_cache: dict = {}      # sym -> (expires_epoch, refs)
        self._oi_base_cache: dict = {}  # sym -> (expires_epoch, {(expiry,strike,ot): prev-session oi})
        self._dhan_expiry_cache: dict = {}  # sym -> (expires_epoch, nearest ISO expiry) -- Dhan's own, independent of AngelOne refs
        self.last_run: dict | None = None
        self.last_error: str | None = None
        self.started_at: str | None = None
        self._owner = f"{os.uname().nodename}:{os.getpid()}"
        self.is_leader = False
        # greeks_engine CYCLE pass: runs OFF the capture thread, single-flight, rate-limited (see _kick_greeks_engine)
        self._ge_lock = threading.Lock()
        self._cd_lock = threading.Lock(); self._cd_running = False; self._cd_skipped = 0; self.last_candle_run = None
        self._ge_running = False
        self._ge_last_start = 0.0
        self._ge_skipped = 0
        self._ge_last: dict | None = None

    @staticmethod
    def _default_sdk():
        try:
            from ..connectors.angelone import _market_sdk
            return _market_sdk(require_auth=False)
        except Exception:
            return None

    @staticmethod
    def _default_dhan_auth_check() -> bool:
        """Whether Dhan has a currently-stored server-side session -- reads
        the same app.db-backed token store dhan_chain._headers() itself
        reads for the real API call. Deliberately independent of
        AngelOne's own sdk/auth_ok (see _capture_dhan_all())."""
        try:
            from .. import dhan_client
            return bool(dhan_client.get_access_token())
        except Exception:
            return False

    def _win_for(self, sym: str) -> int:
        """strikes each side of ATM for this symbol (per-symbol override -> global)."""
        return self.cfg.get("chain_window_by_sym", {}).get(sym, self.cfg["chain_window"])

    # ---------------------------------------------------------------- lifecycle
    def start(self):
        if not self.cfg["enabled"]:
            self.last_error = "disabled (CHANAKYA_HIST_ENABLED=0)"
            return
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self.started_at = N.now_utc_iso()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=6)
            except Exception:
                pass
        try:
            db.lease_release(_LEASE_KEY, self._owner)
        except Exception:
            pass
        self.is_leader = False

    async def _loop(self):
        last_candle = 0.0
        last_hb = 0.0
        last_rotate = 0.0
        while not self._stop.is_set():
            try:
                self.is_leader = bool(await asyncio.to_thread(
                    db.lease_acquire, _LEASE_KEY, self._owner, _LEASE_TTL_SEC))
                if not self.is_leader:
                    await self._sleep(15)
                    continue
                if (time.time() - last_rotate) >= 86400:
                    await asyncio.to_thread(self._rotate_raw)
                    last_rotate = time.time()
                state = market_calendar.status_all()
                open_now = any(v == "OPEN" for v in (state.get("segments") or {}).values())
                if open_now:
                    do_candles = (time.time() - last_candle) >= self.cfg["candle_sec"]
                    if _env("CHANAKYA_HIST_CANDLES_SEPARATE", "1") in ("0", "false", "no", "off"):
                        await asyncio.to_thread(self.run_once, "POLL", do_candles)                 # rollback: the old inline candles inside the quote cycle
                    else:
                        await asyncio.to_thread(self.run_once, "POLL", False)                      # quotes + greeks + dhan only: the cycle never waits for the rate-limited candle calls
                        if do_candles: self._kick_candles()
                    if do_candles:
                        last_candle = time.time()
                    await self._sleep(self.cfg["quote_sec"])
                else:
                    if (time.time() - last_hb) >= self.cfg["heartbeat_sec"]:
                        await asyncio.to_thread(self._heartbeat, state)
                        last_hb = time.time()
                    await self._sleep(15)
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
                await self._sleep(20)

    async def _sleep(self, secs):
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=max(1.0, secs))
        except asyncio.TimeoutError:
            pass

    # ---------------------------------------------------------------- one cycle
    def _heartbeat(self, state):
        rid = self.store.start_run("HEARTBEAT", state.get("segments", {}), auth_ok=self._auth_ok())
        self.store.finish_run(rid, notes="markets closed")

    def _rotate_raw(self):
        """Daily raw_responses archive rotation (MCP 3.3 continuation item
        12) -- runs regardless of market/auth state, independent of the
        open/closed heartbeat cadence above. Bounded to the auxiliary
        verbatim-payload table only; see HistStore.rotate_raw_responses()."""
        try:
            n = self.store.rotate_raw_responses(days_to_keep=self.cfg.get("raw_retention_days", 14))
            if n:
                print(f"[histcap] rotated {n} raw_responses rows past the retention window")
        except Exception as e:
            self.last_error = f"rotate_raw: {type(e).__name__}: {e}"

    def _auth_ok(self) -> bool:
        sdk = self._sdk_provider()
        try:
            return bool(sdk and sdk.authenticate())
        except Exception:
            return False

    def run_once(self, mode: str = "POLL_ONCE", do_candles: bool = True) -> dict:
        # MCP 3.3 continuation -- Dhan capture must NOT require AngelOne to
        # be logged in (previously _capture_dhan() lived inside the
        # AngelOne-gated loop below and used refs["opt_expiry"], so it never
        # ran at all on an AngelOne auth failure). AngelOne's own auth check
        # now only gates the AngelOne capture path; _capture_dhan_all() runs
        # unconditionally afterward, gated solely by Dhan's own session via
        # self._dhan_auth_checker(). AngelOne remains the unchanged fallback
        # -- its capture logic below is untouched.
        t_cycle = time.time(); stage: dict = {}                                    # per-stage seconds of this cycle (quotes / greeks / candles / dhan), stored in capture_runs.notes
        def timed(name, fn, *a):
            t = time.time()
            try: return fn(*a)
            finally: stage[name] = round(stage.get(name, 0.0) + time.time() - t, 2)
        state = market_calendar.status_all().get("segments", {})
        sdk = self._sdk_provider()
        auth_ok = self._auth_ok()
        rid = self.store.start_run(mode, state, auth_ok)
        counts = {"candles": 0, "quotes": 0, "greeks": 0, "raw": 0}
        integ: dict = {"issues": [], "rejected": [], "flagged": 0}
        errors: list = []
        n_instr = 0
        nifty_expiry = None

        if not (sdk and auth_ok):
            errors.append({"stage": "auth", "status": "AUTH_UNAVAILABLE",
                           "message": str(getattr(sdk, "last_auth", {}) or {})})
        else:
            recv = N.now_utc_iso()
            for sym in self.cfg["symbols"]:
                try:
                    refs = self._refs(sdk, sym)
                    if not refs:
                        errors.append({"symbol": sym, "stage": "resolve", "status": "NO_REFS"})
                        continue
                    n_instr += len(refs["tokens_meta"])
                    if sym == "NIFTY": nifty_expiry = refs.get("opt_expiry")
                    # NO transaction around the whole symbol: each phase fetches from the broker FIRST (no DB lock held)
                    # and writes in its own short transaction, so the shared store lock is never held across a broker call
                    # (it used to be held for minutes and starved the 3-second priority capture of live trades).
                    timed("quotes", self._capture_quotes, None, sdk, sym, refs, recv, rid, counts, integ, errors)
                    timed("greeks", self._capture_greeks, None, sdk, sym, refs, recv, rid, counts, integ, errors)
                    if do_candles:
                        timed("candles", self._capture_candles, None, sdk, sym, refs, recv, rid, counts, integ, errors)
                except Exception as e:
                    errors.append({"symbol": sym, "stage": "cycle", "status": type(e).__name__,
                                   "message": str(e)[:200]})
                    traceback.print_exc()

        timed("dhan", self._capture_dhan_all, N.now_utc_iso(), rid, counts, integ, errors)

        notes = (f"symbols={','.join(self.cfg['symbols'])}" if (sdk and auth_ok) else
                 "ANGEL_* credentials unavailable -> no AngelOne market rows written")
        cycle_s = round(time.time() - t_cycle, 2); slow_sec = float(_env("CHANAKYA_HIST_SLOW_CYCLE_SEC", "120"))
        notes += " | " + json.dumps({"cycle_s": cycle_s, "stage_s": stage, "greeks_unavailable": counts.get("greeks_unavailable", 0)}, sort_keys=True)     # visible per run: a silent greeks skip or a slow stage
        self.store.finish_run(rid, instruments_n=n_instr, counts=counts, integrity=integ,
                              errors=errors, notes=notes)
        if cycle_s > slow_sec:                                                                       # alert: one log line per slow cycle, with the stage that ate the time
            self.slow_cycles = getattr(self, "slow_cycles", 0) + 1
            print(f"[histcap] SLOW cycle {cycle_s:.0f}s (> {slow_sec:.0f}s) stages={stage}", flush=True)
        self.last_run = {"run_id": rid, "auth_ok": bool(sdk and auth_ok), "instruments": n_instr, **counts,
                         "flagged": integ.get("flagged", 0), "rejected": len(integ.get("rejected", [])),
                         "errors": len(errors), "cycle_s": cycle_s, "stage_s": stage, "slow": cycle_s > slow_sec}

        # derive Greek exposure from what this cycle just captured (read-only,
        # append-only, own tables). A failure here never affects the capture.
        if counts.get("greeks", 0) > 0 and "NIFTY" in self.cfg["symbols"] and nifty_expiry:
            self._kick_greeks_engine(nifty_expiry)
        return self.last_run

    # ---------------------------------------------------------------- candles (off the quote cycle)
    def _kick_candles(self) -> bool:
        """Start ONE background candle pass (single-flight). The historical-candle endpoint is rate limited, so every call is spaced by candle_delay_sec: for ~100 calls that is 100-250 s. Inline, that wait sat inside
        the per-symbol loop and delayed the QUOTES of every later symbol (measured 2026-10-28: candles stage up to 251 s of a 393 s cycle). A pass still running when the next is due is skipped, not stacked."""
        with self._cd_lock:
            if self._cd_running: self._cd_skipped += 1; return False
            self._cd_running = True
        threading.Thread(target=self._run_candles, daemon=True, name="histcap-candles").start()
        return True

    def _run_candles(self) -> None:
        t0 = time.time(); counts = {"candles": 0, "quotes": 0, "greeks": 0, "raw": 0}; integ: dict = {"issues": [], "rejected": [], "flagged": 0}; errors: list = []; rid = None
        try:
            sdk = self._sdk_provider()
            if not (sdk and self._auth_ok()): return
            rid = self.store.start_run("CANDLES", market_calendar.status_all().get("segments", {}), True); recv = N.now_utc_iso(); n_sym = 0
            for sym in self.cfg["symbols"]:
                try:
                    refs = self._refs(sdk, sym)
                    if refs: self._capture_candles(None, sdk, sym, refs, recv, rid, counts, integ, errors); n_sym += 1
                except Exception as e:
                    errors.append({"symbol": sym, "stage": "candles", "status": type(e).__name__, "message": str(e)[:160]})
            dt = round(time.time() - t0, 1)
            self.store.finish_run(rid, instruments_n=n_sym, counts=counts, integrity=integ, errors=errors, notes="candles pass | " + json.dumps({"cycle_s": dt, "symbols": n_sym}))
            self.last_candle_run = {"run_id": rid, "sec": dt, "candles": counts["candles"], "errors": len(errors)}
        except Exception as e:
            self.last_error = f"candles: {type(e).__name__}: {e}"
            if rid is not None:
                try: self.store.finish_run(rid, instruments_n=0, counts=counts, integrity=integ, errors=[{"stage": "candles", "status": type(e).__name__}], notes="candles pass failed")
                except Exception: pass
        finally:
            with self._cd_lock: self._cd_running = False

    # ---------------------------------------------------------------- greeks engine (off the capture thread)
    def _kick_greeks_engine(self, expiry: str) -> bool:
        """Start ONE background greeks_engine pass for today's session and the expiry this cycle just captured. Never blocks the capture loop, never runs two at once,
        and at most one per GREEKS_ENGINE_MIN_SEC. The old inline call scanned every expiry in 3.2 M rows (100-1500 s per cycle) and collapsed the capture cadence."""
        now = time.time(); min_sec = float(_env("CHANAKYA_HIST_GREEKS_ENGINE_MIN_SEC", "60"))
        with self._ge_lock:
            if self._ge_running or (now - self._ge_last_start) < min_sec:
                self._ge_skipped += 1
                return False
            self._ge_running = True; self._ge_last_start = now
        threading.Thread(target=self._run_greeks_engine, args=(expiry,), daemon=True, name="histcap-greeks-engine").start()
        return True

    def _run_greeks_engine(self, expiry: str) -> None:
        t0 = time.time()
        try:
            from ..greeks_engine.engine import greeks_engine
            day = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
            res = greeks_engine().run_once("NIFTY", expiries=[expiry], mode="CYCLE", session_date_ist=day,
                                           deadline_sec=float(_env("CHANAKYA_HIST_GREEKS_ENGINE_TIMEOUT_SEC", "120")))
            self._ge_last = {"expiry": expiry, "sec": round(time.time() - t0, 2), "snapshots": res.get("snapshots_written"), "errors": len(res.get("errors") or [])}
        except Exception as e:
            self.last_error = f"greeks_engine: {type(e).__name__}: {e}"
            self._ge_last = {"expiry": expiry, "sec": round(time.time() - t0, 2), "error": f"{type(e).__name__}"}
        finally:
            with self._ge_lock:
                self._ge_running = False

    # ---------------------------------------------------------------- resolution
    def _refs(self, sdk, sym: str) -> dict | None:
        hit = self._ref_cache.get(sym)
        if hit and time.time() < hit[0]:
            return hit[1]
        exch = _EXCH_OF.get(sym, "NSE")
        tokens_meta: list[dict] = []
        spot_meta = fut_meta = None
        opt_expiry = None
        opt_metas: list[dict] = []

        if exch == "MCX":
            fut = sdk.resolve_future_contract(sym, "AUTO")
            if fut.get("status") == "OK":
                fut_meta = {"instrument_key": f'MCX:{fut["token"]}', "symbol": sym, "kind": "FUTURE",
                            "exchange": "MCX", "token": str(fut["token"]), "expiry": fut.get("expiry")}
                tokens_meta.append(fut_meta)
                # Also record the NEXT-month future: options expiring after the front future (NG 20-Nov options sit on the 24-Nov future) need it as their ATM anchor, and on the front future's expiry day it is the only live anchor left.
                try:
                    from datetime import datetime as _dt
                    _d = lambda e: _dt.strptime(str(e).upper(), "%d%b%Y")
                    exps = sorted({r.get("expiry") for r in sdk.search_instruments(symbol=sym, exchange="MCX")
                                   if r.get("instrumenttype") == "FUTCOM" and r.get("expiry")}, key=_d)
                    nxt = next((e for e in exps if _d(e) > _d(fut.get("expiry"))), None)
                    f2 = sdk.resolve_future_contract(sym, nxt) if nxt else {}
                    if f2.get("status") == "OK":
                        tokens_meta.append({"instrument_key": f'MCX:{f2["token"]}', "symbol": sym, "kind": "FUTURE",
                                            "exchange": "MCX", "token": str(f2["token"]), "expiry": f2.get("expiry")})
                except Exception:
                    pass
        else:
            r = instruments.resolve(sym) or {}
            if r.get("symboltoken"):
                spot_meta = {"instrument_key": f'{r.get("exchange","NSE")}:{r["symboltoken"]}',
                             "symbol": sym, "kind": "INDEX", "exchange": r.get("exchange", "NSE"),
                             "token": str(r["symboltoken"]), "expiry": None}
                tokens_meta.append(spot_meta)
            fut = instruments.resolve_index_future(sym, "AUTO")
            if fut.get("status") == "OK" and fut.get("symboltoken"):
                fut_meta = {"instrument_key": f'NFO:{fut["symboltoken"]}', "symbol": sym, "kind": "FUTURE",
                            "exchange": "NFO", "token": str(fut["symboltoken"]), "expiry": fut.get("expiry")}
                tokens_meta.append(fut_meta)

        try:
            oc = sdk.resolve_option_contract(sym, "AUTO", "ATM", "CE")
            if oc.get("status") == "OK":
                opt_expiry = oc.get("expiry")
                uni = sdk._option_universe(sym)
                rows = [r for r in sdk.search_instruments(symbol=sym, exchange=uni["exchange"])
                        if r.get("expiry") == opt_expiry and r.get("instrumenttype") in uni["types"]]
                strikes = sorted({round(float(r.get("strike")) / 100, 6)
                                  for r in rows if r.get("strike") is not None})
                spot = uni.get("spot")
                if strikes and spot is not None:
                    ai = min(range(len(strikes)), key=lambda i: abs(strikes[i] - float(spot)))
                    w = self._win_for(sym)
                    for k in strikes[max(0, ai - w): ai + w + 1]:
                        for typ in ("CE", "PE"):
                            c = next((r for r in rows if str(r.get("symbol", "")).upper().endswith(typ)
                                      and abs(float(r.get("strike")) / 100 - k) < 1e-6), None)
                            if c:
                                m = {"instrument_key": f'{uni["quote_ex"]}:{c["token"]}', "symbol": sym,
                                     "kind": "OPTION", "exchange": uni["quote_ex"], "token": str(c["token"]),
                                     "expiry": opt_expiry, "strike": k, "option_type": typ}
                                opt_metas.append(m)
                                tokens_meta.append(m)
        except Exception:
            pass

        if not tokens_meta:
            return None
        refs = {"exch": exch, "spot_meta": spot_meta, "fut_meta": fut_meta,
                "opt_expiry": opt_expiry, "opt_metas": opt_metas, "tokens_meta": tokens_meta}
        self._ref_cache[sym] = (time.time() + 3600, refs)
        return refs

    def _oi_baseline_for(self, conn, sym: str):
        """Previous-session closing OI per contract ('Chng in OI' = current_oi - this baseline, materialised at write time because AngelOne's quote feed sends no change-in-OI). A lazy OiBaseline,
        kept for the session day; the caller prefetch()es the contracts it is about to write (outside the write lock)."""
        hit = self._oi_base_cache.get(sym)
        if hit and time.time() < hit[0]:
            return hit[1]
        ob = OiBaseline(self.store._conn, sym)
        self._oi_base_cache[sym] = (time.time() + 86400, ob)          # the previous session's close does not move intraday; the object resets itself when the session date changes (set_today)
        return ob

    def _wtxn(self, conn):
        """A write transaction: reuse `conn` if the caller already holds one, else open a SHORT one (rows only; never across a broker call)."""
        return nullcontext(conn) if conn is not None else self.store.transaction()

    # ---------------------------------------------------------------- captures
    def _capture_quotes(self, conn, sdk, sym, refs, recv, rid, counts, integ, errors):
        by_ex: dict[str, list] = {}
        for m in refs["tokens_meta"]:
            by_ex.setdefault(m["exchange"], []).append(m["token"])
        try:
            quotes = sdk.get_quotes_batch(by_ex, mode="FULL")
        except Exception as e:
            errors.append({"symbol": sym, "stage": "quotes", "status": type(e).__name__, "message": str(e)[:160]})
            return

        base = self._oi_baseline_for(None, sym)          # lazy: one indexed read per contract, done HERE (outside the write lock) for exactly the contracts about to be written
        try:
            sess = [N.norm_quote(q, m, recv).get("session_date_ist") for m in refs["tokens_meta"] for q in [quotes.get(m["token"])] if isinstance(q, dict) and m.get("kind") in ("OPTION", "FUTURE")]
            base.set_today(max((x for x in sess if x), default=None))
        except Exception: pass
        try: base.prefetch([(m.get("expiry") or "", m.get("strike"), m.get("option_type")) for m in refs["tokens_meta"] if m.get("kind") in ("OPTION", "FUTURE")])
        except Exception as e: errors.append({"symbol": sym, "stage": "oi_baseline", "status": type(e).__name__, "message": str(e)[:120]})          # never blocks the capture: oi_change just stays empty
        with self._wtxn(conn) as c:
            raw_id = self.store.put_raw(c, endpoint="market/v1/quote",
                                        request={"exchangeTokens": {k: v for k, v in by_ex.items()}, "mode": "FULL"},
                                        http_status=None, status="OK" if quotes else "DATA_UNAVAILABLE",
                                        payload=quotes, server_ts=None, run_id=rid)
            counts["raw"] += 1
            spot_ltp = None
            sm = refs.get("spot_meta")
            if sm:
                sq = quotes.get(sm["token"]) or {}
                spot_ltp = N._f(sq.get("ltp") or sq.get("lastTradedPrice"))

            rows = []
            for m in refs["tokens_meta"]:
                q = quotes.get(m["token"])
                if not isinstance(q, dict):
                    q = {"status": "DATA_UNAVAILABLE"}
                nq = N.norm_quote(q, m, recv, spot_ltp=spot_ltp)
                nq["raw_id"] = raw_id
                rows.append(nq)

            # materialise change-in-OI (broker sends none): current_oi - prev-session
            # close OI per contract. Tagged doi_derived; never overwrites a real value.
            if base:
                for nq in rows:
                    oi = nq.get("oi")
                    if (nq.get("kind") in ("OPTION", "FUTURE") and nq.get("oi_change") is None
                            and isinstance(oi, (int, float)) and oi > 0):
                        b = base.get((nq.get("expiry") or "", nq.get("strike"), nq.get("option_type")))
                        if b is not None:
                            nq["oi_change"] = round(oi - b, 0)
                            nq["oi_change_src"] = "derived"

            counts["quotes"] += self.store.write_quotes(c, rows, rid, integ)

    def _capture_greeks(self, conn, sdk, sym, refs, recv, rid, counts, integ, errors):
        expiry = refs.get("opt_expiry")
        if not expiry:
            return
        try:
            g = sdk.get_option_greeks(sym.upper(), expiry)
        except Exception as e:
            errors.append({"symbol": sym, "stage": "greeks", "status": type(e).__name__, "message": str(e)[:160]})
            return
        # PHASE 1 — capability short-circuit: the client returned from cache
        # without a network call because the broker has no greeks for this
        # instrument. Record it once as capability info, not a per-cycle error,
        # and do NOT write a raw row (there is no broker response to store).
        if g.get("cache") == "CAPABILITY" or g.get("capability") == "UNAVAILABLE":
            counts["greeks_unavailable"] = counts.get("greeks_unavailable", 0) + 1
            return
        with self._wtxn(conn) as c:
            raw_id = self.store.put_raw(c, endpoint="marketData/v1/optionGreek",
                                        request={"name": sym.upper(), "expirydate": expiry},
                                        http_status=g.get("http_status"), status=g.get("status"),
                                        payload=g, server_ts=g.get("fetched_at"), run_id=rid)
            counts["raw"] += 1
            if g.get("status") != "OK":
                errors.append({"symbol": sym, "stage": "greeks", "status": g.get("status"),
                               "message": g.get("errorcode") or g.get("message")})
                return
            rows = N.norm_greeks(g.get("rows") or [], sym, expiry, "OK", recv,
                                 server_ts=g.get("fetched_at"))
            for r in rows:
                r["raw_id"] = raw_id
            counts["greeks"] += self.store.write_greeks(c, rows, rid, integ)

    def _capture_candles(self, conn, sdk, sym, refs, recv, rid, counts, integ, errors):
        metas = [m for m in (refs.get("spot_meta"), refs.get("fut_meta")) if m]
        if self.cfg["option_candles"]:
            metas += refs.get("opt_metas", [])
        # AngelOne's historical-candle endpoint rate-limits (HTTP 403
        # "Access denied because of exceeding access rate") when called
        # back-to-back with no delay. With len(symbols)*len(tfs) calls per
        # cycle (8*6=48 by default) fired with zero spacing, this endpoint
        # alone was enough to exhaust the account's real rate limit -
        # observed in production, affecting this worker AND every other
        # consumer of the same AngelOne login concurrently. Runs off the
        # asyncio event loop (via asyncio.to_thread in _run), so a plain
        # blocking sleep here is safe and doesn't stall anything else.
        candle_delay_sec = self.cfg.get("candle_delay_sec", 1.0)
        first_call = True
        for m in metas:
            for tf in self.cfg["tfs"]:
                if not first_call and candle_delay_sec > 0:
                    time.sleep(candle_delay_sec)
                first_call = False
                # session-aware window: recent bars during hours, last close off-hours
                frm, to = instruments.lookback_window(tf, bars=int(self.cfg.get("candle_bars", 20)))
                try:
                    d = sdk.get_candles(m["exchange"], m["token"], _INTERVAL[tf], frm, to)
                except Exception as e:
                    errors.append({"symbol": sym, "stage": f"candles:{tf}", "status": type(e).__name__,
                                   "message": str(e)[:140]})
                    continue
                with self._wtxn(conn) as c:
                    raw_id = self.store.put_raw(c, endpoint="historical/v1/getCandleData",
                                                request={"exchange": m["exchange"], "symboltoken": m["token"],
                                                         "interval": _INTERVAL[tf], "fromdate": frm, "todate": to},
                                                http_status=None, status=d.get("status"),
                                                payload=d, server_ts=None, run_id=rid)
                    counts["raw"] += 1
                    if d.get("status") != "OK":
                        continue
                    rows = N.norm_candles(d.get("candles") or [], m, tf, recv)
                    for r in rows:
                        r["raw_id"] = raw_id
                    counts["candles"] += self.store.write_candles(c, rows, rid, integ)

    def _dhan_nearest_expiry(self, sym: str) -> str | None:
        """Dhan's own nearest live expiry (dhan_chain.expiry_list), fully
        independent of AngelOne's refs["opt_expiry"] -- this is the crux of
        MCP 3.3 continuation's fix: Dhan capture must resolve its own
        expiry, not borrow AngelOne's. Returned in this codebase's canonical
        'DDMMMYYYY' form (dhan_chain's own _to_canonical_expiry) -- NOT
        Dhan's raw ISO 'YYYY-MM-DD' -- so quote_snapshots/option_greeks'
        `expiry` column stays one consistent format regardless of source,
        exactly like AngelOne's refs["opt_expiry"] already is. Cached ~1h
        (an options expiry doesn't change intraday). None if Dhan has
        nothing (expired session, network failure, unsupported symbol)."""
        hit = self._dhan_expiry_cache.get(sym)
        if hit and time.time() < hit[0]:
            return hit[1]
        dates = _dhan.expiry_list(sym)
        if not dates:
            return None
        today = N.ist_date(N.now_utc_iso())
        future = sorted(d for d in dates if d >= today)
        iso = future[0] if future else sorted(dates)[-1]
        chosen = _dhan._to_canonical_expiry(iso)
        self._dhan_expiry_cache[sym] = (time.time() + 3600, chosen)
        return chosen

    def _capture_dhan_all(self, recv, rid, counts, integ, errors):
        """Independent Dhan chain capture (MCP 3.3 continuation) -- runs
        alongside, never instead of, the AngelOne capture in run_once()
        above, and regardless of AngelOne's own sdk/auth_ok: gated solely by
        Dhan's own session (self._dhan_auth_checker()) and its own expiry
        resolution (_dhan_nearest_expiry(), never refs["opt_expiry"]). Any
        Dhan failure (expired OAuth session, network error, unsupported
        symbol, malformed response) is a soft per-symbol skip, exactly like
        every AngelOne capture step already treats its own failures --
        never blocks, slows, or affects anything else in this cycle."""
        if not self.cfg.get("dhan_capture", True):
            return
        try:
            dhan_ok = bool(self._dhan_auth_checker())
        except Exception:
            dhan_ok = False
        if not dhan_ok:
            # sanitized: never the token/session value itself, only its absence.
            errors.append({"stage": "dhan_auth", "status": "AUTH_UNAVAILABLE",
                           "message": "no valid Dhan session"})
            return

        for sym in self.cfg["symbols"]:
            su = sym.upper()
            if su not in _DHAN_SUPPORTED:
                counts["dhan_unsupported"] = counts.get("dhan_unsupported", 0) + 1
                continue
            try:
                expiry = self._dhan_nearest_expiry(su)
                if not expiry:
                    errors.append({"symbol": sym, "stage": "dhan_expiry", "status": "NO_EXPIRY"})
                    continue
                out = capture_dhan_chain(su, expiry, received_ts=recv)
            except Exception as e:
                errors.append({"symbol": sym, "stage": "dhan_chain", "status": type(e).__name__,
                               "message": str(e)[:160]})
                continue
            if out is None:
                counts["dhan_unavailable"] = counts.get("dhan_unavailable", 0) + 1
                continue
            try:
                with self.store.transaction() as conn:
                    # archive the exact raw response (MCP 3.3 continuation
                    # item 10) -- request params only (no auth headers, see
                    # dhan_chain.fetch_raw()'s own docstring for why the
                    # response body structurally can't carry a credential).
                    raw_id = self.store.put_raw(
                        conn, endpoint="v2/optionchain",
                        request={"Underlying": su, "UnderlyingSeg": "MCX_COMM" if su in _dhan._MCX_SYMBOLS else "IDX_I",
                                 "Expiry": expiry},
                        http_status=None, status="OK", payload=out["raw"],
                        server_ts=out.get("exch_ts"), run_id=rid)
                    counts["raw"] += 1
                    for r in out["quote_rows"]:
                        r["raw_id"] = raw_id
                    for r in out["greek_rows"]:
                        r["raw_id"] = raw_id
                    counts["dhan_quotes"] = counts.get("dhan_quotes", 0) + \
                        self.store.write_quotes(conn, out["quote_rows"], rid, integ)
                    counts["dhan_greeks"] = counts.get("dhan_greeks", 0) + \
                        self.store.write_greeks(conn, out["greek_rows"], rid, integ)
            except Exception as e:
                errors.append({"symbol": sym, "stage": "dhan_write", "status": type(e).__name__,
                               "message": str(e)[:160]})

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "enabled": self.cfg["enabled"], "running": bool(self._task and not self._task.done()),
            "is_leader": self.is_leader, "lease_owner": db.lease_owner(_LEASE_KEY),
            "started_at": self.started_at, "last_error": self.last_error,
            "config": {k: self.cfg[k] for k in ("symbols", "chain_window", "chain_window_by_sym",
                                                "tfs", "quote_sec", "candle_sec", "candle_delay_sec",
                                                "option_candles", "dhan_capture", "raw_retention_days")},
            "last_run": self.last_run, "store": self.store.summary(),
            "greeks_engine": {"running": self._ge_running, "skipped": self._ge_skipped, "last": self._ge_last},
            "candles": {"running": self._cd_running, "skipped": self._cd_skipped, "last": self.last_candle_run},
        }


# --------------------------------------------------------------- CLI: one cycle
if __name__ == "__main__":
    once = "--once" in sys.argv or "--run-once" in sys.argv
    w = CaptureWorker()
    if once:
        import pprint
        pprint.pprint(w.run_once("POLL_ONCE", do_candles=True))
    else:
        async def _main():
            w.start()
            print("capture worker running; Ctrl-C to stop")
            try:
                while True:
                    await asyncio.sleep(3600)
            except KeyboardInterrupt:
                await w.stop()
        asyncio.run(_main())
