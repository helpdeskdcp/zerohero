"""
SmartScalperScheduler — wires SmartScalperPaperEngine into the live app loop
(spec section 48, the deferred follow-on; `manage()`/`evaluate()` were
previously endpoint-only — this is why zero SMART_SCALPER trades existed
despite the engine being built).

Same shape as AutoScalpRunner: a DB-backed ARMED flag (`app_settings`) + a
cross-process lease for leader election + a poll loop, so only one process
ever ticks it even under multiple uvicorn workers.

SAFETY
------
- Defaults DISARMED. Wiring the scheduler into the app lifespan does NOT start
  opening positions by itself — call `/api/smart-scalper/arm` first (or set
  SMART_SCALPER_AUTO_ARM=1). Every entry still goes through
  SmartScalperPaperEngine._open -> autoscalp.safeguards.Safeguards.check_entry;
  that gate is never bypassed here.
- PAPER ONLY. Nothing in this module or SmartScalperPaperEngine places a real
  order (see tests/test_smart_scalper_paper.py's no-order-path check, which
  globs every *.py in this package). `live_trading` stays false.
- `manage()` (mark + exit open positions) runs every tick regardless of armed
  state, so an already-open paper position is never abandoned by disarming.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import traceback

from .. import db
from .paper_engine import SmartScalperPaperEngine

_log = logging.getLogger(__name__)

LEASE_KEY = "smart_scalper_lease"
LEASE_TTL_SEC = 30
ARMED_KEY = "smart_scalper_armed"
DEFAULT_POLL_SEC = int(os.environ.get("SMART_SCALPER_POLL_SEC", "60"))


class SmartScalperScheduler:
    def __init__(self, *, profile: str | None = None, poll_sec: int | None = None,
                owner: str | None = None):
        self.engine = SmartScalperPaperEngine(profile=profile)
        self.poll_sec = max(15, int(poll_sec if poll_sec is not None else DEFAULT_POLL_SEC))
        self.owner = owner or f"{os.uname().nodename}:{os.getpid()}"
        self.armed = False
        self.is_leader = False
        self.last_tick_ts: float | None = None
        self.last_error: str | None = None
        self.last_result: dict | None = None
        self.ticks = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception as e:
                _log.warning("scheduler stop(): loop task did not finish cleanly: %r", e)
        try:
            db.lease_release(LEASE_KEY, self.owner)
        except Exception as e:
            _log.warning("scheduler stop(): lease_release failed, another owner may wait out the TTL: %r", e)

    # ------------------------------------------------------------------ arm/disarm
    def arm(self):
        db.set_setting(ARMED_KEY, "1")
        self.armed = True

    def disarm(self):
        db.set_setting(ARMED_KEY, "0")
        self.armed = False

    # ------------------------------------------------------------------ status
    def status(self) -> dict:
        return {
            "armed": self.armed, "is_leader": self.is_leader, "poll_sec": self.poll_sec,
            "profile": self.engine.profile.get("name"), "ticks": self.ticks,
            "last_tick_ts": self.last_tick_ts, "last_error": self.last_error,
            "last_result": self.last_result, "lease_owner": self._lease_owner(),
            "live_trading": False,
        }

    def _lease_owner(self):
        try:
            return db.lease_owner(LEASE_KEY)
        except Exception as e:
            _log.debug("_lease_owner failed: %r", e)
            return None

    # ------------------------------------------------------------------ tick
    def _market_open(self) -> bool:
        try:
            from .. import market_calendar
            return bool(market_calendar.is_trading("NSE") or market_calendar.is_trading("MCX")
                       or market_calendar.is_trading("BSE"))
        except Exception as e:
            _log.debug("_market_open: calendar check failed, failing open: %r", e)
            return True    # fail open — the engine's own data-quality gates still protect

    async def tick_once(self):
        self.last_tick_ts = time.time()
        self.ticks += 1
        if not self._market_open():
            self.last_result = {"skipped": "market_closed"}
            return
        managed = None
        try:
            managed = await asyncio.to_thread(self.engine.manage, use_cache=True)
        except Exception as e:                                  # never let a mark/exit failure kill the loop
            managed = {"error": f"{type(e).__name__}: {e}"}
        evaluated = None
        if self.armed:
            try:
                evaluated = await asyncio.to_thread(self.engine.evaluate, None, dry_run=False, use_cache=True)
            except Exception as e:
                evaluated = {"error": f"{type(e).__name__}: {e}"}
        else:
            evaluated = {"skipped": "disarmed"}
        self.last_result = {"managed": managed, "evaluate": evaluated}

    async def _loop(self):
        while not self._stop.is_set():
            try:
                leader = await asyncio.to_thread(db.lease_acquire, LEASE_KEY, self.owner, LEASE_TTL_SEC)
                self.is_leader = bool(leader)
                self.armed = db.get_setting(ARMED_KEY) == "1"
                if self.is_leader:
                    await self.tick_once()
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_sec)
            except asyncio.TimeoutError:
                pass


# Module-level singleton — main.py starts/stops it; the API router reads the
# same instance so /arm, /disarm and /scheduler agree with what's actually
# ticking. Profile from SMART_SCALPER_PROFILE (default BALANCED, see profiles.py).
SCHEDULER = SmartScalperScheduler()
if os.environ.get("SMART_SCALPER_AUTO_ARM") in ("1", "true", "yes"):
    SCHEDULER.arm()
