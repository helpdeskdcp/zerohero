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
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from .. import db, instruments, market_calendar
from . import normalize as N
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
            "NATURALGAS": "MCX", "CRUDEOIL": "MCX", "CRUDEOILM": "MCX", "GOLD": "MCX", "SILVER": "MCX"}


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _cfg() -> dict:
    syms = [s.strip().upper() for s in _env("CHANAKYA_HIST_SYMBOLS", "NIFTY,NATURALGAS,CRUDEOIL").split(",") if s.strip()]
    try:
        win = max(1, min(40, int(_env("CHANAKYA_HIST_CHAIN_WINDOW", "15"))))
    except ValueError:
        win = 15
    tfs = [t.strip() for t in _env("CHANAKYA_HIST_TFS", "1m,5m,15m").split(",") if t.strip() in _INTERVAL]
    return {
        "enabled": _env("CHANAKYA_HIST_ENABLED", "1") not in ("0", "false", "no"),
        "symbols": syms, "chain_window": win, "tfs": tfs or ["1m", "5m", "15m"],
        "quote_sec": float(_env("CHANAKYA_HIST_QUOTE_SEC", "20")),
        "candle_sec": float(_env("CHANAKYA_HIST_CANDLE_SEC", "90")),
        "heartbeat_sec": float(_env("CHANAKYA_HIST_HEARTBEAT_SEC", "300")),
        "option_candles": _env("CHANAKYA_HIST_OPTION_CANDLES", "0") in ("1", "true", "yes"),
    }


class CaptureWorker:
    def __init__(self, sdk_provider=None, store: HistStore | None = None):
        # sdk_provider() -> a broker.angelone.AngelOneClient (or None). Default:
        # the shared read-only market SDK (same JWT as the rest of the app; NO
        # second login, NO websocket).
        self._sdk_provider = sdk_provider or self._default_sdk
        self.store = store or hist_store()
        self.cfg = _cfg()
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._ref_cache: dict = {}      # sym -> (expires_epoch, refs)
        self.last_run: dict | None = None
        self.last_error: str | None = None
        self.started_at: str | None = None
        self._owner = f"{os.uname().nodename}:{os.getpid()}"
        self.is_leader = False

    @staticmethod
    def _default_sdk():
        try:
            from ..connectors.angelone import _market_sdk
            return _market_sdk(require_auth=False)
        except Exception:
            return None

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
        while not self._stop.is_set():
            try:
                self.is_leader = bool(await asyncio.to_thread(
                    db.lease_acquire, _LEASE_KEY, self._owner, _LEASE_TTL_SEC))
                if not self.is_leader:
                    await self._sleep(15)
                    continue
                state = market_calendar.status_all()
                open_now = any(v == "OPEN" for v in (state.get("segments") or {}).values())
                if open_now:
                    do_candles = (time.time() - last_candle) >= self.cfg["candle_sec"]
                    await asyncio.to_thread(self.run_once, "POLL", do_candles)
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

    def _auth_ok(self) -> bool:
        sdk = self._sdk_provider()
        try:
            return bool(sdk and sdk.authenticate())
        except Exception:
            return False

    def run_once(self, mode: str = "POLL_ONCE", do_candles: bool = True) -> dict:
        state = market_calendar.status_all().get("segments", {})
        sdk = self._sdk_provider()
        auth_ok = self._auth_ok()
        rid = self.store.start_run(mode, state, auth_ok)
        counts = {"candles": 0, "quotes": 0, "greeks": 0, "raw": 0}
        integ: dict = {"issues": [], "rejected": [], "flagged": 0}
        errors: list = []
        n_instr = 0

        if not (sdk and auth_ok):
            self.store.finish_run(rid, counts=counts, integrity=integ,
                                  errors=[{"stage": "auth", "status": "AUTH_UNAVAILABLE",
                                           "message": str((getattr(sdk, "last_auth", {}) or {}))}],
                                  notes="ANGEL_* credentials unavailable -> no market rows written")
            self.last_run = {"run_id": rid, "auth_ok": False, **counts}
            return self.last_run

        recv = N.now_utc_iso()
        for sym in self.cfg["symbols"]:
            try:
                refs = self._refs(sdk, sym)
                if not refs:
                    errors.append({"symbol": sym, "stage": "resolve", "status": "NO_REFS"})
                    continue
                n_instr += len(refs["tokens_meta"])
                with self.store.transaction() as conn:
                    self._capture_quotes(conn, sdk, sym, refs, recv, rid, counts, integ, errors)
                    self._capture_greeks(conn, sdk, sym, refs, recv, rid, counts, integ, errors)
                    if do_candles:
                        self._capture_candles(conn, sdk, sym, refs, recv, rid, counts, integ, errors)
            except Exception as e:
                errors.append({"symbol": sym, "stage": "cycle", "status": type(e).__name__,
                               "message": str(e)[:200]})
                traceback.print_exc()

        self.store.finish_run(rid, instruments_n=n_instr, counts=counts, integrity=integ,
                              errors=errors, notes=f"symbols={','.join(self.cfg['symbols'])}")
        self.last_run = {"run_id": rid, "auth_ok": True, "instruments": n_instr, **counts,
                         "flagged": integ.get("flagged", 0), "rejected": len(integ.get("rejected", [])),
                         "errors": len(errors)}

        # derive Greek exposure from what this cycle just captured (read-only,
        # append-only, own tables). A failure here never affects the capture.
        if counts.get("greeks", 0) > 0 and "NIFTY" in self.cfg["symbols"]:
            try:
                from ..greeks_engine.engine import greeks_engine
                greeks_engine().run_once("NIFTY", mode="CYCLE")
            except Exception as e:
                self.last_error = f"greeks_engine: {type(e).__name__}: {e}"
        return self.last_run

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
                    w = self.cfg["chain_window"]
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

        raw_id = self.store.put_raw(conn, endpoint="market/v1/quote",
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
        counts["quotes"] += self.store.write_quotes(conn, rows, rid, integ)

    def _capture_greeks(self, conn, sdk, sym, refs, recv, rid, counts, integ, errors):
        expiry = refs.get("opt_expiry")
        if not expiry:
            return
        try:
            g = sdk.get_option_greeks(sym.upper(), expiry)
        except Exception as e:
            errors.append({"symbol": sym, "stage": "greeks", "status": type(e).__name__, "message": str(e)[:160]})
            return
        raw_id = self.store.put_raw(conn, endpoint="marketData/v1/optionGreek",
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
        counts["greeks"] += self.store.write_greeks(conn, rows, rid, integ)

    def _capture_candles(self, conn, sdk, sym, refs, recv, rid, counts, integ, errors):
        metas = [m for m in (refs.get("spot_meta"), refs.get("fut_meta")) if m]
        if self.cfg["option_candles"]:
            metas += refs.get("opt_metas", [])
        for m in metas:
            for tf in self.cfg["tfs"]:
                # session-aware window: recent bars during hours, last close off-hours
                frm, to = instruments.lookback_window(tf, bars=int(self.cfg.get("candle_bars", 20)))
                try:
                    d = sdk.get_candles(m["exchange"], m["token"], _INTERVAL[tf], frm, to)
                except Exception as e:
                    errors.append({"symbol": sym, "stage": f"candles:{tf}", "status": type(e).__name__,
                                   "message": str(e)[:140]})
                    continue
                raw_id = self.store.put_raw(conn, endpoint="historical/v1/getCandleData",
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
                counts["candles"] += self.store.write_candles(conn, rows, rid, integ)

    # ---------------------------------------------------------------- status
    def status(self) -> dict:
        return {
            "enabled": self.cfg["enabled"], "running": bool(self._task and not self._task.done()),
            "is_leader": self.is_leader, "lease_owner": db.lease_owner(_LEASE_KEY),
            "started_at": self.started_at, "last_error": self.last_error,
            "config": {k: self.cfg[k] for k in ("symbols", "chain_window", "tfs",
                                                "quote_sec", "candle_sec", "option_candles")},
            "last_run": self.last_run, "store": self.store.summary(),
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
