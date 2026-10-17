"""Shadow runner: SIGNAL-ONLY, PAPER-ONLY, no orders. Reads the Dhan classified ticks (read-only) -> the SAME InstrumentEvaluator the replay uses ->
persists every closed 5m bar and its score in its OWN sqlite file (never chanakya.db, so no contention with the trading DB lock).

Forward outcomes of SIGNAL bars (underlying bps, entry next-bar open, exit +3/+6 bars close) accumulate and are judged by the net-of-cost promotion
gate. Nothing here can place or even prepare an order; promotion only ever unlocks the NEXT paper stage (option lifecycle), which is not wired."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone

from ..strategy_lab import gate as G
from .bar_eval import BAR, IST, InstrumentEvaluator
from .policy import load_policy

_log = logging.getLogger("hybrid_orderflow.shadow")
_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
TICK_DB = os.path.join(_ROOT, "data", "dhan_orderflow_ticks.db")
SHADOW_DB = os.path.join(_ROOT, "data", "hybrid_orderflow_shadow.db")
HORIZONS = (3, 6)
REBUILD_DAYS = 4                                          # calendar days of ticks replayed on (re)start (covers a weekend + the previous session)
PRIMARY_H = 6                                             # pre-registered horizon the gate judges
COST_BPS = {"index": 2.0, "mcx": 3.0}


def enabled() -> bool:
    return os.environ.get("HYBRID_ORDERFLOW_SHADOW", "1").strip().lower() not in ("0", "false", "off", "no")


class ShadowStore:
    def __init__(self, path: str = SHADOW_DB):
        self.path = path
        with self._c() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS bars(sym TEXT NOT NULL, k INTEGER NOT NULL, o REAL, h REAL, l REAL, c REAL, vol INTEGER,
                    PRIMARY KEY(sym,k));
                CREATE TABLE IF NOT EXISTS scores(sym TEXT NOT NULL, k INTEGER NOT NULL, status TEXT, dir TEXT, total REAL, reasons TEXT, comps TEXT,
                    policy_version TEXT, PRIMARY KEY(sym,k));
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
            """)

    def _c(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        return c

    def save(self, results: list, policy_version: str):
        if not results:
            return
        with self._c() as c:
            c.executemany("INSERT OR REPLACE INTO bars VALUES(?,?,?,?,?,?,?)",
                          [(r["sym"], r["k"], r["bar"]["o"], r["bar"]["h"], r["bar"]["l"], r["bar"]["c"], r["bar"]["vol"]) for r in results])
            c.executemany("INSERT OR REPLACE INTO scores VALUES(?,?,?,?,?,?,?,?)",
                          [(r["sym"], r["k"], r["status"], r["dir"], r["total"], json.dumps(r["reasons"]),
                            json.dumps({x["component"]: x["points"] for x in r["components"]}), policy_version) for r in results if r["scored"]])

    def set_meta(self, key: str, value) -> None:
        with self._c() as c:
            c.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, json.dumps(value)))

    def get_meta(self, key: str, default=None):
        with self._c() as c:
            r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def counts(self) -> dict:
        with self._c() as c:
            st = dict(c.execute("SELECT status, COUNT(*) FROM scores GROUP BY status").fetchall())
            last = dict(c.execute("SELECT sym, MAX(k) FROM bars GROUP BY sym").fetchall())
            days = c.execute("SELECT COUNT(DISTINCT k*300/86400) FROM scores").fetchone()[0]
        return {"scored_by_status": st, "last_bar_k_by_symbol": last, "scored_days": days}

    def forward_trades(self, h: int) -> list:
        """[(sym, k, direction, gross_bps)] for SIGNAL bars with an exact +1 and +h bar in the same session (no forward-fill)."""
        out = []
        with self._c() as c:
            bars = {(s, k): (o, cl) for s, k, o, cl in c.execute("SELECT sym,k,o,c FROM bars")}
            for sym, k, d in c.execute("SELECT sym,k,dir FROM scores WHERE status='SIGNAL' ORDER BY k,sym"):
                a, b = bars.get((sym, k + 1)), bars.get((sym, k + h))
                if a and b and (k + h) // (86400 // BAR) == k // (86400 // BAR):
                    x = (b[1] / a[0] - 1) * 1e4
                    out.append((sym, k, d, x if d == "BULLISH" else -x))
        return out


def forward_report(store: ShadowStore, policy: dict) -> dict:
    """Interim numbers + the gate verdict on the PRIMARY horizon. INSUFFICIENT_SAMPLE is the expected answer for months."""
    rep = {"primary_horizon_bars": PRIMARY_H, "horizons": {}}
    mcx = set(policy["markets"]["mcx"])
    for h in HORIZONS:
        tr = store.forward_trades(h)
        g = [t[3] for t in tr]
        rep["horizons"][h] = {"n": len(g), "gross_mean_bps": (sum(g) / len(g)) if g else None, "hit": (sum(x > 0 for x in g) / len(g)) if g else None}
    tr = store.forward_trades(PRIMARY_H)
    if tr:
        # one cost per trade: gate takes a scalar cost, so judge index and MCX trades together at the MORE conservative (MCX) cost
        res = G.evaluate([t[3] for t in tr], [t[1] * BAR // 86400 for t in tr], max(COST_BPS.values()) if any(t[0] in mcx for t in tr) else COST_BPS["index"],
                         policy["promotion"])
    else:
        res = G.evaluate([], [], 0.0, policy["promotion"])
    rep["gate"] = {k: res.get(k) for k in ("verdict", "n", "distinct_days", "net_mean_bps", "net_ci95_bps", "failed")}
    rep["paper_stage_unlocked"] = res["verdict"] == G.PROMOTE
    rep["next_stage_if_unlocked"] = "option lifecycle paper (not wired)"
    return rep


class ShadowRunner:
    def __init__(self, meta_provider, tick_db: str = TICK_DB, store: ShadowStore | None = None, interval: float = 20.0, policy: dict | None = None):
        self.policy = policy or load_policy()                  # fail closed: PolicyError prevents the runner from existing
        self.meta_provider, self.tick_db, self.interval = meta_provider, tick_db, interval
        self.store = store or ShadowStore()
        self.ev: dict[int, InstrumentEvaluator] = {}
        self.cursor: dict[int, int] = {}
        self.last_error: str | None = None
        self.last_run_ts: float | None = None
        self.ticks_fed = 0
        self.bars_closed = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    # ---------------------------------------------------------------- one cycle (sync; run in a thread)
    def instruments(self) -> dict[int, tuple[str, float | None]]:
        ok = set(self.policy["markets"]["index_derivatives"]) | set(self.policy["markets"]["mcx"])
        out = {}
        for sid, m in (self.meta_provider() or {}).items():
            if m.get("kind") == "FUTURE" and m.get("symbol") in ok:
                out[int(sid)] = (m["symbol"], m.get("tick_size"))
        return out

    def run_once(self, now: float | None = None) -> dict:
        now = now or time.time()
        fed = closed = 0
        try:
            inst = self.instruments()
            if not inst:
                self.last_error = "no tracked futures in instrument meta yet"
                return {"fed": 0, "closed": 0}
            day_start = ((int(now) + IST) // 86400) * 86400 - IST
            conn = sqlite3.connect(f"file:{self.tick_db}?mode=ro", uri=True, timeout=5)
            try:
                for sid, (sym, tick) in inst.items():
                    ev = self.ev.get(sid)
                    results = []
                    if ev is None:                             # first sight (start/restart/new instrument): rebuild from the persisted ticks of the last
                                                               # REBUILD_DAYS sessions, so prior-day high/low (PDH/PDL) are known exactly as in the replay
                        ev = self.ev[sid] = InstrumentEvaluator(sym, tick, self.policy)
                        rows = conn.execute("SELECT id,ltt,ltp,ltq,side,classification_method,best_bid,best_ask FROM classified_ticks "
                                            "WHERE security_id=? AND ltt>=? ORDER BY ltt,id", (sid, day_start - REBUILD_DAYS * 86400)).fetchall()
                    else:
                        rows = conn.execute("SELECT id,ltt,ltp,ltq,side,classification_method,best_bid,best_ask FROM classified_ticks "
                                            "WHERE security_id=? AND id>? AND ltt>1500000000 ORDER BY id LIMIT 50000", (sid, self.cursor.get(sid, 0))).fetchall()
                    if ev.tick is None and tick:
                        ev.tick = tick
                    for tid, ltt, ltp, ltq, side, meth, bb, ba in rows:
                        self.cursor[sid] = max(self.cursor.get(sid, 0), tid)
                        if bb and ba and bb >= ba:
                            continue                           # crossed/locked book: never classified, never fed
                        results += ev.add_tick(ltt, ltp, ltq, side, meth)
                        fed += 1
                    results += ev.flush(now)
                    closed += len(results)
                    self.store.save(results, self.policy["policy_version"])
            finally:
                conn.close()
            self.last_error = None
        except Exception as e:                                 # never propagate into the trading process
            self.last_error = f"{type(e).__name__}: {e}"
            _log.warning("shadow run_once failed: %s", self.last_error)
        self.last_run_ts = now
        self.ticks_fed += fed
        self.bars_closed += closed
        return {"fed": fed, "closed": closed}

    # ---------------------------------------------------------------- lifecycle
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
            except Exception:
                pass

    async def _loop(self):
        while not self._stop.is_set():
            await asyncio.to_thread(self.run_once)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
            except asyncio.TimeoutError:
                pass

    def status(self) -> dict:
        try:
            counts = self.store.counts()
            fwd = forward_report(self.store, self.policy)
        except Exception as e:
            counts, fwd = {}, {"error": f"{type(e).__name__}: {e}"}
        return {"mode": "SHADOW_SIGNAL_ONLY", "live_orders": False, "policy_version": self.policy["policy_version"],
                "validation_status": self.policy["validation_status"], "tracked": {int(k): v[0] for k, v in self.instruments().items()},
                "last_run_age_sec": (round(time.time() - self.last_run_ts, 1) if self.last_run_ts else None), "last_error": self.last_error,
                "ticks_fed": self.ticks_fed, "bars_closed": self.bars_closed, **counts, "forward": fwd}
