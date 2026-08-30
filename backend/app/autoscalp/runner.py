"""
AutoScalpRunner — the autonomous PAPER scalping loop (spec-13/15/17).

  live WS feed -> per-instrument CandleAggregator -> P1-P5 decision engine
              -> spec-15 safeguards -> PAPER trade open (paper_trading)
              -> per-tick monitor / exit -> outcome logged to scalp_signals
              -> canonical live_market_snapshots persisted every cycle
              -> periodic recalibration from resolved LIVE outcomes

Only the ONE lease holder runs the loop. Disarmed by default. LIVE order
routing is never reached from here.
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
from datetime import datetime, timezone

from .. import db
from .aggregator import CandleAggregator
from .safeguards import Safeguards
from ..engines.scalp_strategy import decide_from_context
from ..engines.paper_trading import open_trade, update_trade_price, close_trade
from ..backtest import calibration as _cal
from ..backtest.replay import _mod, _tod_bucket
from . import notify

LEASE_KEY = "autoscalp_lease"
LEASE_TTL = 30
ARMED_KEY = "autoscalp_armed"
CALIB_KEY = "autoscalp_calibration"
CONFIG_KEY = "autoscalp_config"

DEFAULT_CONFIG = {
    "symbols": ["NIFTY"],
    "decide_every_sec": 30,
    "poll_sec": 2,
    "strike_window": 2,
    "recalibrate_every_sec": 900,
    "min_recalibrate_samples": 40,
    "strategy": {},                 # forwarded to decide_from_context config
    "safeguards": {},
    "auto_arm": False,
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


class AutoScalpRunner:
    def __init__(self, *, feed=None, chain_provider=None, broadcast=None,
                 telegram_fn=None, now_fn=time.time, owner="autoscalp"):
        self.feed = feed                      # AngelMarketFeed-like: get_ltp(token), status()
        self.chain_provider = chain_provider  # (symbol, atm, window) -> canonical chain list
        self._broadcast = broadcast
        self._telegram = telegram_fn
        self._now = now_fn
        self.owner = owner
        self.is_leader = False
        self.armed = False
        self.last_tick_ts = None
        self.last_error = None
        self._task = None
        self._stop = asyncio.Event()
        self._aggs: dict[str, CandleAggregator] = {}     # "SYMBOL" -> index aggregator
        self._opt_aggs: dict[str, CandleAggregator] = {}  # token -> option aggregator
        self._sub_tokens: dict[str, dict] = {}
        self._last_decide: dict[str, float] = {}
        self._last_recal = 0.0
        self.safeguards = Safeguards()
        self._sg_cfg_sig = None

    # ---------------- config ----------------
    def get_config(self) -> dict:
        raw = db.get_setting(CONFIG_KEY)
        stored = {}
        if raw:
            try:
                stored = json.loads(raw)
            except Exception:
                stored = {}
        return {**DEFAULT_CONFIG, **stored}

    def set_config(self, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ValueError("config must be an object")
        unknown = set(patch) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(f"unknown config field(s): {', '.join(sorted(unknown))}")
        cfg = {**self.get_config(), **patch}
        db.set_setting(CONFIG_KEY, json.dumps(cfg))
        return cfg

    def calibration(self) -> dict | None:
        raw = db.get_setting(CALIB_KEY)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except Exception:
            return None

    # ---------------- lifecycle ----------------
    def start(self):
        db.init_db()
        if self.get_config().get("auto_arm"):
            db.set_setting(ARMED_KEY, "1")
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        self._stop.set()
        if self.is_leader:
            db.lease_release(LEASE_KEY, self.owner)
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                pass

    def arm(self):
        db.set_setting(ARMED_KEY, "1")
        self.armed = True

    def disarm(self):
        db.set_setting(ARMED_KEY, "0")
        self.armed = False

    # ---------------- feed wiring ----------------
    def _refresh_subscription(self, cfg):
        if not self.feed:
            return
        want = []
        from ..connectors.angel_ws import EXCHANGE_TYPE
        from .. import instruments
        for sym in cfg["symbols"]:
            meta = instruments.resolve(sym)
            if meta and meta.get("symboltoken"):
                want.append({"token": str(meta["symboltoken"]),
                             "exchange_type": EXCHANGE_TYPE.get(str(meta.get("exchange") or "NSE").upper(), 1)})
                self._aggs.setdefault(sym.upper(), CandleAggregator())
        for tok, meta in list(self._sub_tokens.items()):
            want.append({"token": tok, "exchange_type": meta.get("exchange_type", 2)})
        try:
            self.feed.subscribe(want)
        except Exception as e:
            self.last_error = f"subscribe: {type(e).__name__}: {e}"

    def _ensure_option_subs(self, chain):
        """Subscribe the ATM-band option tokens so their premium candles build."""
        if not self.feed:
            return
        for row in chain or []:
            for ot in ("ce", "pe"):
                leg = row.get(ot) or {}
                tok = leg.get("token")
                if tok and str(tok) not in self._sub_tokens:
                    self._sub_tokens[str(tok)] = {"exchange_type": 2}
                    self._opt_aggs.setdefault(str(tok), CandleAggregator())

    def _pump_feed(self):
        """Copy fresh WS marks into the aggregators."""
        if not self.feed:
            return
        now = self._now()
        for sym, agg in self._aggs.items():
            from .. import instruments
            meta = instruments.resolve(sym)
            tok = meta and meta.get("symboltoken")
            ltp = self.feed.get_ltp(str(tok)) if tok else None
            if ltp is not None:
                agg.add_tick(now, float(ltp))
        for tok, agg in self._opt_aggs.items():
            ltp = self.feed.get_ltp(tok)
            if ltp is not None:
                agg.add_tick(now, float(ltp))

    # ---------------- decision + trade ----------------
    def _open_positions(self):
        return [t for t in db.list_trades(status="OPEN", limit=100, strategy="AUTOSCALP")]

    def _leg_bars_fn(self, chain):
        tok_by = {}
        for row in chain or []:
            for ot in ("CE", "PE"):
                leg = row.get(ot.lower()) or {}
                if leg.get("token"):
                    tok_by[(row["strike"], ot)] = str(leg["token"])

        def fn(strike, ot):
            tok = tok_by.get((strike, str(ot).upper()))
            agg = self._opt_aggs.get(tok) if tok else None
            if not agg:
                return None
            snap = agg.snapshot(now_epoch=self._now())
            return snap if len(snap.get("5m") or []) >= 20 else None
        return fn

    async def _evaluate(self, sym, cfg):
        agg = self._aggs.get(sym.upper())
        if not agg or agg.last_price is None:
            return
        bars = agg.snapshot(now_epoch=self._now())
        if len(bars.get("5m") or []) < 20:
            return
        atm = round(agg.last_price / 50.0) * 50
        chain = []
        if self.chain_provider:
            try:
                chain = self.chain_provider(sym, atm, cfg["strike_window"]) or []
            except Exception as e:
                self.last_error = f"chain: {type(e).__name__}: {e}"
        self._ensure_option_subs(chain)

        feed_st = self.feed.status() if self.feed else {}
        feed_age = feed_st.get("last_msg_age_sec")
        tod = _tod_bucket(_mod(datetime.now(timezone.utc).astimezone().isoformat()))

        sig = decide_from_context(
            bars, chain, atm=atm, calib=self.calibration(),
            avg_win=None, avg_loss=None, leg_bars_fn=self._leg_bars_fn(chain),
            tod_bucket=tod, config=cfg.get("strategy") or {})

        self._persist_snapshot(sym, agg, atm, chain, sig, feed_age)
        await self._emit("autoscalp_signal", {"symbol": sym, **{k: sig.get(k) for k in
                         ("decision", "signal_type", "direction", "probability", "confidence", "reason")}})
        if sig.get("false_risk") == "LIKELY_FALSE" or sig.get("filtered"):
            notify.push(self._telegram, notify.lifecycle("FALSE_BREAKOUT",
                        {"underlying": sym, "opt_type": "", "strike": 0},
                        note=f"{sig.get('signal_type','')} {sig.get('reason','')}"))

        if sig.get("decision") not in ("BUY_CE", "BUY_PE"):
            return

        opens = self._open_positions()
        open_keys = {(t.get("underlying"), t.get("option_type")) for t in opens}
        allow, why = self.safeguards.check_entry(
            open_count=len(opens),
            feed_connected=bool(feed_st.get("connected", True)),
            feed_age_sec=feed_age, underlying=sym.upper(),
            side=sig["decision"].split("_")[1], open_keys=open_keys,
            option_premium=sig.get("entry"))
        if not allow:
            await self._emit("autoscalp_blocked", {"symbol": sym, "reason": why, "signal": sig["decision"]})
            return

        self._open_paper(sym, sig)

    def _open_paper(self, sym, sig):
        signal_id = "ASC-" + format(int(time.time() * 1000), "x")
        qty = 1
        row = open_trade({
            "signal_id": signal_id, "market": "NSE", "underlying": sym.upper(),
            "instrument": "OPTION", "expiry": sig.get("expiry") or "",
            "strike": sig.get("strike") or 0, "option_type": sig["decision"].split("_")[1],
            "direction": "BUY", "timeframe": "5m", "entry": sig["entry"],
            "target_1": sig["target_1"], "target_2": sig.get("target_2"),
            "stop_loss": sig["stop_loss"], "trailing_stop": sig.get("trailing_stop") or 0,
            "quantity": qty, "probability": sig.get("probability"),
            "confidence": None, "market_regime": sig.get("regime"),
            "oi_evidence": "", "reason": sig.get("reason", "autoscalp"),
            "strategy": "AUTOSCALP", "setup": sig.get("signal_type"),
            "atr_pct": None, "max_hold_sec": sig.get("max_hold_sec"),
            "symboltoken": str(sig.get("token") or ""),
        })
        db.insert_scalp_signal({
            "signal_id": signal_id, "source": "LIVE",
            "provenance": json.dumps({"runner": "autoscalp", "owner": self.owner}),
            "created_ts": _now_iso(), "session_date": _now_iso()[:10],
            "tod_bucket": _tod_bucket(_mod(datetime.now(timezone.utc).astimezone().isoformat())),
            "symbol": sym.upper(), "index_ltp": self._aggs[sym.upper()].last_price,
            "regime": sig.get("regime"), "signal_type": sig.get("signal_type"),
            "direction": sig.get("direction"), "mtf_alignment": sig.get("mtf_alignment"),
            "component_scores": json.dumps(sig.get("component_scores") or {}),
            "signal_score": sig.get("signal_score"), "probability": sig.get("probability"),
            "confidence": sig.get("confidence"), "ev": sig.get("ev"), "rr": sig.get("rr"),
            "decision": sig["decision"], "reason": sig.get("reason"),
            "support": sig.get("support"), "resistance": sig.get("resistance"),
            "support_strength": sig.get("support_strength"),
            "resistance_strength": sig.get("resistance_strength"),
            "sr_level": sig.get("sr_level"), "sr_side": sig.get("sr_side"),
            "opt_underlying": sym.upper(), "opt_strike": sig.get("strike"),
            "opt_expiry": sig.get("expiry"), "opt_type": sig["decision"].split("_")[1],
            "opt_token": sig.get("token"), "opt_tradingsymbol": sig.get("tradingsymbol"),
            "entry": sig["entry"], "stop_loss": sig["stop_loss"],
            "target_1": sig["target_1"], "target_2": sig.get("target_2"),
            "trailing_stop": sig.get("trailing_stop") or 0,
            "max_hold_sec": sig.get("max_hold_sec"), "entry_ts": _now_iso(),
            "status": "OPEN", "resolved": 0,
        })
        asyncio.create_task(self._emit("autoscalp_open", {"symbol": sym, "trade": row, "signal_id": signal_id}))
        notify.push(self._telegram, notify.signal_card(
            {**sig, "opt_tradingsymbol": sig.get("tradingsymbol")}, symbol=sym,
            index_ltp=self._aggs[sym.upper()].last_price))

    def _monitor(self):
        for t in self._open_positions():
            tok = str(t.get("symboltoken") or "")
            ltp = self.feed.get_ltp(tok) if (self.feed and tok) else None
            if ltp is None:
                continue
            updated = update_trade_price(t["trade_id"], float(ltp))
            if updated and updated.get("status") == "CLOSED":
                pnl = updated.get("pnl")
                self.safeguards.on_trade_closed(pnl)
                db.update_scalp_signal(updated.get("signal_id") or "", {
                    "status": "CLOSED", "exit_price": updated.get("exit_price"),
                    "exit_ts": _now_iso(), "exit_reason": updated.get("exit_reason"),
                    "points": (updated.get("exit_price") or 0) - (updated.get("entry") or 0),
                    "outcome": updated.get("result"), "resolved": 1,
                    "holding_sec": None, "mfe": updated.get("mfe"), "mae": updated.get("mae")})
                asyncio.create_task(self._emit("autoscalp_close", {"trade": updated}))
                notify.push(self._telegram, notify.lifecycle(
                    updated.get("exit_reason") or "EXIT", updated,
                    note=f"held; MFE {updated.get('mfe')} MAE {updated.get('mae')}"))

    # ---------------- persistence + calibration ----------------
    def _persist_snapshot(self, sym, agg, atm, chain, sig, feed_age):
        try:
            db.insert_live_snapshot({
                "ts": _now_iso(), "session_date": _now_iso()[:10], "symbol": sym.upper(),
                "source": "LIVE", "provenance": json.dumps({"feed": "angel_ws", "owner": self.owner}),
                "index_ltp": agg.last_price, "atm": atm,
                "vwap": sig.get("vwap"), "atr": sig.get("atr"),
                "regime": sig.get("regime"), "mtf_alignment": sig.get("mtf_alignment"),
                "support": sig.get("support"), "resistance": sig.get("resistance"),
                "support_strength": sig.get("support_strength"),
                "resistance_strength": sig.get("resistance_strength"),
                "signal_type": sig.get("signal_type"), "direction": sig.get("direction"),
                "signal_score": sig.get("signal_score"), "probability": sig.get("probability"),
                "confidence": sig.get("confidence"), "decision": sig.get("decision"),
                "reason": sig.get("reason"), "feed_age_sec": feed_age,
                "chain_json": json.dumps(chain, default=str)[:20000],
            })
        except Exception as e:
            self.last_error = f"persist: {type(e).__name__}: {e}"

    def _maybe_recalibrate(self, cfg):
        now = self._now()
        if now - self._last_recal < int(cfg.get("recalibrate_every_sec", 900)):
            return
        self._last_recal = now
        rows = db.list_scalp_signals(source="LIVE", status="CLOSED", limit=2000)
        samples = [{"score": r.get("signal_score"), "regime": r.get("regime"),
                    "signal_type": r.get("signal_type"), "win": r.get("outcome") == "WIN"}
                   for r in rows if r.get("signal_score") is not None and r.get("outcome") in ("WIN", "LOSS")]
        if len(samples) < int(cfg.get("min_recalibrate_samples", 40)):
            return
        calib = _cal.fit(samples, version=f"live-{_now_iso()[:10]}-n{len(samples)}")
        db.set_setting(CALIB_KEY, json.dumps(calib))
        asyncio.create_task(self._emit("autoscalp_recalibrated",
                                       {"samples": len(samples), "version": calib["version"]}))

    # ---------------- emit / telegram ----------------
    async def _emit(self, kind, data):
        if self._broadcast:
            try:
                await self._broadcast({"type": kind, "data": data})
            except Exception:
                pass

    def _tg(self, text):
        if not self._telegram:
            return
        try:
            self._telegram(text)
        except Exception:
            pass

    # ---------------- status ----------------
    def status(self) -> dict:
        cfg = self.get_config()
        return {
            "armed": (db.get_setting(ARMED_KEY) == "1") or bool(cfg.get("auto_arm")),
            "running": bool(self._task and not self._task.done()),
            "is_leader": self.is_leader, "owner": self.owner,
            "last_tick_ts": self.last_tick_ts, "last_error": self.last_error,
            "symbols": cfg["symbols"], "decide_every_sec": cfg["decide_every_sec"],
            "open_positions": len(self._open_positions()),
            "calibration": (self.calibration() or {}).get("version"),
            "feed": self.feed.status() if self.feed else None,
            "safeguards": self.safeguards.status(),
            "live_trading": False, "paper_mode": True,
            "config": cfg,
        }

    # ---------------- main loop ----------------
    async def tick_once(self):
        """One iteration (also directly callable from tests)."""
        cfg = self.get_config()
        self.last_tick_ts = _now_iso()
        sg_sig = json.dumps(cfg.get("safeguards") or {}, sort_keys=True)
        if sg_sig != self._sg_cfg_sig:
            self.safeguards = Safeguards(cfg.get("safeguards") or {})
            self._sg_cfg_sig = sg_sig
        self._refresh_subscription(cfg)
        self._pump_feed()
        self._monitor()
        if not self.armed:
            return
        every = max(2, int(cfg.get("decide_every_sec", 30)))
        now = self._now()
        for sym in cfg["symbols"]:
            if now - self._last_decide.get(sym, 0) < every:
                continue
            self._last_decide[sym] = now
            try:
                await self._evaluate(sym, cfg)
            except Exception as e:
                self.last_error = f"evaluate {sym}: {type(e).__name__}: {e}"
                traceback.print_exc()
        self._maybe_recalibrate(cfg)

    async def _loop(self):
        while not self._stop.is_set():
            cfg = self.get_config()
            leader = await asyncio.to_thread(db.lease_acquire, LEASE_KEY, self.owner, LEASE_TTL)
            self.is_leader = bool(leader)
            self.armed = (db.get_setting(ARMED_KEY) == "1") or bool(cfg.get("auto_arm"))
            if self.is_leader:
                try:
                    await self.tick_once()
                except Exception as e:
                    self.last_error = f"{type(e).__name__}: {e}"
                    traceback.print_exc()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=max(1, int(cfg.get("poll_sec", 2))))
            except asyncio.TimeoutError:
                pass
