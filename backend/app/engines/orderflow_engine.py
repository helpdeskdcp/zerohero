"""
Orderflow engine -- consumes raw FULL packets (from
app.connectors.dhan_ws.DhanOrderflowWorker's queue), classifies each into a
BUY_AGGRESSOR / SELL_AGGRESSOR / UNKNOWN trade tick
(app.connectors.dhan_parser.classify_tick), and rolls them into per-symbol
Footprint bars: delta (buy volume - sell volume), session Cumulative Volume
Delta (CVD), and stacked imbalances (3+ consecutive occupied price levels
within one bar all clearing a 3:1 buy/sell ratio in the same direction).

Trade-side classification is INFERRED (quote rule + tick-rule fallback), not
broker-confirmed -- see dhan_parser.py's module docstring. Every bar keeps a
classification_methods breakdown so nothing downstream mistakes this for
ground truth.

RESEARCH/OBSERVABILITY ONLY: this module computes bars in memory and exposes
them via .snapshot()/.status(); it emits no BUY/SELL/order/signal and is not
wired to app/execution/ (LIVE order path) or any scalp/autoscalp decision.
Optionally persists every classified tick via app.engines.orderflow_store
so the real (quote-rule) signal can eventually be backtested -- historical
OHLCV data has no bid/ask depth, so that backtest is only possible against
ticks captured going forward (see the [[nifty-tick-rule-cvd-nogo]] research
memory for what had to be tested instead, and why).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone

from ..connectors import dhan_parser

_log = logging.getLogger("orderflow_engine")

STACK_MIN_RUN = 3
STACK_RATIO = 3.0
STACK_MIN_LEVEL_VOLUME = 10       # a level needs this much classified volume (buy+sell) to be judged at all; INITIAL, UNCALIBRATED
BAR_SECONDS = (60, 300)
_IST_SEC = 19800


class FootprintBar:
    __slots__ = ("bar_start", "bar_sec", "levels", "open", "high", "low", "close",
                "buy_vol", "sell_vol", "method_counts", "tick_size")

    def __init__(self, bar_start: int, bar_sec: int, price: float, tick_size: float | None = None):
        self.tick_size = tick_size                 # None => price adjacency cannot be verified (reported in the output)
        self.bar_start = bar_start
        self.bar_sec = bar_sec
        self.levels: dict[float, dict] = {}   # price -> {"buy": int, "sell": int}
        self.open = self.high = self.low = self.close = price
        self.buy_vol = 0
        self.sell_vol = 0
        self.method_counts: dict[str, int] = defaultdict(int)

    def add(self, tick: dict):
        price, qty, side = tick["ltp"], max(int(tick["ltq"]), 0), tick["side"]
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.method_counts[tick["classification_method"]] += 1
        lvl = self.levels.setdefault(price, {"buy": 0, "sell": 0})
        if side == "BUY_AGGRESSOR":
            lvl["buy"] += qty
            self.buy_vol += qty
        elif side == "SELL_AGGRESSOR":
            lvl["sell"] += qty
            self.sell_vol += qty
        # UNKNOWN-side ticks still move OHLC (they were real trades) but are
        # deliberately excluded from delta/imbalance -- we don't know which
        # side they belong to, and guessing would corrupt the count.

    @property
    def delta(self) -> int:
        return self.buy_vol - self.sell_vol

    def stacked_imbalances(self) -> list[dict]:
        """Runs of >= STACK_MIN_RUN consecutive occupied price levels (in
        traded-price order within this bar) that each individually clear
        STACK_RATIO:1 buy/sell, all in the same direction."""
        prices = sorted(self.levels)
        directions = []
        for p in prices:
            b, s = self.levels[p]["buy"], self.levels[p]["sell"]
            if b + s < STACK_MIN_LEVEL_VOLUME:       # too thin to call an imbalance (1-lot vs 0 is not 'infinite' imbalance)
                directions.append(None)
            elif b > 0 and (s == 0 or b / s >= STACK_RATIO):
                directions.append("BUY")
            elif s > 0 and (b == 0 or s / b >= STACK_RATIO):
                directions.append("SELL")
            else:
                directions.append(None)
        runs = []
        i = 0
        while i < len(directions):
            d = directions[i]
            if d is None:
                i += 1
                continue
            j = i
            while j < len(directions) and directions[j] == d and (
                    j == i or self.tick_size is None or abs(prices[j] - prices[j - 1] - self.tick_size) <= self.tick_size * 1e-6):
                j += 1                                # with a known tick size, levels must be ADJACENT, not merely the next occupied ones
            if j - i >= STACK_MIN_RUN:
                runs.append({"direction": d, "price_from": prices[i], "price_to": prices[j - 1],
                            "levels": j - i, "adjacency_verified": self.tick_size is not None})
            i = j
        return runs

    def to_dict(self) -> dict:
        return {
            "bar_start": self.bar_start, "bar_sec": self.bar_sec,
            "open": self.open, "high": self.high, "low": self.low, "close": self.close,
            "buy_vol": self.buy_vol, "sell_vol": self.sell_vol, "delta": self.delta,
            "delta_pct": (round(self.delta / (self.buy_vol + self.sell_vol), 4) if (self.buy_vol + self.sell_vol) else None),
            "classified_vol": self.buy_vol + self.sell_vol,
            "n_levels": len(self.levels), "stacked_imbalances": self.stacked_imbalances(),
            "classification_methods": dict(self.method_counts),
        }


class SymbolState:
    def __init__(self, max_closed_bars: int = 500):
        self.prev: dict | None = None          # dhan_parser.classify_tick()'s ltt/ltp de-dupe state
        self.cvd = 0
        self.cvd_session_date: int | None = None   # IST day number the running CVD belongs to (resets each session)
        self.cvd_resets = 0
        self.late_ticks = 0                        # ticks older than the open bar: counted in CVD, kept out of bars
        self.bars: dict[int, dict[int, FootprintBar]] = {sec: {} for sec in BAR_SECONDS}
        self.closed_bars: dict[int, list[dict]] = {sec: [] for sec in BAR_SECONDS}
        self.max_closed_bars = max_closed_bars

    def ingest(self, packet: dict) -> dict | None:
        tick, self.prev = dhan_parser.classify_tick(packet, self.prev)
        if tick is None:
            return None
        day = (tick["ltt"] + _IST_SEC) // 86400
        if self.cvd_session_date is not None and day != self.cvd_session_date:
            self.cvd, self.cvd_resets = 0, self.cvd_resets + 1       # CVD is a SESSION quantity: never carried across days
        self.cvd_session_date = day
        if tick["side"] == "BUY_AGGRESSOR":
            self.cvd += tick["ltq"]
        elif tick["side"] == "SELL_AGGRESSOR":
            self.cvd -= tick["ltq"]
        tick["cvd"] = self.cvd
        counted_late = False
        for bar_sec in BAR_SECONDS:
            bar_start = (tick["ltt"] // bar_sec) * bar_sec
            bars = self.bars[bar_sec]
            if bars and bar_start < max(bars):          # out-of-order tick for an already-superseded bar: do not reopen/close bars
                if not counted_late:
                    self.late_ticks, counted_late = self.late_ticks + 1, True
                continue
            for existing_start in [s for s in bars if s != bar_start]:
                self.closed_bars[bar_sec].append(bars.pop(existing_start).to_dict())
            if bar_start not in bars:
                bars[bar_start] = FootprintBar(bar_start, bar_sec, tick["ltp"])
            bars[bar_start].add(tick)
            if len(self.closed_bars[bar_sec]) > self.max_closed_bars:
                self.closed_bars[bar_sec] = self.closed_bars[bar_sec][-self.max_closed_bars:]
        return tick


_FLUSH_EVERY_TICKS = 50
_FLUSH_EVERY_SEC = 5.0


class OrderflowEngine:
    """Consumes app.connectors.dhan_ws's queue. .start()/.stop() mirror the
    rest of this codebase's background-worker shape.

    tick_store (app.engines.orderflow_store.TickStore), when given, gets
    every classified tick batched (by count or time, whichever first) and
    flushed via a thread -- persistence must never block the consumer loop.
    meta_provider, when given, is called once per flush to look up each
    tracked security_id's {"symbol", "kind"} for the persisted rows (e.g.
    dhan_ws.DhanOrderflowWorker's own resolved instrument_meta -- avoids
    keeping a second, potentially-stale copy in the engine itself)."""

    def __init__(self, queue: asyncio.Queue, tick_store=None, meta_provider=None):
        self.queue = queue
        self._symbols: dict[int, SymbolState] = {}
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self.n_ticks_classified = 0
        self.n_ticks_unknown = 0
        self.tick_store = tick_store
        self._meta_provider = meta_provider or (lambda: {})
        self._pending: list[dict] = []
        # NOT 0.0 -- time.time() - 0.0 is always >= _FLUSH_EVERY_SEC (any real
        # epoch is already huge), which would force an immediate flush on the
        # very first tick regardless of the count threshold, defeating batching.
        self._last_flush = time.time()
        self.n_ticks_persisted = 0

    def start(self):
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=6)
            except Exception:
                pass

    async def _run(self):
        while not self._stop.is_set():
            try:
                packet = await asyncio.wait_for(self.queue.get(), timeout=1)
            except asyncio.TimeoutError:
                await self._maybe_flush()
                continue
            try:
                self.ingest(packet)
            except Exception as e:
                _log.warning("orderflow_engine: ingest failed: %s: %s", type(e).__name__, e)
            await self._maybe_flush()
        await self._flush()  # drain whatever's left on a clean stop

    def ingest(self, packet: dict) -> dict | None:
        sid = packet["security_id"]
        state = self._symbols.setdefault(sid, SymbolState())
        tick = state.ingest(packet)
        if tick is None:
            return None
        self.n_ticks_classified += 1
        if tick["side"] == "UNKNOWN":
            self.n_ticks_unknown += 1
        if self.tick_store is not None:
            metas = self._meta_provider()
            meta = metas.get(sid) or metas.get(str(sid)) or {}      # instrument_meta is keyed by STRING security_id; packet sid is int
            self._pending.append({
                "received_ts": datetime.now(timezone.utc).isoformat(),
                "security_id": sid, "symbol": meta.get("symbol"), "kind": meta.get("kind"),
                "ltp": tick["ltp"], "ltq": tick["ltq"], "ltt": tick["ltt"],
                "side": tick["side"], "classification_method": tick["classification_method"],
                "best_bid": tick["best_bid"], "best_ask": tick["best_ask"], "cvd": tick["cvd"],
            })
        return tick

    async def _maybe_flush(self):
        if self.tick_store is None or not self._pending:
            return
        if len(self._pending) >= _FLUSH_EVERY_TICKS or (time.time() - self._last_flush) >= _FLUSH_EVERY_SEC:
            await self._flush()

    async def _flush(self):
        if self.tick_store is None or not self._pending:
            return
        batch, self._pending = self._pending, []
        self._last_flush = time.time()
        try:
            n = await asyncio.to_thread(self.tick_store.insert_many, batch)
            self.n_ticks_persisted += n
        except Exception as e:
            _log.warning("orderflow_engine: tick persistence failed: %s: %s", type(e).__name__, e)

    def snapshot(self, security_id: int) -> dict | None:
        state = self._symbols.get(security_id)
        if state is None:
            return None
        return {
            "security_id": security_id, "cvd": state.cvd, "cvd_session_ist_day": state.cvd_session_date,
            "cvd_resets": state.cvd_resets, "late_ticks": state.late_ticks,
            "open_bars": {sec: [b.to_dict() for b in bars.values()] for sec, bars in state.bars.items()},
            "closed_bars": state.closed_bars,
        }

    def status(self) -> dict:
        return {"symbols_tracked": len(self._symbols), "ticks_classified": self.n_ticks_classified,
                "ticks_unknown": self.n_ticks_unknown, "queue_depth": self.queue.qsize(),
                "ticks_persisted": self.n_ticks_persisted, "ticks_pending_flush": len(self._pending)}
