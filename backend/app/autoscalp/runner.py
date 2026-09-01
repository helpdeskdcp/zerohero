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
from datetime import date, datetime, timedelta, timezone

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

# Per-symbol trading metadata. `strike_step` is the option strike grid (NIFTY
# 50 pts; NATURALGAS ~2.5; CRUDEOIL ~50). `exchange` drives the market-hours
# gate + WS exchange type. MCX underlyings are priced off the front-month
# future, whose token is resolved live (it rolls monthly).
_SYMBOL_META = {
    "NIFTY":      {"exchange": "NSE", "strike_step": 50.0},
    "BANKNIFTY":  {"exchange": "NSE", "strike_step": 100.0},
    "NATURALGAS": {"exchange": "MCX", "strike_step": 2.5},
    "CRUDEOIL":   {"exchange": "MCX", "strike_step": 50.0},
    "CRUDEOILM":  {"exchange": "MCX", "strike_step": 50.0},
}


_META_CACHE: dict[str, dict] = {}


def _sym_meta(sym):
    key = str(sym or "").upper()
    if key in _SYMBOL_META:
        return _SYMBOL_META[key]
    if key in _META_CACHE:
        return _META_CACHE[key]
    meta = {"exchange": "NSE", "strike_step": 50.0}
    # Unknown symbol: infer from the instrument master. An F&O stock (NFO
    # OPTSTK) gets its real strike grid so equity options scalp correctly when
    # the operator adds it to `symbols`.
    try:
        from .. import instruments
        strikes = sorted({round(float(r["strike"]) / 100.0, 4)
                          for r in instruments.master_rows()
                          if r.get("exch_seg") == "NFO" and r.get("instrumenttype") == "OPTSTK"
                          and str(r.get("name") or "").upper() == key and r.get("strike")})
        gaps = sorted(round(b - a, 4) for a, b in zip(strikes, strikes[1:]) if b > a)
        if gaps:
            meta = {"exchange": "NSE", "strike_step": gaps[len(gaps) // 2]}  # modal-ish gap
    except Exception:
        pass
    _META_CACHE[key] = meta
    return meta


_UND_CACHE: dict[str, tuple] = {}          # SYM -> (expiry_epoch, {token, exchange, expiry})


def _underlying_ref(sym):
    """WS-subscribable underlying for `sym`: the NSE index/stock token (static
    registry) or the MCX front-month future token (resolved live, cached 1h --
    it rolls monthly)."""
    key = str(sym or "").upper()
    meta = _sym_meta(key)
    if meta["exchange"] != "MCX":
        from .. import instruments
        r = instruments.resolve(key) or {}
        tok = str(r.get("symboltoken") or "")
        if tok:
            return {"token": tok, "exchange": r.get("exchange") or "NSE", "expiry": None}
        # not an index in the static registry -> resolve it as an NSE equity
        # (F&O stock). Its EQ token is static, so cache it for a day.
        hit = _UND_CACHE.get(key)
        if hit and time.time() < hit[0]:
            return hit[1]
        ref = {"token": "", "exchange": "NSE", "expiry": None}
        try:
            from ..connectors.angelone import _market_sdk
            sdk = _market_sdk(require_auth=False)
            eq = sdk.resolve_equity(key) if sdk else {}
            if eq.get("status") == "OK":
                ref = {"token": str(eq.get("token") or ""), "exchange": "NSE", "expiry": None}
        except Exception:
            pass
        _UND_CACHE[key] = (time.time() + 86400, ref)
        return ref
    hit = _UND_CACHE.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    ref = {"token": "", "exchange": "MCX", "expiry": None}
    try:
        from ..connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        fut = sdk.resolve_future_contract(key, "AUTO") if sdk else {}
        if fut.get("status") == "OK":
            ref = {"token": str(fut.get("token") or ""), "exchange": "MCX", "expiry": fut.get("expiry")}
    except Exception:
        pass
    _UND_CACHE[key] = (time.time() + 3600, ref)
    return ref


DEFAULT_CONFIG = {
    "symbols": ["NIFTY", "NATURALGAS", "CRUDEOIL"],
    "decide_every_sec": 30,
    "poll_sec": 2,
    "strike_window": 2,
    "recalibrate_every_sec": 900,
    "min_recalibrate_samples": 40,
    "strategy": {},                 # base decide_from_context config (all symbols)
    # Per-symbol strategy overrides, merged over `strategy`. NIFTY is DELIBERATELY
    # absent -> it runs on the P6-validated defaults and must stay that way
    # (best live win-rate). MCX commodities move slower and trend longer, so
    # they get more hold time + a slightly stricter EV bar.
    "symbol_profiles": {
        "NATURALGAS": {"max_hold_sec": 1800, "ev": {"min_ev_r": 0.15, "rr_min": 1.4},
                       "est_cost_r": 0.10},        # ~0.1R round-trip on the NG option spread
        "CRUDEOIL":   {"max_hold_sec": 2400, "ev": {"min_ev_r": 0.15, "rr_min": 1.4},
                       "sl_atr": 1.2, "t1_atr": 1.9, "est_cost_r": 0.10},
    },
    "safeguards": {},
    "auto_arm": False,
    # ---- expiry-day (0-DTE) trading ----------------------------------------
    # On an NSE index option's own expiry day the runner TRADES the 0-DTE
    # contract (theta is brutal but gamma pays fast when the read is right).
    #   expiry_day_mode: "trade" (default) | "roll" (skip 0-DTE -> next weekly)
    # `expiry_day_profile` is merged over the symbol's strategy ONLY on its
    # expiry day, so NIFTY's frozen base profile is untouched on every other
    # day. Faster targets + far shorter hold: a 0-DTE scalp that has not paid
    # in a few minutes is decaying, not developing.
    "expiry_day_mode": "trade",
    "expiry_day_profile": {
        "max_hold_sec": 480, "t1_atr": 1.1, "t2_atr": 1.8, "sl_atr": 0.9,
        "ev": {"rr_min": 1.15}, "est_cost_r": 0.12,
    },
    "expiry_day_entry_cutoff": "14:15",   # no fresh 0-DTE entries after this IST time
    # ---- "zero to hero": one tiny far-OTM lottery leg on expiry day --------
    # Only fires on a HIGH-confidence trending signal. Premium IS the risk
    # (capped at stop_frac); books at target_mult or hard time-exit. Kept out
    # of the calibration sample and the scalp safeguard budget on purpose.
    "zth": {
        "enabled": True, "otm_strikes": 4, "max_premium": 12.0,
        "target_mult": 3.0, "stop_frac": 0.5, "hard_exit": "13:30",
        "max_per_day": 1, "min_confidence": "HIGH",
        "require_regime": ["TRENDING_UP", "TRENDING_DOWN", "TRENDING", "STRONG_TREND"],
    },
    "telegram_min_confidence": "HIGH",   # only push TG cards at/above this (LOW|MEDIUM|HIGH)
    "telegram_dedup_sec": 900,           # drop a repeat of the same TG key within this window
}

_CONF_RANK = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)


def _ist_hhmm():
    return _ist_now().strftime("%H:%M")


def _parse_ddmmmyyyy(s):
    """'01SEP2026' -> date(2026, 9, 1); None if not that exact form."""
    s = str(s or "").strip().upper()
    if len(s) == 9 and s[:2].isdigit() and s[5:].isdigit() and s[2:5] in _MONTHS:
        try:
            return date(int(s[5:]), _MONTHS[s[2:5]], int(s[:2]))
        except ValueError:
            return None
    return None


def _secs_until_ist(hhmm, *, floor=60):
    """Seconds from now until today's HH:MM IST. `floor` if that moment has passed."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except (TypeError, ValueError):
        return floor
    now = _ist_now()
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return max(floor, int((target - now).total_seconds()))


def _chain_is_expiry_day(chain):
    """True when the resolved option chain's expiry is today (IST). Best-effort:
    an unparseable / missing expiry -> False."""
    exp = None
    for row in chain or []:
        exp = (row.get("ce") or {}).get("expiry") or (row.get("pe") or {}).get("expiry")
        if exp:
            break
    d = _parse_ddmmmyyyy(exp)
    return d is not None and d == _ist_now().date()


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
        self._tg_last: dict[str, float] = {}   # dedup key -> last-sent epoch
        self._seeded: set[str] = set()         # aggs already backfilled from broker candles
        self._blocks: dict[str, dict] = {}     # sym -> {n, last, ts, signal} — why entries were refused

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
        for sym in cfg["symbols"]:
            ref = _underlying_ref(sym)
            if ref.get("token"):
                want.append({"token": ref["token"],
                             "exchange_type": EXCHANGE_TYPE.get(str(ref.get("exchange") or "NSE").upper(), 1)})
                self._aggs.setdefault(sym.upper(), CandleAggregator())
        for tok, meta in list(self._sub_tokens.items()):
            want.append({"token": tok, "exchange_type": meta.get("exchange_type", 2)})
        try:
            self.feed.subscribe(want, owner="autoscalp")
        except Exception as e:
            self.last_error = f"subscribe: {type(e).__name__}: {e}"

    async def _seed_aggs(self, cfg):
        """One-time broker backfill for a fresh aggregator. Without this a
        (re)start blinds the engine for ~100 min (it needs 20 closed 5m bars
        from live ticks alone) and an open position cannot be time-stopped
        until its option candles rebuild. Runs at most once per symbol/token
        per process; seed_from_ohlc is itself a no-op once live ticks arrive."""
        from ..connectors import angelone
        for sym in cfg["symbols"]:
            key = sym.upper()
            agg = self._aggs.get(key)
            if agg is None or key in self._seeded:
                continue
            self._seeded.add(key)
            if agg.last_ts is not None:
                continue
            ref = _underlying_ref(sym)
            try:
                conn = await asyncio.to_thread(
                    angelone.fetch_candles, ref.get("exchange") or "NSE", sym,
                    ref.get("exchange") or "NSE", ref.get("token"),
                    None, None, None, "1m", "FUTURE" if _sym_meta(sym)["exchange"] == "MCX" else None)
                agg.seed_from_ohlc(conn.get("candles") or [])
            except Exception as e:
                self.last_error = f"seed {sym}: {type(e).__name__}: {e}"
        for t in self._open_positions():
            tok = str(t.get("symboltoken") or "")
            if not tok or tok in self._seeded:
                continue
            self._seeded.add(tok)
            is_mcx = str(t.get("market") or "").upper() == "MCX"
            opt_ex = "MCX" if is_mcx else "NFO"
            agg = self._opt_aggs.setdefault(tok, CandleAggregator())
            self._sub_tokens.setdefault(tok, {"exchange_type": 5 if is_mcx else 2})
            if agg.last_ts is not None:
                continue
            try:
                conn = await asyncio.to_thread(
                    angelone.fetch_candles, opt_ex, "", opt_ex, tok,
                    None, None, None, "1m", "OPTION")
                agg.seed_from_ohlc(conn.get("candles") or [])
            except Exception as e:
                self.last_error = f"seed opt {tok}: {type(e).__name__}: {e}"

    def _ensure_option_subs(self, chain):
        """Subscribe the ATM-band option tokens so their premium candles build."""
        if not self.feed:
            return
        for row in chain or []:
            for ot in ("ce", "pe"):
                leg = row.get(ot) or {}
                tok = leg.get("token")
                if tok and str(tok) not in self._sub_tokens:
                    self._sub_tokens[str(tok)] = {"exchange_type": int(leg.get("exchange_type") or 2)}
                    self._opt_aggs.setdefault(str(tok), CandleAggregator())

    def _pump_feed(self):
        """Copy fresh WS marks into the aggregators."""
        if not self.feed:
            return
        now = self._now()
        for sym, agg in self._aggs.items():
            tok = _underlying_ref(sym).get("token")
            ltp = self.feed.get_ltp(str(tok)) if tok else None
            if ltp is not None:
                agg.add_tick(now, float(ltp))
        for tok, agg in self._opt_aggs.items():
            ltp = self.feed.get_ltp(tok)
            if ltp is not None:
                agg.add_tick(now, float(ltp))

    # ---------------- decision + trade ----------------
    def _open_positions(self):
        return [t for t in db.list_trades(status="OPEN", limit=100, strategy="AUTOSCALP")
                ] + [t for t in db.list_trades(status="OPEN", limit=100, strategy="AUTOSCALP-ZTH")]

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
        smeta = _sym_meta(sym)
        step = float(smeta["strike_step"])
        atm = round(agg.last_price / step) * step
        # Market-hours awareness: suspend the strategy and publish an explicit
        # MARKET_CLOSED regime outside THIS symbol's exchange hours / on a holiday.
        if not (cfg.get("safeguards") or {}).get("allow_weekend"):
            from .. import market_calendar
            seg = market_calendar.segment_status(smeta["exchange"])
            if seg != "OPEN":
                sig = {"decision": "NO_TRADE", "signal_type": "NONE", "direction": "NONE",
                       "regime": "MARKET_CLOSED", "reason": f"{smeta['exchange']} {seg.lower()}"}
                self._persist_snapshot(sym, agg, atm, [], sig,
                                       (self.feed.status() if self.feed else {}).get("last_msg_age_sec"))
                await self._emit("autoscalp_signal", {"symbol": sym, "decision": "NO_TRADE",
                                                      "regime": "MARKET_CLOSED", "reason": sig["reason"]})
                return
        bars = agg.snapshot(now_epoch=self._now())
        if len(bars.get("5m") or []) < 20:
            return
        chain = []
        emode = "AUTO"
        if self.chain_provider:
            try:
                emode = ("AUTO_ROLL" if str(cfg.get("expiry_day_mode", "trade")).lower() == "roll"
                         and smeta["exchange"] in ("NSE", "BSE") else "AUTO")
                chain = self.chain_provider(sym, atm, cfg["strike_window"], smeta["exchange"], emode) or []
            except Exception as e:
                self.last_error = f"chain: {type(e).__name__}: {e}"
        self._ensure_option_subs(chain)

        # 0-DTE today? Only NSE index weeklies expire intraday-often; when the
        # operator chose "roll" we are already on the next weekly, so never.
        is_expiry_day = (smeta["exchange"] in ("NSE", "BSE") and emode != "AUTO_ROLL"
                         and _chain_is_expiry_day(chain))

        feed_st = self.feed.status() if self.feed else {}
        feed_age = feed_st.get("last_msg_age_sec")
        tod = _tod_bucket(_mod(datetime.now(timezone.utc).astimezone().isoformat()))

        strat_cfg = {"symbol": sym.upper(),
                     "strike_step": step, "strike_window": cfg["strike_window"],
                     **(cfg.get("strategy") or {}),
                     **((cfg.get("symbol_profiles") or {}).get(sym.upper()) or {}),
                     **((cfg.get("expiry_day_profile") or {}) if is_expiry_day else {})}
        sig = decide_from_context(
            bars, chain, atm=atm, calib=self.calibration(),
            avg_win=None, avg_loss=None, leg_bars_fn=self._leg_bars_fn(chain),
            tod_bucket=tod, config=strat_cfg)

        snap_id = self._persist_snapshot(sym, agg, atm, chain, sig, feed_age)
        await self._emit("autoscalp_signal", {"symbol": sym, **{k: sig.get(k) for k in
                         ("decision", "signal_type", "direction", "probability", "confidence", "reason")}})
        if sig.get("false_risk") == "LIKELY_FALSE" or sig.get("filtered"):
            self._tg_send(
                f"fb:{sym}:{sig.get('signal_type')}:{sig.get('filtered') or sig.get('false_risk')}",
                notify.lifecycle("FALSE_BREAKOUT",
                                 {"underlying": sym, "opt_type": "", "strike": 0},
                                 note=f"{sig.get('signal_type','')} {sig.get('reason','')}"),
                conf=sig.get("confidence"))

        if sig.get("decision") not in ("BUY_CE", "BUY_PE"):
            return

        def _record_block(why):
            b = self._blocks.setdefault(sym.upper(), {"n": 0})
            b.update(n=b["n"] + 1, last=why, ts=_now_iso(), signal=sig["decision"])
            if snap_id:
                db.update_live_snapshot(snap_id, {
                    "reason": (f"BLOCKED[{why}] :: {sig.get('reason') or ''}")[:2000]})

        # Expiry day: stop opening fresh 0-DTE risk into the pin / spread blow-out.
        if is_expiry_day and _ist_hhmm() >= str(cfg.get("expiry_day_entry_cutoff", "14:15")):
            _record_block("expiry_day_entry_cutoff")
            await self._emit("autoscalp_blocked", {"symbol": sym, "signal": sig["decision"],
                                                   "reason": "expiry_day_entry_cutoff"})
            return

        opens = self._open_positions()
        open_keys = {(t.get("underlying"), t.get("option_type")) for t in opens}
        allow, why = self.safeguards.check_entry(
            open_count=len(opens),
            feed_connected=bool(feed_st.get("connected", True)),
            feed_age_sec=feed_age, underlying=sym.upper(),
            side=sig["decision"].split("_")[1], open_keys=open_keys,
            option_premium=sig.get("entry"), underlying_price=agg.last_price,
            exchange=smeta["exchange"])
        if not allow:
            _record_block(why)
            await self._emit("autoscalp_blocked", {"symbol": sym, "reason": why, "signal": sig["decision"]})
            return

        self._open_paper(sym, sig)
        if is_expiry_day:
            self._maybe_open_zth(sym, sig, cfg, atm, step, smeta)

    def _maybe_open_zth(self, sym, sig, cfg, atm, step, smeta):
        """Expiry-day 'zero to hero': one tiny far-OTM long in the signal's
        direction. Premium is the risk (capped at zth.stop_frac); books at
        zth.target_mult or a hard IST time-exit. Deliberately kept out of the
        calibration sample and the scalp safeguard budget."""
        z = cfg.get("zth") or {}
        if not z.get("enabled"):
            return
        need = _CONF_RANK.get(str(z.get("min_confidence", "HIGH")).upper(), 3)
        if _CONF_RANK.get(str(sig.get("confidence") or "").upper(), 0) < need:
            return
        want_regimes = {str(r).upper() for r in (z.get("require_regime") or [])}
        if want_regimes and str(sig.get("regime") or "").upper() not in want_regimes:
            return
        if _ist_hhmm() >= str(z.get("hard_exit", "13:30")):
            return
        # one ZTH per symbol per IST session
        cap = int(z.get("max_per_day", 1) or 1)
        today = _ist_now().date().isoformat()
        done = [t for t in db.list_trades(status=None, limit=200, strategy="AUTOSCALP-ZTH")
                if str(t.get("underlying") or "").upper() == sym.upper()
                and str(t.get("opened_ts") or "")[:10] == today]
        if len(done) >= cap:
            return
        ot = sig["decision"].split("_")[1]                       # CE | PE
        n = int(z.get("otm_strikes", 4) or 4)
        target_strike = atm + (n * step if ot == "CE" else -n * step)
        # a wide one-shot chain pull just for the far strike (rare path)
        wide = []
        if self.chain_provider:
            try:
                wide = self.chain_provider(sym, atm, max(int(cfg["strike_window"]), n + 1),
                                           smeta["exchange"], "AUTO") or []
            except Exception as e:
                self.last_error = f"zth chain: {type(e).__name__}: {e}"
        row = next((r for r in wide
                    if abs(float(r.get("strike") or 0) - target_strike) < step / 2), None)
        leg = (row or {}).get(ot.lower()) or {}
        prem = leg.get("ltp")
        try:
            prem = float(prem)
        except (TypeError, ValueError):
            prem = None
        if not prem or prem <= 0 or prem > float(z.get("max_premium", 12.0)):
            return
        self._ensure_option_subs([row])
        zid = "ZTH-" + format(int(time.time() * 1000), "x")
        trow = open_trade({
            "signal_id": zid, "market": smeta["exchange"], "underlying": sym.upper(),
            "instrument": "OPTION", "expiry": leg.get("expiry") or sig.get("expiry") or "",
            "strike": row.get("strike"), "option_type": ot, "direction": "BUY",
            "timeframe": "5m", "entry": round(prem, 2),
            "target_1": round(prem * float(z.get("target_mult", 3.0)), 2), "target_2": None,
            "stop_loss": round(prem * float(z.get("stop_frac", 0.5)), 2), "trailing_stop": 0,
            "quantity": 1, "probability": sig.get("probability"),
            "confidence": sig.get("confidence"), "market_regime": sig.get("regime"),
            "oi_evidence": "", "reason": "expiry-day zero-to-hero | " + (sig.get("reason") or ""),
            "strategy": "AUTOSCALP-ZTH", "setup": sig.get("signal_type"), "atr_pct": None,
            "max_hold_sec": _secs_until_ist(z.get("hard_exit", "13:30")),
            "symboltoken": str(leg.get("token") or ""),
        })
        asyncio.create_task(self._emit("autoscalp_open",
                                       {"symbol": sym, "trade": trow, "signal_id": zid, "zth": True}))
        self._tg_send("zth:" + zid, notify.lifecycle(
            "ZERO_TO_HERO", {"underlying": sym.upper(), "opt_type": ot,
                             "strike": row.get("strike"), "entry": round(prem, 2)},
            note=f"far-OTM {ot} @ {round(prem, 2)} -> {round(prem * float(z.get('target_mult', 3.0)), 2)}"),
            conf=sig.get("confidence"))

    def _tg_send(self, key, text, conf=None, *, dedup=True, gate=True):
        """Single Telegram exit point: HIGH-confidence gate + de-duplication.

        - `conf`: the signal's confidence label. Unless `gate=False`, the card is
          dropped unless `conf` is at or above config.telegram_min_confidence
          (default HIGH). An unknown/None confidence fails closed (dropped).
        - `key`: dedup identity. A repeat of the same key inside
          config.telegram_dedup_sec is silently dropped.
        """
        cfg = self.get_config()
        if gate:
            need = _CONF_RANK.get(str(cfg.get("telegram_min_confidence", "HIGH")).upper(), 3)
            if _CONF_RANK.get(str(conf or "").upper(), 0) < need:
                return
        now = self._now()
        if dedup:
            gap = float(cfg.get("telegram_dedup_sec", 900) or 0)
            last = self._tg_last.get(key)
            if last is not None and (now - last) < gap:
                return
        self._tg_last[key] = now
        notify.push(self._telegram, text)

    def _open_paper(self, sym, sig):
        signal_id = "ASC-" + format(int(time.time() * 1000), "x")
        qty = 1
        row = open_trade({
            "signal_id": signal_id, "market": _sym_meta(sym)["exchange"], "underlying": sym.upper(),
            "instrument": "OPTION", "expiry": sig.get("expiry") or "",
            "strike": sig.get("strike") or 0, "option_type": sig["decision"].split("_")[1],
            "direction": "BUY", "timeframe": "5m", "entry": sig["entry"],
            "target_1": sig["target_1"], "target_2": sig.get("target_2"),
            "stop_loss": sig["stop_loss"], "trailing_stop": sig.get("trailing_stop") or 0,
            "quantity": qty, "probability": sig.get("probability"),
            "confidence": sig.get("confidence"), "market_regime": sig.get("regime"),
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
        self._tg_send("entry:" + signal_id, notify.signal_card(
            {**sig, "opt_tradingsymbol": sig.get("tradingsymbol")}, symbol=sym,
            index_ltp=self._aggs[sym.upper()].last_price), conf=sig.get("confidence"))

    def _monitor(self):
        for t in self._open_positions():
            tok = str(t.get("symboltoken") or "")
            ltp = self.feed.get_ltp(tok) if (self.feed and tok) else None
            if ltp is None:
                # The WS feed has no mark for this option token (feed gap /
                # illiquid strike). Such a position cannot be time-stopped the
                # normal way — sweep it if it is now badly overdue.
                swept = self._sweep_unmarkable(t)
                if swept:
                    self._finalize_close(swept)
                continue
            updated = update_trade_price(t["trade_id"], float(ltp))
            if updated and updated.get("status") == "CLOSED":
                self._finalize_close(updated)

    def _sweep_unmarkable(self, t):
        """Force-close a position the WS feed has never marked, once it is well
        past its max-hold (2x, min 20 min). Closes FLAT at entry with a
        TIME_NODATA exit — a clean scratch beats carrying a phantom position
        that the monitor can never resolve. Returns the closed row or None."""
        mh = t.get("max_hold_sec")
        try:
            mh = float(mh) if mh else 900.0
        except (TypeError, ValueError):
            mh = 900.0
        try:
            opened = datetime.fromisoformat(str(t.get("opened_ts")).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        age = (datetime.now(timezone.utc) - opened).total_seconds()
        if age < max(1200.0, 2.0 * mh):
            return None
        entry = t.get("entry")
        if entry is None:
            return None
        self.last_error = (f"swept unmarkable {t.get('underlying')} "
                           f"{t.get('option_type')}{t.get('strike')} after {int(age)}s (no WS mark)")
        return close_trade(t["trade_id"], float(entry), forced_result="FLAT",
                           exit_reason="TIME_NODATA")

    def _finalize_close(self, updated):
        """Shared close bookkeeping: safeguard feedback, scalp_signal outcome
        backfill, WS emit, Telegram exit card."""
        pnl = updated.get("pnl")
        # ZTH is a fixed-premium lottery leg — its P&L must not move the
        # scalp daily-loss / streak budget that halts the core engine.
        if (updated.get("strategy") or "") != "AUTOSCALP-ZTH":
            self.safeguards.on_trade_closed(pnl)
        _entry = updated.get("entry") or 0
        _pts = (updated.get("exit_price") or 0) - _entry
        _risk = abs(_entry - (updated.get("stop_loss") or _entry))
        _held = None
        try:
            _o = datetime.fromisoformat(str(updated.get("opened_ts")).replace("Z", "+00:00"))
            _c = datetime.fromisoformat(str(updated.get("closed_ts")).replace("Z", "+00:00"))
            _held = round((_c - _o).total_seconds())
        except (TypeError, ValueError):
            pass
        db.update_scalp_signal(updated.get("signal_id") or "", {
            "status": "CLOSED", "exit_price": updated.get("exit_price"),
            "exit_ts": _now_iso(), "exit_reason": updated.get("exit_reason"),
            "points": round(_pts, 2),
            "r_multiple": round(_pts / _risk, 3) if _risk > 0 else None,
            "outcome": updated.get("result"), "resolved": 1,
            "holding_sec": _held, "mfe": updated.get("mfe"), "mae": updated.get("mae")})
        asyncio.create_task(self._emit("autoscalp_close", {"trade": updated}))
        self._tg_send("exit:" + str(updated.get("trade_id")), notify.lifecycle(
            updated.get("exit_reason") or "EXIT", updated,
            note=f"held; MFE {updated.get('mfe')} MAE {updated.get('mae')}"),
            conf=updated.get("confidence"))

    # ---------------- persistence + calibration ----------------
    def _persist_snapshot(self, sym, agg, atm, chain, sig, feed_age):
        try:
            return db.insert_live_snapshot({
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
                "reason": sig.get("reason"), "ev": sig.get("ev"), "rr": sig.get("rr"),
                "feed_age_sec": feed_age,
                "chain_json": json.dumps(chain, default=str)[:20000],
            })
        except Exception as e:
            self.last_error = f"persist: {type(e).__name__}: {e}"
        return None

    def _maybe_daily_report(self):
        """Once per IST day per exchange, after its session close, push the
        day's per-symbol rollup to Telegram. Bypasses the confidence gate;
        a settings key makes it fire exactly once."""
        hhmm = _ist_hhmm()
        day = _ist_now().date().isoformat()
        for ex, close_hhmm in (("NSE", "15:35"), ("MCX", "23:35")):
            if hhmm < close_hhmm:
                continue
            key = f"autoscalp_report_sent:{ex}:{day}"
            try:
                if db.get_setting(key):
                    continue
                db.set_setting(key, _now_iso())
                from . import report as _report
                rep = _report.session_report(day)
                self._tg_send(f"report:{ex}:{day}",
                              notify.session_report_card(rep, segment=ex),
                              conf="HIGH", dedup=False, gate=False)
                asyncio.create_task(self._emit("autoscalp_daily_report",
                                               {"segment": ex, "report": rep}))
            except Exception as e:
                self.last_error = f"daily report {ex}: {type(e).__name__}: {e}"

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
            "entry_blocks": self._blocks,   # per-symbol: why BUY signals were refused entry
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
        await self._seed_aggs(cfg)
        self._pump_feed()
        self._monitor()
        self._maybe_daily_report()
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
