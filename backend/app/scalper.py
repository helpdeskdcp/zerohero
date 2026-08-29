"""
ScalpRunner — the automation layer that actually makes scalping possible.

A scalp cannot be run from a button: it needs continuous re-evaluation and
a hard exit clock. This module owns an async loop that, ONLY WHEN ARMED:

  * every poll_sec, evaluates each watchlist symbol through run_scalp_pipeline
  * opens SCALP paper trades on gate approval (paper only — no broker call)
  * marks every open SCALP trade to market each tick and lets paper_trading
    apply target / stop / trailing / TIME exits
  * enforces behavioural guards that protect expectancy:
      - session window (skip auction chop + the close)
      - max concurrent scalps
      - daily trade cap
      - loss cooldown (stand down for N seconds after a losing scalp)

Disarmed by default. State + config persist in app_settings so an
arm survives a process restart only if you re-arm — arming is never sticky.
"""
from __future__ import annotations
import os
import time
import json
import socket
import asyncio
import traceback
from collections import deque
from datetime import datetime, timezone, timedelta

# ---- single-active-runner lease (survives multi-worker uvicorn) ----
LEASE_KEY = "runner_lease"
LEASE_TTL_SEC = 30          # a leader that misses this many seconds of heartbeat is replaced
ARMED_KEY = "runner_armed"  # arm/disarm intent, shared across workers
PUB_KEY = "runner_pub"      # leader publishes its live status here for standby workers to serve
LATCH_KEY = "runner_latches"  # persisted alert latches — a restart must not re-spam Telegram

from . import db
from . import instruments
from . import combos
from .scalp_pipeline import run_scalp_pipeline
from .connectors import angelone, telegram
from .connectors.angel_ws import AngelMarketFeed, EXCHANGE_TYPE
from .engines.paper_trading import update_trade_price, open_trade, close_trade
from .engines.signal_engine import run_signal_engine
from .connectors.telegram import notify_position_alert, _send as _tg_send
from .engines.scalp_engine import _parse_hhmm

CONFIG_KEY = "scalp_config"

DEFAULT_CONFIG = {
    "poll_sec": 5,
    "max_concurrent": 2,
    "daily_cap": 20,
    "loss_cooldown_sec": 120,
    "auto_arm": False,          # arm the runner automatically on process start
    "fast_mode": False,         # manage/exit loop at 1s; entry candles cached ≤60s
    "candle_refresh_sec": 60,   # min seconds between REST candle fetches per symbol
    "broker_sync": True,        # auto-pull Angel One net positions into the monitor
    "broker_sync_sec": 30,      # how often to poll getPosition
    "broker_flat_confirm": 3,   # consecutive absent syncs before closing a mirror
    "smart_manage": True,       # auto breakeven + trailing alert-stop on MANUAL legs
    "be_arm_rs": 400,           # move alert-stop to entry once unrealised >= this
    "trail_arm_rs": 900,        # arm the trailing alert-stop once unrealised >= this
    "trail_give_rs": 400,       # then keep alert-stop locking (unrealised - this)
    "wrong_side_check": True,   # run the signal engine on the underlying, flag opposing bias
    "reversal_watch": [],       # ["NATGASMINI","NIFTY",...] — alert on S/R reversals
    "reversal_tf": "15m",        # a single timeframe or a list, e.g. ["5m","15m"]
    "tp_engine": True,          # run the Turning-Point Engine on scan + pipeline (additive)
    "tp_calibration": True,     # record predictions + resolve them against future OHLC
    "tp_veto": False,           # let a high-confidence opposing turn block a scalp entry
    "tp_use_levels": False,     # use TP zone entry/stop/target when the turn is confirmed
    "tp_config": {},            # forwarded verbatim to run_turning_point_engine
    # ---- Order Adapter (execution layer) — additive, LIVE hard-gated ----
    "execution_enabled": False,   # route APPROVED scalps through the OrderManager
    "execution_mode": "PAPER",    # PAPER | SHADOW | LIVE (LIVE has server-side gates)
    "execution_auto_exit": False,  # LIVE only: place a broker exit on a local TARGET/SL
    "execution_place_broker_exits": False,   # LIVE only: pre-place SL-M after entry
    "execution_reconcile_sec": 15,  # how often the runner reconciles open intents
    "exec_stale_ltp_sec": 20,       # feed LTP older than this → no NEW entries
    "exec_stale_reconcile_sec": 90,  # last reconcile older than this → freeze entries
    "exec_rate_per_sec": 3,
    "exec_burst": 5,
    "exec_max_retries": 2,
    "default_stop_pct": 0.35,       # fallback stop distance if the contract has none
    "default_target_rr": 1.6,
    "ignore_session": False,
    "session_tz_offset_min": 330,
    "session_start": "09:20",
    "session_end": "15:05",
    "skip_open_min": 5,
    "skip_close_min": 20,
    "account": {"capital": 500000, "risk_pct": 0.5, "available_margin": 1000000},
    "risk_instrument": {"lot_size": 1},
    "limits": {"max_daily_loss_pct": 2, "max_trades": 20, "max_open_positions": 2,
               "max_consecutive_losses": 3},
    "scalp_config": {},          # forwarded verbatim to run_scalp_engine
    "watchlist": [
        # {"market":"NSE","symbol":"NIFTY","exchange":"NFO","symboltoken":"...",
        #  "instrument":"OPTION","underlying":"NIFTY","option_type":"CE",
        #  "interval":"ONE_MINUTE","timeframe":"1m"}
        # or for replay/demo (no broker):
        # {"symbol":"DEMO","replay_candles":[[t,o,h,l,c,v], ...]}
    ],
}


def _today_start_iso():
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class ScalpRunner:
    def __init__(self, broadcast=None):
        self._broadcast = broadcast
        self._errors = deque(maxlen=20)    # bounded (ts, msg) ring — see last_error setter
        self._last_error = None
        self._latch_dirty = False
        self._latch_loaded = False
        self.armed = False
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_tick_ts = None
        self.cooldown_until = None          # datetime or None
        self.consecutive_losses = 0
        self.session_note = None
        self.feed = AngelMarketFeed(cred_provider=angelone.get_stream_credentials)
        self._candle_cache = {}          # symbol -> (epoch_fetched, connector_dict)
        self._last_manage_ms = None      # wall-clock of last manage pass (latency probe)
        self._manage_latency_ms = None
        self._last_broker_sync = None
        self.broker_sync_status = None
        self._alerted = {}          # trade_id -> "TARGET" | "STOP" (latch, monitor-only)
        self._broker_miss = {}      # token -> consecutive syncs it was absent
        self._ws_alerted = {}      # trade_id -> True once a WRONG_SIDE alert fired
        self._sig_cache = {}        # underlying token -> (epoch, signal dict)
        self._rev_alerted = {}      # symbol -> "BULLISH"|"BEARISH" last alerted
        self.reversals = {}         # symbol -> last reversal dict (for /api/monitor)
        self.turning_points = {}    # "sym@tf" -> last turning_point dict (for /api/monitor)
        self._tp_alerted = {}       # "sym@tf" -> last high-confidence direction alerted
        self._fresh_tp = []         # TP alerts to emit this tick (set by _scan_reversals)
        self._latch_maps = (        # every dict here is persisted across restarts
            "_alerted", "_broker_miss", "_ws_alerted", "_rev_alerted", "_tp_alerted")
        self._owner = f"{socket.gethostname()}:{os.getpid()}"
        self.is_leader = False      # True only in the ONE process that holds the lease
        self._mkt_open = True       # cached each loop; gates reversal scan + sync cadence
        # ---- Order Adapter ----
        self.order_mgr = None            # lazily built; only the leader drives it
        self._order_mgr_mode = None      # rebuild if execution_mode changes
        self._last_exec_recon = 0.0
        self._exec_alerts = deque(maxlen=50)   # OrderManager -> loop thread, drained in _manage

    # ---------------- error ring ----------------
    @property
    def last_error(self):
        return self._last_error

    @last_error.setter
    def last_error(self, v):
        self._last_error = v
        if v:
            self._errors.append({"ts": datetime.now(timezone.utc).isoformat(), "msg": str(v)[:300]})

    # ---------------- alert-latch persistence ----------------
    def _load_latches(self):
        try:
            data = json.loads(db.get_setting(LATCH_KEY) or "{}")
        except Exception:
            data = {}
        for name in self._latch_maps:
            v = data.get(name)
            if isinstance(v, dict):
                setattr(self, name, dict(v))
        self._latch_loaded = True

    def _save_latches(self):
        snap = json.dumps({name: getattr(self, name) for name in self._latch_maps}, default=str)
        if snap == getattr(self, "_latch_snap", None):
            return                      # unchanged — skip the write
        try:
            db.set_setting(LATCH_KEY, snap)
            self._latch_snap = snap
        except Exception:
            pass

    # ---------------- config ----------------
    def get_config(self) -> dict:
        raw = db.get_setting(CONFIG_KEY)
        stored = {}
        if raw:
            try:
                stored = json.loads(raw)
            except Exception:
                stored = {}
        # Older versions persisted this secret in SQLite and sent it back from
        # status/config APIs.  Live confirmation is now server environment
        # only, so erase the legacy value while retaining all non-secret config.
        legacy_secret = stored.pop("live_confirm_token", None)
        if legacy_secret is not None:
            # Best-effort migration: do not leave a previously exposed token at
            # rest merely because nobody has saved the config again.
            try:
                db.set_setting(CONFIG_KEY, json.dumps(stored))
            except Exception:
                pass
        return _deep_merge(DEFAULT_CONFIG, stored)

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("configuration must be an object")
        if "live_confirm_token" in patch:
            raise ValueError("live confirmation must be configured with CHANAKYA_LIVE_CONFIRM_TOKEN on the server")
        unknown = set(patch) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(f"unknown configuration field(s): {', '.join(sorted(unknown))}")
        current = self.get_config()
        cfg = _deep_merge(current, patch)
        mode = str(cfg.get("execution_mode") or "").upper()
        if mode not in ("PAPER", "SHADOW", "LIVE"):
            raise ValueError("execution_mode must be PAPER, SHADOW, or LIVE")
        cfg["execution_mode"] = mode
        for key in ("execution_enabled", "execution_auto_exit", "execution_place_broker_exits"):
            if not isinstance(cfg.get(key), bool):
                raise ValueError(f"{key} must be true or false")
        if (cfg.get("execution_auto_exit") or cfg.get("execution_place_broker_exits")) and mode != "LIVE":
            raise ValueError("automatic broker exits require execution_mode LIVE")
        if (current.get("execution_enabled") and cfg.get("execution_enabled")
                and current.get("execution_mode") != mode):
            raise ValueError("disable execution before changing execution_mode")
        if mode == "LIVE" and cfg.get("execution_enabled"):
            missing = [name for name in ("CHANAKYA_API_TOKEN", "CHANAKYA_ALLOW_LIVE", "CHANAKYA_LIVE_CONFIRM_TOKEN")
                       if not os.environ.get(name)]
            if os.environ.get("CHANAKYA_ALLOW_LIVE") not in (None, "1"):
                missing.append("CHANAKYA_ALLOW_LIVE=1")
            if missing:
                raise ValueError("LIVE execution is blocked until server safeguards are set: " + ", ".join(missing))
        db.set_setting(CONFIG_KEY, json.dumps(cfg))
        return cfg

    # ---------------- lifecycle ----------------
    def start(self):
        # Every worker spawns the loop, but only the lease HOLDER does any work
        # (feed / broker polling / scans / entries). See _loop().
        db.init_db()   # idempotent; guarantees app_settings exists before _loop runs
        if self.get_config().get("auto_arm"):
            db.set_setting(ARMED_KEY, "1")
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        try:
            if self.is_leader:
                self._save_latches()
                await self.feed.stop()
        except Exception:
            pass
        db.lease_release(LEASE_KEY, self._owner)
        self.is_leader = False
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                pass

    def arm(self):
        db.set_setting(ARMED_KEY, "1")   # shared intent — the leader picks it up
        self.armed = True
        self.last_error = None

    def disarm(self):
        db.set_setting(ARMED_KEY, "0")
        self.armed = False

    # ---------------- status ----------------
    def status(self) -> dict:
        cfg = self.get_config()
        open_scalps = db.list_trades(status="OPEN", limit=200, strategy="SCALP")
        traded_today = db.count_trades_since(_today_start_iso(), strategy="SCALP")
        cd = None
        if self.cooldown_until:
            rem = (self.cooldown_until - datetime.now(timezone.utc)).total_seconds()
            cd = round(rem) if rem > 0 else None
        armed = (db.get_setting(ARMED_KEY) == "1") or bool(cfg.get("auto_arm"))

        # standby workers serve the LEADER's published live view (marks, latency,
        # last tick, errors) so the dashboard is coherent under --workers N.
        pub = {}
        if not self.is_leader:
            try:
                pub = json.loads(db.get_setting(PUB_KEY) or "{}")
            except Exception:
                pub = {}

        execution = (self.order_mgr.status() if (self.is_leader and self.order_mgr is not None)
                     else {"mode": cfg.get("execution_mode", "PAPER")})
        execution["enabled"] = bool(cfg.get("execution_enabled"))
        return {
            "armed": armed,
            "running": bool(self._task and not self._task.done()),
            "is_leader": self.is_leader,
            "runner_owner": db.lease_owner(LEASE_KEY),
            "this_worker": self._owner,
            "live_trading": bool(execution.get("enabled") and execution.get("live_enabled")),
            "paper_mode": not bool(execution.get("enabled") and execution.get("live_enabled")),
            "last_tick_ts": self.last_tick_ts if self.is_leader else pub.get("last_tick_ts"),
            "last_error": self.last_error if self.is_leader else pub.get("last_error"),
            "errors": list(self._errors) if self.is_leader else (pub.get("errors") or []),
            "session_open": self._session_open(cfg)[0],
            "session_note": self.session_note if self.is_leader else pub.get("session_note"),
            "cooldown_sec_remaining": cd,
            "consecutive_losses": self.consecutive_losses,
            "open_scalps": len(open_scalps),
            "max_concurrent": cfg["max_concurrent"],
            "traded_today": traded_today,
            "daily_cap": cfg["daily_cap"],
            "watchlist_size": len(cfg.get("watchlist") or []),
            "poll_sec": cfg["poll_sec"],
            "auto_arm": bool(cfg.get("auto_arm")),
            "fast_mode": bool(cfg.get("fast_mode")),
            "manage_latency_ms": self._manage_latency_ms if self.is_leader else pub.get("manage_latency_ms"),
            "broker_sync": bool(cfg.get("broker_sync", True)),
            "broker_sync_status": self.broker_sync_status if self.is_leader else pub.get("broker_sync_status"),
            "feed": self.feed.status() if self.is_leader else (pub.get("feed") or self.feed.status()),
            "execution": execution,
            "config": cfg,
        }

    def _publish(self):
        """Leader writes its live view for standby workers to serve."""
        try:
            db.set_setting(PUB_KEY, json.dumps({
                "last_tick_ts": self.last_tick_ts,
                "last_error": self.last_error,
                "errors": list(self._errors),
                "session_note": self.session_note,
                "manage_latency_ms": self._manage_latency_ms,
                "broker_sync_status": self.broker_sync_status,
                "feed": self.feed.status(),
            }, default=str))
        except Exception:
            pass

    # ---------------- internals ----------------
    def _markets_open(self, cfg) -> bool:
        """Any Indian market (NSE cash or MCX) plausibly open right now (IST)."""
        now_ist = datetime.now(timezone.utc) + timedelta(minutes=int(cfg["session_tz_offset_min"]))
        if now_ist.weekday() >= 5:
            return False
        m = now_ist.hour * 60 + now_ist.minute
        return (9 * 60 + 15 <= m <= 15 * 60 + 30) or (9 * 60 <= m <= 23 * 60 + 30)

    def _session_open(self, cfg):
        if cfg.get("ignore_session"):
            return True, "session filter disabled (replay/demo)"
        now_ist = datetime.now(timezone.utc) + timedelta(minutes=int(cfg["session_tz_offset_min"]))
        mod = now_ist.hour * 60 + now_ist.minute
        s_start = _parse_hhmm(cfg["session_start"], 9 * 60 + 20) + int(cfg["skip_open_min"])
        s_end = _parse_hhmm(cfg["session_end"], 15 * 60 + 5) - int(cfg["skip_close_min"])
        if now_ist.weekday() >= 5:
            return False, "weekend — market closed"
        if mod < s_start:
            return False, f"pre-session ({mod//60:02d}:{mod%60:02d} < {s_start//60:02d}:{s_start%60:02d})"
        if mod > s_end:
            return False, f"post-session ({mod//60:02d}:{mod%60:02d} > {s_end//60:02d}:{s_end%60:02d})"
        return True, "session open"

    async def _emit(self, kind, data):
        if self._broadcast:
            try:
                await self._broadcast({"type": kind, "data": data})
            except Exception:
                pass

    def _req_for(self, item: dict, cfg: dict) -> dict:
        req = {
            "account": cfg["account"],
            "risk_instrument": cfg["risk_instrument"],
            "limits": cfg["limits"],
            "scalp_config": _deep_merge(
                {k: cfg[k] for k in ("ignore_session", "session_tz_offset_min", "session_start",
                                     "session_end", "skip_open_min", "skip_close_min")},
                cfg.get("scalp_config") or {}),
            # Turning-Point Engine wiring (additive by default; veto/levels opt-in)
            "turning_point": cfg.get("tp_engine", True),
            "tp_record": cfg.get("tp_calibration", True),
            "tp_veto": bool(cfg.get("tp_veto", False)),
            "tp_use_levels": bool(cfg.get("tp_use_levels", False)),
            "tp_config": cfg.get("tp_config") or {},
        }
        if cfg.get("execution_enabled") and self.order_mgr is not None:
            req["execution"] = {
                "enabled": True,
                "mode": cfg.get("execution_mode", "PAPER"),
                "manager": self.order_mgr,
                "ltp_provider": self.feed.get_ltp,
                "symboltoken": item.get("symboltoken"),
                "tradingsymbol": item.get("tradingsymbol"),
                "instrument": {"exchange": item.get("exchange"),
                               "product": item.get("product") or "INTRADAY",
                               "option_type": item.get("option_type")},
            }
        req.update(item)
        if item.get("replay_candles") is not None:
            req["candles"] = item["replay_candles"]
        else:
            cached = self._cached_candles(item, cfg)
            if cached:
                req["candles"] = cached
        return req

    def _cached_candles(self, item: dict, cfg: dict):
        """REST candles for a watchlist symbol, refetched at most once per
        candle_refresh_sec. Keeps the entry-signal path off the network on
        every tick — decision latency drops from ~300ms to sub-ms."""
        key = "|".join(str(item.get(k) or "") for k in ("symbol", "symboltoken", "timeframe"))
        ttl = int(cfg.get("candle_refresh_sec") or 60)
        now = time.time()
        hit = self._candle_cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
        try:
            conn = angelone.fetch_candles(
                market=item.get("market"), symbol=item.get("symbol"),
                exchange=item.get("exchange"), symboltoken=item.get("symboltoken"),
                interval=item.get("interval"), fromdate=None, todate=None,
                timeframe=item.get("timeframe") or "1m", instrument=item.get("instrument"))
        except Exception as e:
            self.last_error = f"candle fetch: {type(e).__name__}: {e}"
            return hit[1] if hit else None
        candles = conn.get("candles") or []
        if candles:
            self._candle_cache[key] = (now, candles)
            return candles
        return hit[1] if hit else None

    def _feed_tokens(self, cfg: dict) -> list[dict]:
        """Resolve every non-replay watchlist symbol + every open scalp's
        underlying to {token, exchange_type} for the WebSocket feed."""
        want, seen = [], set()

        def add(symbol, explicit_token=None, explicit_ex=None):
            tok, ex = explicit_token, explicit_ex
            if not tok:
                meta = instruments.resolve(symbol)
                if not meta:
                    return
                tok = meta.get("symboltoken")
                ex = EXCHANGE_TYPE.get(str(meta.get("exchange") or "NSE").upper(), 1)
            if tok and tok not in seen:
                seen.add(tok)
                want.append({"token": str(tok), "exchange_type": ex or 1})

        for item in (cfg.get("watchlist") or []):
            if item.get("replay_candles") is not None:
                continue
            add(item.get("underlying") or item.get("symbol"),
                item.get("symboltoken"),
                EXCHANGE_TYPE.get(str(item.get("exchange") or "").upper()) if item.get("exchange") else None)
        for t in db.list_open_managed():
            tok = t.get("symboltoken")
            if not tok:
                continue  # never registry-resolve a leg; that maps index spot only
            ex = EXCHANGE_TYPE.get(str(t.get("market") or "").upper(), 5 if "MCX" in str(t.get("market") or "").upper() else 1)
            add(None, str(tok), ex)
        return want

    def _rest_mark_for(self, trade: dict, cfg: dict):
        """Fallback mark for an illiquid contract the WS LTP stream never ticks:
        last 1m candle close, cached ≤ candle_refresh_sec per token."""
        tok = str(trade.get("symboltoken") or "")
        if not tok:
            return None
        key = "MARK|" + tok
        ttl = int(cfg.get("candle_refresh_sec") or 60)
        now = time.time()
        hit = self._candle_cache.get(key)
        if hit and (now - hit[0]) < ttl:
            return hit[1]
        try:
            conn = angelone.fetch_candles(
                market=trade.get("market"), symbol=trade.get("underlying"),
                exchange=trade.get("market"), symboltoken=tok, interval="ONE_MINUTE",
                fromdate=None, todate=None, timeframe="1m", instrument=trade.get("instrument"))
        except Exception:
            return hit[1] if hit else None
        cds = conn.get("candles") or []
        if cds:
            px = float(cds[-1]["c"])
            self._candle_cache[key] = (now, px)
            return px
        return hit[1] if hit else None

    def _sane_option_mark(self, trade: dict, mark):
        """Guard against a mark from the wrong instrument (e.g. an option leg
        accidentally priced at the underlying future). An option premium is
        > 0 and never near/above the strike or a large multiple of entry."""
        if mark is None:
            return None
        try:
            mark = float(mark)
        except (TypeError, ValueError):
            return None
        if mark <= 0:
            return None
        is_option = bool((trade.get("option_type") or "").strip()) or \
            "OPTION" in str(trade.get("instrument") or "").upper()
        if is_option:
            strike = trade.get("strike") or 0
            entry = trade.get("entry") or 0
            if strike and mark >= strike * 0.9:
                return None
            if entry and (mark > entry * 6 or mark < entry / 6):
                return None
        return mark

    def _ltp_for(self, trade: dict, cfg: dict):
        """Mark price for an open position, freshest source first:
        1. live WebSocket LTP for THIS contract's own token
        2. replay_candles close (scalp watchlist)
        Registry resolution is NOT used here — that maps index *spot* symbols
        and must never price an option leg.  An un-timestamped REST candle is
        not safe for manual target/stop alerts, so unavailable data returns
        None and the monitor fails closed."""
        tok = str(trade.get("symboltoken") or "")
        if tok:
            live = self._sane_option_mark(trade, self.feed.get_ltp(tok))
            if live is not None:
                return live

        tkey = trade.get("underlying") or trade.get("market") or ""
        for item in (cfg.get("watchlist") or []):
            ikey = item.get("underlying") or item.get("symbol") or ""
            if ikey != tkey:
                continue
            itok = item.get("symboltoken")
            if itok:
                live = self._sane_option_mark(trade, self.feed.get_ltp(itok))
                if live is not None:
                    return live
            candles = item.get("replay_candles")
            if candles:
                last = candles[-1]
                try:
                    return float(last[4] if isinstance(last, list) else last.get("c"))
                except (TypeError, ValueError, IndexError):
                    return None

        return None

    def _smart_stop(self, trade: dict, cfg: dict, live_pnl):
        """Auto breakeven + trailing on the ALERT stop of a MANUAL leg.
        Returns a new stop_loss (tighter/locked) or None. Alert-only — the
        app cannot place a broker order; this raises the price at which it
        shouts EXIT so profit is protected as the trade runs."""
        if not cfg.get("smart_manage", True) or live_pnl is None:
            return None
        entry = trade.get("entry") or 0
        qty = trade.get("quantity") or 0
        if not entry or not qty:
            return None
        sign = 1 if trade.get("direction") == "BUY" else -1
        sl = trade.get("stop_loss")
        new_sl = sl
        if live_pnl >= float(cfg.get("be_arm_rs") or 400):
            cand = entry
            new_sl = cand if new_sl is None else (max(new_sl, cand) if sign > 0 else min(new_sl, cand))
        trail_arm = float(cfg.get("trail_arm_rs") or 900)
        if live_pnl >= trail_arm:
            locked = live_pnl - float(cfg.get("trail_give_rs") or 400)
            cand = round(entry + sign * (locked / qty), 2)
            new_sl = cand if new_sl is None else (max(new_sl, cand) if sign > 0 else min(new_sl, cand))
        if new_sl is not None and new_sl != sl:
            # never loosen past current
            if sl is None or (sign > 0 and new_sl > sl) or (sign < 0 and new_sl < sl):
                return round(new_sl, 2)
        return None

    def _scan_reversals(self, cfg: dict):
        """For each reversal_watch symbol + each held underlying, run the
        reversal detector (cached ~90s) and return freshly-fired turns.
        Skipped entirely when markets are closed — a reversal on a stale
        candle is meaningless and the REST candle fetch is pure waste."""
        from .reversal import detect_reversal
        if not self._mkt_open:
            return []
        syms = set(cfg.get("reversal_watch") or [])
        for t in db.list_trades(status="OPEN", limit=200, strategy="MANUAL"):
            if t.get("underlying"):
                syms.add(t["underlying"])
        tfs = cfg.get("reversal_tf") or "15m"
        tfs = [tfs] if isinstance(tfs, str) else list(tfs)
        fresh, fresh_tp = [], []
        for sym in syms:
            meta = instruments.resolve(sym)
            if not meta:
                continue
            for tf in tfs:
                key = f"REV|{meta.get('symboltoken')}|{tf}"
                lkey = f"{sym}@{tf}"
                now = time.time()
                hit = self._candle_cache.get(key)
                if hit and now - hit[0] < 90:
                    cds = hit[1]
                else:
                    try:
                        conn = angelone.fetch_candles(
                            market=meta.get("market"), symbol=sym, exchange=meta.get("exchange"),
                            symboltoken=meta.get("symboltoken"), interval=None, fromdate=None,
                            todate=None, timeframe=tf, instrument="FUT")
                    except Exception:
                        continue
                    if conn.get("data_status") != "OK":
                        continue
                    cds = conn["candles"]
                    self._candle_cache[key] = (now, cds)
                r = detect_reversal(cds)
                r["symbol"] = sym
                r["timeframe"] = tf
                self.reversals[lkey] = r
                rev = r.get("reversal")
                if rev and self._rev_alerted.get(lkey) != rev:
                    self._rev_alerted[lkey] = rev
                    fresh.append(r)
                elif not rev and lkey in self._rev_alerted:
                    self._rev_alerted.pop(lkey, None)

                # Turning-Point engine on the same candles → continuous
                # prediction stream for calibration (+ high-confidence alert)
                if cfg.get("tp_engine", True):
                    try:
                        from .engines.turning_point_engine import run_turning_point_engine
                        from . import tp_calibration
                        tp = run_turning_point_engine({
                            "candles": cds, "config": cfg.get("tp_config") or {},
                            "calibration": tp_calibration.load()})
                        self.turning_points[lkey] = {**tp, "symbol": sym, "timeframe": tf}
                        tp_calibration.record(tp, sym, tf)
                        if tp.get("high_confidence") and self._tp_alerted.get(lkey) != tp["direction"]:
                            self._tp_alerted[lkey] = tp["direction"]
                            fresh_tp.append({**tp, "symbol": sym, "timeframe": tf})
                        elif not tp.get("high_confidence") and lkey in self._tp_alerted:
                            self._tp_alerted.pop(lkey, None)
                    except Exception:
                        pass
        self._fresh_tp = fresh_tp
        return fresh

    # ---------------- order adapter (execution layer) ----------------
    def _exec_alert(self, kind: str, payload: dict):
        """OrderManager callback — runs on whatever thread submit()/reconcile()
        is on. Just queue; _manage drains on the loop thread."""
        self._exec_alerts.append({"kind": kind, "data": payload})

    def _ensure_order_mgr(self, cfg: dict):
        """Leader-only. Build (or rebuild on a mode change) the single
        OrderManager. Never raises — execution must not break the runner."""
        # Turning execution off must halt new submissions immediately, but an
        # already-created manager remains in monitor/reconcile mode.  Dropping
        # it would abandon visibility of an in-flight or live broker position.
        if not cfg.get("execution_enabled"):
            if self.order_mgr is not None:
                self.order_mgr.config["auto_exit"] = False
                self.order_mgr.config["place_broker_exits"] = False
            return
        mode = (cfg.get("execution_mode") or "PAPER").upper()
        if self.order_mgr is not None and self._order_mgr_mode == mode:
            self.order_mgr.config.update({
                "auto_exit": bool(cfg.get("execution_auto_exit")),
                "place_broker_exits": bool(cfg.get("execution_place_broker_exits")),
                "exec_stale_ltp_sec": cfg.get("exec_stale_ltp_sec", 20),
                "exec_stale_reconcile_sec": cfg.get("exec_stale_reconcile_sec", 90),
                "exec_reconcile_sec": cfg.get("execution_reconcile_sec", 15),
            })
            return
        try:
            from .execution import OrderManager
            ex_cfg = {
                "execution_mode": mode,
                "auto_exit": bool(cfg.get("execution_auto_exit")),
                "place_broker_exits": bool(cfg.get("execution_place_broker_exits")),
                "exec_stale_ltp_sec": cfg.get("exec_stale_ltp_sec", 20),
                "exec_stale_reconcile_sec": cfg.get("exec_stale_reconcile_sec", 90),
                "exec_reconcile_sec": cfg.get("execution_reconcile_sec", 15),
                "exec_rate_per_sec": cfg.get("exec_rate_per_sec", 3),
                "exec_burst": cfg.get("exec_burst", 5),
                "exec_max_retries": cfg.get("exec_max_retries", 2),
                "default_stop_pct": cfg.get("default_stop_pct", 0.35),
                "default_target_rr": cfg.get("default_target_rr", 1.6),
            }
            self.order_mgr = OrderManager(mode=mode, config=ex_cfg,
                                          ltp_provider=self.feed.get_ltp,
                                          on_alert=self._exec_alert)
            self._order_mgr_mode = mode
            try:
                self.order_mgr.recover()     # reconcile any intents left by a prior process
            except Exception as e:
                self.last_error = f"order recover: {type(e).__name__}: {e}"
        except Exception as e:
            self.order_mgr = None
            self.last_error = f"order mgr init: {type(e).__name__}: {e}"

    def _daily_realised_pnl(self) -> float:
        """Sum of realised P&L on trades CLOSED since midnight UTC (SCALP+MANUAL).
        Feeds the OrderManager's daily risk halt."""
        total = 0.0
        start = _today_start_iso()
        for strat in ("SCALP", "MANUAL"):
            for t in db.list_trades(status="CLOSED", limit=500, strategy=strat):
                if (t.get("closed_ts") or "") >= start:
                    try:
                        total += float(t.get("pnl") or 0)
                    except (TypeError, ValueError):
                        pass
        return round(total, 2)

    def _drive_execution(self, cfg: dict):
        """Leader-only, off the entry path. Mark every open local monitor to the
        live feed LTP (target/SL/trail decisions), enforce the daily max-loss
        halt, and periodically reconcile open intents against the broker
        (source of truth)."""
        om = self.order_mgr
        if om is None:
            return

        # daily risk / max-loss halt — blocks NEW entries only
        cap = float((cfg.get("account") or {}).get("capital") or 0)
        max_loss_pct = float((cfg.get("limits") or {}).get("max_daily_loss_pct") or 0)
        if cap > 0 and max_loss_pct > 0:
            pnl = self._daily_realised_pnl()
            limit = -(cap * max_loss_pct / 100.0)
            if pnl <= limit:
                om.set_risk_halt(True, f"daily realised {pnl} <= limit {round(limit, 2)}")
            else:
                om.set_risk_halt(False)

        for tid, mon in list(om.monitors.items()):
            if mon.closed:
                continue
            st = om.states.get(tid)
            tok = st.symboltoken if st else None
            ltp = self.feed.get_ltp(tok) if tok else None
            if ltp is not None:
                om.on_ltp(tid, ltp)
        now = time.time()
        if now - self._last_exec_recon >= int(cfg.get("execution_reconcile_sec", 15)):
            self._last_exec_recon = now
            try:
                om.reconcile_all()
            except Exception as e:
                self.last_error = f"order reconcile: {type(e).__name__}: {e}"

    def _wrong_side(self, trade: dict, cfg: dict):
        """Run the signal engine on the position's UNDERLYING; return
        'WRONG_SIDE' if a clean directional read opposes the position."""
        if not cfg.get("wrong_side_check", True) or not self._mkt_open:
            return None
        meta = instruments.resolve(trade.get("underlying") or "")
        if not meta:
            return None
        utok = meta.get("symboltoken")
        now = time.time()
        hit = self._sig_cache.get(utok)
        if hit and now - hit[0] < 90:
            sig = hit[1]
        else:
            try:
                conn = angelone.fetch_candles(
                    market=meta.get("market"), symbol=trade.get("underlying"),
                    exchange=meta.get("exchange"), symboltoken=utok, interval=None,
                    fromdate=None, todate=None, timeframe="15m", instrument="FUT")
            except Exception:
                return None
            if conn.get("data_status") != "OK":
                return None
            sig = run_signal_engine({"symbol": trade.get("underlying"), "timeframe": "15m",
                                     "source": "ANGELONE", "data_status": "OK",
                                     "candles": conn["candles"], "config": {}})
            self._sig_cache[utok] = (now, sig)
        d = sig.get("direction")
        conf = sig.get("confidence") or 0
        if d not in ("BUY", "SELL") or conf < 45:
            return None
        ot = (trade.get("option_type") or "").upper()
        pos_bull = ot == "CE" or (ot == "" and trade.get("direction") == "BUY")
        pos_bear = ot == "PE" or (ot == "" and trade.get("direction") == "SELL")
        if (pos_bull and d == "SELL") or (pos_bear and d == "BUY"):
            return f"WRONG_SIDE ({sig.get('market_regime')}, {d} bias {conf}%)"
        return None

    async def _sync_broker_positions(self, cfg):
        """Pull live net positions from Angel One and auto-register any that
        aren't already tracked, so a position opened in the broker terminal
        shows up on the Live Monitor without a manual /track call.
        Throttled by broker_sync_sec (default 30). New rows get no target/stop
        until the user sets them — they P&L-track only."""
        every = int(cfg.get("broker_sync_sec") or 30)
        # markets shut + nothing open → the only reason to poll is to notice a
        # position you just opened; a 5-min cadence is plenty and saves ~10 calls/hr
        if not self._mkt_open and not db.list_open_managed():
            every = max(every, 300)
        now = time.time()
        if self._last_broker_sync and (now - self._last_broker_sync) < every:
            return
        self._last_broker_sync = now
        res = await asyncio.to_thread(angelone.fetch_positions)
        self.broker_sync_status = res.get("status")
        if res.get("status") != "OK":
            return
        live_tokens = {str(p.get("symboltoken") or "") for p in res.get("positions", [])}
        open_manual = db.list_trades(status="OPEN", limit=300, strategy="MANUAL")
        known = {str(t.get("symboltoken") or "") for t in open_manual}

        # broker-flat: only close a mirror after the token has been ABSENT from
        # getPosition for `broker_flat_confirm` consecutive syncs (default 3) —
        # one partial/transient broker read must never square a mirror.
        closed_tokens = {str(p.get("symboltoken") or "") for p in res.get("closed", [])}
        open_by_tok = {str(t.get("symboltoken") or ""): t for t in open_manual}

        # 1) round-turned broker positions → realised P&L. If a mirror is still
        # OPEN for that token, CLOSE THE SAME ROW (no duplicate); else record one.
        seen_closed = {
            (t.get("symboltoken"), t.get("reason"))
            for t in db.list_trades(status="CLOSED", limit=500, strategy="MANUAL")
        }
        for p in res.get("closed", []):
            tok = str(p.get("symboltoken") or "")
            rsn = f"broker realised {p.get('realised_pnl')}"
            rp = p.get("realised_pnl") or 0
            b, s = p.get("buy_avg") or 0, p.get("sell_avg") or 0
            mirror = open_by_tok.get(tok)
            if mirror:
                await asyncio.to_thread(close_trade, mirror["trade_id"], s or b, None, "BROKER_REALISED")
                db.update_trade(mirror["trade_id"], {
                    "pnl": rp, "result": "WIN" if rp > 0 else ("LOSS" if rp < 0 else "FLAT"),
                    "reason": rsn})
                self._broker_miss.pop(tok, None)
                await self._emit("position_exit", db.get_trade(mirror["trade_id"]))
                continue
            if not tok or (tok, rsn) in seen_closed:
                continue
            qty = abs(p.get("traded_qty") or 0)
            row = await asyncio.to_thread(open_trade, {
                "signal_id": None, "market": p.get("exchange") or "",
                "underlying": p.get("symbol") or "", "instrument": "OPTION" if p.get("option_type") else "FUT",
                "expiry": p.get("expiry") or "", "strike": p.get("strike") or 0,
                "option_type": p.get("option_type") or "", "direction": "BUY",
                "timeframe": "", "entry": b, "target_1": None, "stop_loss": None,
                "trailing_stop": 0, "quantity": qty, "market_regime": "",
                "oi_evidence": "", "reason": rsn, "strategy": "MANUAL",
                "setup": None, "atr_pct": None, "max_hold_sec": None, "symboltoken": str(tok),
            })
            await asyncio.to_thread(close_trade, row["trade_id"], s or b, None, "BROKER_REALISED")
            db.update_trade(row["trade_id"], {
                "pnl": rp, "result": "WIN" if rp > 0 else ("LOSS" if rp < 0 else "FLAT")})
            await self._emit("position_exit", db.get_trade(row["trade_id"]))

        # 2) broker-flat: token vanished from BOTH lists for N consecutive syncs
        need = int(cfg.get("broker_flat_confirm") or 3)
        for t in open_manual:
            tok = str(t.get("symboltoken") or "")
            if "auto-synced" not in (t.get("reason") or "") or tok in live_tokens or tok in closed_tokens:
                self._broker_miss.pop(tok, None)
                continue
            self._broker_miss[tok] = self._broker_miss.get(tok, 0) + 1
            if self._broker_miss[tok] < need:
                continue
            mark = await asyncio.to_thread(self._rest_mark_for, t, cfg)
            closed = await asyncio.to_thread(
                close_trade, t["trade_id"], self._sane_option_mark(t, mark) or t.get("entry") or 0,
                None, "BROKER_FLAT")
            self._broker_miss.pop(tok, None)
            if closed:
                await self._emit("position_exit", closed)

        for p in res.get("positions", []):
            tok = p.get("symboltoken") or ""
            if not tok or tok in known:
                continue
            # re-check the DB right before insert (defensive against a manual
            # /track that landed between the snapshot above and now)
            if db.find_open_by_token(str(tok), strategy="MANUAL"):
                known.add(tok)
                continue
            row = await asyncio.to_thread(open_trade, {
                "signal_id": None, "market": p.get("exchange") or "",
                "underlying": p.get("symbol") or p.get("tradingsymbol") or "",
                "instrument": "OPTION" if p.get("option_type") else "FUT",
                "expiry": p.get("expiry") or "", "strike": p.get("strike") or 0,
                "option_type": p.get("option_type") or "", "direction": p.get("direction"),
                "timeframe": "", "entry": p.get("avg_price"),
                "target_1": None, "target_2": None, "stop_loss": None, "trailing_stop": 0,
                "quantity": abs(p.get("net_qty") or 0),
                "probability": None, "confidence": None, "market_regime": "",
                "oi_evidence": "", "reason": "auto-synced from Angel One positions",
                "strategy": "MANUAL", "setup": None, "atr_pct": None,
                "max_hold_sec": None, "symboltoken": str(tok),
            })
            known.add(tok)
            await self._emit("position_open", row)

    async def _manage(self):
        """Always runs (armed or not): keep the feed subscribed and mark every
        open SCALP + MANUAL position to live LTP so target / stop / trail / time
        exits fire. MANUAL positions emit position_* events and never trigger the
        scalp loss-cooldown."""
        cfg = self.get_config()
        self.last_tick_ts = datetime.now(timezone.utc).isoformat()
        try:
            self.feed.subscribe(self._feed_tokens(cfg))
        except Exception as e:
            self.last_error = f"feed subscribe: {type(e).__name__}: {e}"

        if cfg.get("broker_sync", True):
            try:
                await self._sync_broker_positions(cfg)
            except Exception as e:
                self.last_error = f"broker sync: {type(e).__name__}: {e}"

        for t in db.list_open_managed():
            ltp = await asyncio.to_thread(self._ltp_for, t, cfg)   # cached; network off-loop
            if ltp is None:
                continue
            updated = await asyncio.to_thread(update_trade_price, t["trade_id"], ltp)
            if not updated:
                continue
            manual = (updated.get("strategy") or "").upper() == "MANUAL"
            if manual:
                tid = updated["trade_id"]
                # --- smart: auto breakeven + trailing on the ALERT stop ---
                try:
                    new_sl = self._smart_stop(updated, cfg, updated.get("pnl"))
                    if new_sl is not None:
                        db.update_trade(tid, {"stop_loss": new_sl})
                        updated["stop_loss"] = new_sl
                        await self._emit("position_update", {**updated, "smart_stop": new_sl})
                except Exception as e:
                    self.last_error = f"smart_stop: {type(e).__name__}: {e}"
                # --- smart: wrong-side detection via the signal engine ---
                try:
                    ws = self._wrong_side(updated, cfg)
                    if ws and not self._ws_alerted.get(tid):
                        self._ws_alerted[tid] = True
                        await asyncio.to_thread(_tg_send,
                            f"⚠️ <b>WRONG-SIDE — {updated.get('underlying')} "
                            f"{updated.get('option_type')}{int(updated.get('strike') or 0)}</b>\n"
                            f"Signal engine now reads against this position: {ws}\n"
                            f"Entry {updated.get('entry')}  Paper P&L {updated.get('pnl')}\n"
                            f"⚠️ Monitor-only — decide the exit.",
                            os.environ.get("TELEGRAM_CHAT_ID"))
                        await self._emit("position_alert", {**updated, "hit": "WRONG_SIDE", "note": ws})
                    elif not ws:
                        self._ws_alerted.pop(tid, None)
                except Exception as e:
                    self.last_error = f"wrong_side: {type(e).__name__}: {e}"

                hit = updated.get("_hit")
                if hit and self._alerted.get(tid) != hit:
                    self._alerted[tid] = hit
                    await asyncio.to_thread(notify_position_alert,
                                            {**updated, "exit_reason": hit,
                                             "exit_price": updated.get("entry") and
                                             round((updated["entry"] + (updated.get("pnl") or 0) /
                                                    (updated.get("quantity") or 1)), 2)})
                    await self._emit("position_alert", {**updated, "hit": hit})
                elif not hit and tid in self._alerted:
                    self._alerted.pop(tid, None)          # re-arm once back in band
                await self._emit("position_update", updated)
            elif updated.get("status") == "CLOSED":
                self._on_close(updated, cfg)
                await self._emit("scalp_closed", updated)
            else:
                await self._emit("scalp_update", updated)

        # combos: auto-link CE+PE legs into a strangle, then evaluate the pair
        # as ONE unit (combined debit vs mark). Monitor-only — alerts, no close.
        try:
            for c in await asyncio.to_thread(combos.auto_detect_and_create):
                await self._emit("combo_open", c)
            for p in await asyncio.to_thread(combos.evaluate, telegram.notify_combo_alert):
                await self._emit("combo_alert", p)
        except Exception as e:
            self.last_error = f"combo eval: {type(e).__name__}: {e}"

        self._save_latches()   # persist alert latches (only writes when changed)

        # reversal scan — S/R turn detection for held + watched symbols
        try:
            for r in await asyncio.to_thread(self._scan_reversals, cfg):
                await asyncio.to_thread(_tg_send,
                    f"🔄 <b>REVERSAL {r.get('timeframe','')} — {r['symbol']} {r['reversal']} {r['kind']}</b>\n"
                    f"Level {r.get('level')}  ·  price {r.get('price')}  ·  conf {r.get('confidence')}%\n"
                    f"Trade: buy {r.get('option')}  entry {r.get('entry')}  "
                    f"SL {r.get('stop')}  T1 {r.get('target_1')}  T2 {r.get('target_2')}  (RR {r.get('risk_reward')})\n"
                    f"{' · '.join(r.get('reason') or [])}\n⚠️ Monitor-only — you place it.",
                    os.environ.get("TELEGRAM_CHAT_ID"))
                await self._emit("reversal_signal", r)
        except Exception as e:
            self.last_error = f"reversal scan: {type(e).__name__}: {e}"

        # Turning-Point high-confidence alerts (populated by _scan_reversals)
        for tp in getattr(self, "_fresh_tp", []):
            tr = tp.get("trade_ref") or {}
            await asyncio.to_thread(_tg_send,
                f"🎯 <b>TURNING POINT {tp.get('timeframe','')} — {tp['symbol']} {tp['direction']}</b>\n"
                f"conf {tp['confidence']}%  ·  p_up {tp['p_up']}  ·  turn {tp['turn']}\n"
                f"Expected move: {tp['expected_move']['direction']} {tp['expected_move']['pts']} pts "
                f"({tp['expected_move']['pct']}%)\n"
                f"Plan: buy {tr.get('option','?')}  entry {tr.get('entry_ref')}  "
                f"SL {tr.get('stop_loss')}  T1 {tr.get('target_1')}  T2 {tr.get('target_2')}  "
                f"(RR {tr.get('risk_reward')})\n"
                f"{' · '.join(tp.get('reason') or [])[:3] if isinstance(tp.get('reason'), list) else ''}\n"
                f"⚠️ Deterministic zone estimate — monitor-only, you place it.",
                os.environ.get("TELEGRAM_CHAT_ID"))
            await self._emit("turning_point_signal", tp)
        self._fresh_tp = []

        # Turning-Point calibration: resolve due predictions, recalibrate
        if self._mkt_open and cfg.get("tp_calibration", True):
            try:
                from . import tp_calibration
                await asyncio.to_thread(tp_calibration.tick, angelone.fetch_candles)
            except Exception as e:
                self.last_error = f"tp calib: {type(e).__name__}: {e}"

        # Order Adapter: keep the singleton alive, mark monitors to live LTP,
        # reconcile open intents, and flush any alerts it raised.
        self._ensure_order_mgr(cfg)
        if self.order_mgr is not None:
            try:
                await asyncio.to_thread(self._drive_execution, cfg)
            except Exception as e:
                self.last_error = f"exec drive: {type(e).__name__}: {e}"
        while self._exec_alerts:
            a = self._exec_alerts.popleft()
            await self._emit(f"execution_{a['kind']}", a["data"])
            if a["kind"] in ("exit_signal", "reconcile_freeze", "order_rejected", "order_dead"):
                try:
                    d = a["data"]
                    await asyncio.to_thread(_tg_send,
                        f"⚙️ <b>ORDER ADAPTER — {a['kind'].replace('_', ' ').upper()}</b>\n"
                        f"trade {d.get('trade_id')}  ·  {d.get('reason') or d.get('status') or ''}\n"
                        f"{'; '.join(d.get('reasons') or []) if d.get('reasons') else d.get('note') or ''}\n"
                        f"⚠️ Mode {self.get_config().get('execution_mode')} — "
                        f"{'auto-exit ON' if self.get_config().get('execution_auto_exit') else 'monitor-only, you act'}.",
                        os.environ.get("TELEGRAM_CHAT_ID"))
                except Exception:
                    pass

    async def _entries(self):
        cfg = self.get_config()
        session_ok, note = self._session_open(cfg)
        self.session_note = note
        if not session_ok:
            return
        if self.cooldown_until and datetime.now(timezone.utc) < self.cooldown_until:
            return
        self.cooldown_until = None

        if db.count_trades_since(_today_start_iso(), strategy="SCALP") >= int(cfg["daily_cap"]):
            self.session_note = "daily scalp cap reached"
            return

        open_now = len(db.list_trades(status="OPEN", limit=200, strategy="SCALP"))
        held_underlyings = {t.get("underlying") for t in db.list_trades(status="OPEN", limit=200, strategy="SCALP")}

        cap = int(cfg["daily_cap"])
        opened_this_tick = 0
        for item in (cfg.get("watchlist") or []):
            if open_now >= int(cfg["max_concurrent"]):
                break
            if db.count_trades_since(_today_start_iso(), strategy="SCALP") + opened_this_tick >= cap:
                break
            u = item.get("underlying") or item.get("symbol")
            if u in held_underlyings:
                continue
            try:
                res = await asyncio.to_thread(
                    lambda it=item: run_scalp_pipeline(self._req_for(it, cfg)))
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                continue
            await self._emit("scalp_signal", res["contract"])
            if res.get("trade"):
                open_now += 1
                opened_this_tick += 1
                held_underlyings.add(u)
                await self._emit("scalp_open", res["trade"])

    def _on_close(self, trade: dict, cfg: dict):
        pnl = trade.get("pnl") or 0
        if pnl < 0:
            self.consecutive_losses += 1
            cd = int(cfg.get("loss_cooldown_sec") or 0)
            if cd > 0:
                self.cooldown_until = datetime.now(timezone.utc) + timedelta(seconds=cd)
        else:
            self.consecutive_losses = 0

    async def _loop(self):
        while not self._stop.is_set():
            cfg = self.get_config()

            # --- single-active-runner election -------------------------------
            leader = await asyncio.to_thread(
                db.lease_acquire, LEASE_KEY, self._owner, LEASE_TTL_SEC)
            if leader and not self.is_leader:
                self.is_leader = True
                self.last_error = None
                if not self._latch_loaded:
                    self._load_latches()     # restore alert latches — no re-spam on restart
                self.feed.start()             # only the leader connects the WS feed
            elif not leader and self.is_leader:
                self.is_leader = False
                self.order_mgr = None         # a standby must not drive execution
                self._order_mgr_mode = None
                try:
                    await self.feed.stop()    # relinquish the feed if leadership lost
                except Exception:
                    pass

            if not self.is_leader:
                self.session_note = "standby — another instance is the active runner"
                self._manage_latency_ms = None
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                continue

            # --- leader: reconcile shared arm intent, then do the work ------
            armed_raw = db.get_setting(ARMED_KEY)
            self.armed = (armed_raw == "1") or bool(cfg.get("auto_arm"))
            self._mkt_open = self._markets_open(cfg)

            t0 = time.perf_counter()
            try:
                await self._manage()          # exits / sync / scans — always (leader)
                if self.armed:
                    await self._entries()     # new positions only when armed
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            self._manage_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
            self._publish()

            # adaptive cadence — don't burn CPU/network when there's nothing to do
            open_cnt = len(db.list_open_managed())
            base = 1 if cfg.get("fast_mode") else max(1, int(cfg["poll_sec"]))
            if open_cnt == 0 and not self._mkt_open:
                interval = 60          # deep idle: markets shut, nothing open
            elif open_cnt == 0 and not self.armed:
                interval = max(base, 15)   # market hours but flat & disarmed
            else:
                interval = base
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass
