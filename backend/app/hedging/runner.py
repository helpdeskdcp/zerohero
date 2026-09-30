"""Autonomous hedging scan -- the piece connecting primary_selector (which
strike to sell) + selector (which strike protects it) + capital (how many
lots) + position (open/close) into one PAPER decision, run on a cron (see
scripts/hedging_scan.py), not a live WS-feed worker -- a hedge is a slower,
protective structure, so a <=5-min-stale captured chain (allow_network=False,
same as today's other new read-only engines) is an acceptable, deliberately
conservative tradeoff against the complexity/risk of wiring a new live feed
subscription into main.py's startup.

DISARMED by default (HEDGING_ARMED_KEY unset = "0"), matching every other
autonomous engine in this codebase (autoscalp, ScalpRunner): nothing opens a
position until a human explicitly arms it via the API/frontend.

The primary/hedge selection math itself (selector.py, primary_selector.py)
is deterministic and NOT backtested -- see their own docstrings. This module
adds no new trading logic, only the plumbing to run that math on a schedule
and notify.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import datetime, timedelta, timezone

from .. import db, instrument_profiles, instruments, market_calendar, telegram_dispatcher
from ..connectors.angelone import fetch_market_quote
from ..optionchain.resolve import get_chain
from . import capital as _capital
from . import decision_log as _decision_log
from . import position as _position
from . import primary_selector as _primary
from . import selector as _selector

_log = logging.getLogger(__name__)

# File lock, not an in-process one: scan_and_record() is called both from
# this API server process (POST /scan-now) and from a separate OS process
# every 5 min (scripts/hedging_scan_cron.sh -> scripts/hedging_scan.py,
# which imports and calls this same function) -- an in-process lock
# (threading.Lock) would only guard the former. Found 2026-09-29: an
# API-triggered scan and a near-simultaneous second trigger raced each
# other past the (lock-free) "is there already an open NATURALGAS
# position?" check in scan(), each independently deciding to open one --
# TWO real positions opened 211ms apart, doubling the intended capital
# exposure. Non-blocking: an overlapping call skips instead of queuing,
# same semantics as the cron wrapper's own flock (scripts/hedging_scan_
# cron.sh) -- a hedge scan that's still running is stale data anyway, not
# worth waiting on.
_LOCK_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                          "data", ".hedging_runner_scan.lock")

ARMED_KEY = "hedging_armed"
CONFIG_KEY = "hedging_config"
LAST_SCAN_KEY = "hedging_last_scan"
_IST = timezone(timedelta(hours=5, minutes=30))   # matches position.py/market_calendar.py's own constant

DEFAULT_CONFIG = {
    "symbols": ["NIFTY", "BANKNIFTY", "SENSEX"],
    "min_distance_pct": 1.5,
    "min_oi": 500,
    "min_volume": 100,
    "max_spread_pct": 0.15,
    "max_hedge_cost_pct_of_credit": 0.60,
    "max_risk_pct": 0.02,
    "max_concurrent_positions": 3,
    "max_positions_per_symbol": 1,
    # Portfolio-level cap (MCP 3.1.2 gap: per-trade risk% alone doesn't stop
    # several max-sized positions from collectively over-using the account) --
    # checked against starting_capital, not the shrinking available_capital,
    # so the ceiling is a stable fraction of the account, not a moving target.
    "max_portfolio_margin_pct": 0.50,
    # Kill-switch (MCP 3.3 section 45/46 -- "capital survival over trade
    # frequency"). Both checked once per scan cycle, before any symbol is
    # evaluated; either breach disarms the engine outright (not just
    # skips this cycle's entries) -- "do not attempt recovery trades".
    "max_daily_loss_pct": 0.05,     # of starting_capital; today's (IST) realized P&L only
    "max_consecutive_losses": 3,    # most-recently-closed streak, across any days
}


def get_config() -> dict:
    raw = db.get_setting(CONFIG_KEY)
    stored = {}
    if raw:
        try:
            stored = json.loads(raw)
        except Exception as e:
            _log.warning("get_config: corrupt hedging_config value, using defaults: %r", e)
    return {**DEFAULT_CONFIG, **stored}


def set_config(patch: dict) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("config must be an object")
    unknown = set(patch) - set(DEFAULT_CONFIG)
    if unknown:
        raise ValueError(f"unknown config field(s): {', '.join(sorted(unknown))}")
    cfg = {**get_config(), **patch}
    db.set_setting(CONFIG_KEY, json.dumps(cfg))
    return cfg


def is_armed() -> bool:
    return db.get_setting(ARMED_KEY) == "1"


def arm() -> None:
    db.set_setting(ARMED_KEY, "1")


def disarm() -> None:
    db.set_setting(ARMED_KEY, "0")


def _notify(kind: str, symbol: str, text: str) -> None:
    try:
        telegram_dispatcher.dispatch(source_engine="hedging", underlying=symbol,
                                     direction=kind, text=text)
    except Exception as e:
        _log.warning("_notify(%s, %s): %r", kind, symbol, e)


def _lot_size(symbol: str) -> int | None:
    prof = instrument_profiles.get_instrument_profile(symbol)
    val = prof.lot_size.value
    return int(val) if val else None


_OPTION_RESOLVER = {
    "NIFTY": instruments.resolve_nse_option, "BANKNIFTY": instruments.resolve_nse_option,
    "FINNIFTY": instruments.resolve_nse_option, "MIDCPNIFTY": instruments.resolve_nse_option,
    "SENSEX": instruments.resolve_bse_option, "BANKEX": instruments.resolve_bse_option,
    "CRUDEOIL": instruments.resolve_mcx_option, "NATURALGAS": instruments.resolve_mcx_option,
}


def _leg_token(symbol: str, expiry: str, strike: float, option_type: str) -> str | None:
    """Resolve a leg's broker symboltoken for live-quote lookups (see
    _current_quotes()). Best-effort: returns None on anything unexpected
    rather than blocking the (already-decided, already-selected) trade --
    a missing token just means that leg's exit-checks fall back to the
    captured-chain path, same as before this existed."""
    fn = _OPTION_RESOLVER.get(symbol.upper())
    if fn is None:
        return None
    try:
        r = fn(symbol, expiry, strike=strike, option_type=option_type)
        return r.get("symboltoken") if r.get("status") == "OK" else None
    except Exception as e:
        _log.debug("_leg_token(%s %s %s%s): %r", symbol, expiry, strike, option_type, e)
        return None


def evaluate_symbol(symbol: str, cfg: dict, now=None) -> dict:
    """One symbol's evaluate-and-maybe-open pass. Never raises -- a bad
    symbol/chain/lot-size is a NO_TRADE result, not a crash that stops the
    rest of the scan. `now` defaults to real IST time; pass it explicitly in
    tests (mirrors position.check_exit's own convention) so the session gate
    doesn't depend on the wall clock at whatever moment the suite runs."""
    try:
        session_ok, session_reason = _session_open(symbol, now=now)
        if not session_ok:
            return {"symbol": symbol, "status": "NO_TRADE", "reason": session_reason}

        positions = _position.load_all()
        open_for_symbol = [p for p in positions.values() if p.status == "OPEN" and p.symbol == symbol]
        open_total = [p for p in positions.values() if p.status == "OPEN"]
        if len(open_for_symbol) >= cfg["max_positions_per_symbol"]:
            return {"symbol": symbol, "status": "SKIPPED", "reason": "already has an open position"}
        if len(open_total) >= cfg["max_concurrent_positions"]:
            return {"symbol": symbol, "status": "SKIPPED", "reason": "max_concurrent_positions reached"}

        lot_size = _lot_size(symbol)
        if not lot_size:
            return {"symbol": symbol, "status": "NO_TRADE", "reason": "no verified lot size for this symbol"}

        chain = get_chain(symbol, "AUTO", allow_network=False)
        if chain is None or not chain.rows:
            return {"symbol": symbol, "status": "NO_DATA", "reason": "no captured chain"}

        primary_sel = _primary.select_primary(
            chain, min_distance_pct=cfg["min_distance_pct"], min_oi=cfg["min_oi"], lot_size=lot_size,
            min_volume=cfg.get("min_volume", _primary.DEFAULT_MIN_VOLUME),
            max_spread_pct=cfg["max_spread_pct"])
        if primary_sel.status != "SELECTED":
            return {"symbol": symbol, "status": "NO_TRADE", "reason": f"primary: {primary_sel.reason}"}

        cap = _capital.load()
        hedge_cfg = _selector.HedgeConfig(min_oi=cfg["min_oi"], max_spread_pct=cfg["max_spread_pct"],
                                          max_hedge_cost_pct_of_credit=cfg["max_hedge_cost_pct_of_credit"])
        decision = _selector.select_hedge(chain, primary_sel.leg, hedge_cfg,
                                          available_capital=cap.available_capital,
                                          max_risk_pct=cfg["max_risk_pct"])
        if decision.status != "SELECTED":
            return {"symbol": symbol, "status": "NO_TRADE", "reason": f"hedge: {decision.reason}"}

        cand = decision.candidate
        new_margin = cand.economics.max_loss_per_lot * decision.lots
        existing_margin = sum(p.margin_locked for p in positions.values() if p.status == "OPEN")
        margin_ceiling = cap.starting_capital * cfg.get("max_portfolio_margin_pct", 0.50)
        projected_margin = existing_margin + new_margin
        if projected_margin > margin_ceiling:
            return {"symbol": symbol, "status": "NO_TRADE",
                    "reason": f"PORTFOLIO_MARGIN_LIMIT: projected margin {projected_margin:.2f} "
                              f"> ceiling {margin_ceiling:.2f} ({cfg.get('max_portfolio_margin_pct', 0.50):.0%} "
                              f"of starting capital {cap.starting_capital:.2f})"}

        primary_token = _leg_token(symbol, chain.expiry, primary_sel.leg.strike, primary_sel.leg.option_type)
        hedge_token = _leg_token(symbol, chain.expiry, cand.strike, cand.option_type)
        pos = _position.open_position(
            symbol=symbol, primary_strike=primary_sel.leg.strike,
            primary_option_type=primary_sel.leg.option_type,
            primary_entry_premium=primary_sel.leg.premium,
            hedge_strike=cand.strike, hedge_entry_premium=cand.premium,
            lots=decision.lots, lot_size=lot_size,
            max_loss_per_lot=cand.economics.max_loss_per_lot,
            margin_locked=new_margin,
            cost_paid=cand.premium * lot_size * decision.lots,
            primary_token=primary_token, hedge_token=hedge_token,
            max_profit_per_lot=cand.economics.max_profit_per_lot,
            net_credit_per_lot=round(cand.economics.net_credit_per_unit * lot_size, 2),
            strike_width=cand.economics.strike_width)

        decision_id = _decision_log.record({
            "position_id": pos.position_id, "symbol": symbol, "config": cfg,
            "market": {"spot": chain.spot, "expiry": chain.expiry, "chain_ts": chain.ts},
            "primary_leg": {"strike": primary_sel.leg.strike, "option_type": primary_sel.leg.option_type,
                            "fill_price": primary_sel.leg.premium, "ltp": primary_sel.leg.ltp,
                            "bid": primary_sel.leg.bid, "ask": primary_sel.leg.ask,
                            "reason": primary_sel.reason},
            "hedge_leg": {"strike": cand.strike, "option_type": cand.option_type,
                         "fill_price": cand.premium, "ltp": cand.ltp, "bid": cand.bid, "ask": cand.ask,
                         "spread_pct": cand.spread_pct},
            "rejected_candidates": [
                {"strike": c.strike, "premium": c.premium, "reasons": c.reject_reasons}
                for c in decision.all_candidates if not c.accepted],
            "economics": {"net_credit_per_lot": round(cand.economics.net_credit_per_unit * lot_size, 2),
                         "max_profit_per_lot": cand.economics.max_profit_per_lot,
                         "max_loss_per_lot": cand.economics.max_loss_per_lot,
                         "breakeven": cand.economics.breakeven, "strike_width": cand.economics.strike_width},
            "portfolio": {"existing_margin": existing_margin, "new_margin": new_margin,
                         "margin_ceiling": margin_ceiling},
            "lots": decision.lots, "lot_size": lot_size,
        })

        _notify("OPEN", symbol,
               f"HEDGING OPEN -- {symbol}\n"
               f"SELL {primary_sel.leg.option_type} {primary_sel.leg.strike} @ {primary_sel.leg.premium}\n"
               f"BUY  {primary_sel.leg.option_type} {cand.strike} @ {cand.premium}\n"
               f"lots: {decision.lots} x {lot_size}  "
               f"max_profit/lot: {cand.economics.max_profit_per_lot}  "
               f"max_loss/lot: {cand.economics.max_loss_per_lot}  "
               f"breakeven: {cand.economics.breakeven}\n"
               f"reason: {primary_sel.reason}\n"
               f"PAPER only, not a live order.")
        return {"symbol": symbol, "status": "OPENED", "position_id": pos.position_id,
                "decision_id": decision_id,
                "primary": primary_sel.leg.__dict__, "hedge_strike": cand.strike, "lots": decision.lots}
    except Exception as e:
        _log.warning("evaluate_symbol(%s): %r", symbol, e)
        return {"symbol": symbol, "status": "ERROR", "reason": f"{type(e).__name__}: {e}"}


_SYMBOL_EXCHANGE = {
    "NIFTY": "NFO", "BANKNIFTY": "NFO", "FINNIFTY": "NFO", "MIDCPNIFTY": "NFO",
    "SENSEX": "BFO", "BANKEX": "BFO",
    "CRUDEOIL": "MCX", "NATURALGAS": "MCX",
}

# app.market_calendar's window table is keyed by cash-segment name
# (NSE/BSE/MCX -- see its own _DEFAULT_WINDOWS), not the broker
# order-routing segment (_SYMBOL_EXCHANGE's NFO/BFO/MCX) used for quote
# fetches -- NFO/BFO share their cash market's session, so this is a pure
# rename, not a second/duplicated calendar.
_CALENDAR_EXCHANGE = {"NFO": "NSE", "BFO": "BSE", "MCX": "MCX"}

# Exit cutoff = the instrument's real session close minus this buffer, so a
# position is unwound with room to spare rather than racing the close.
_EXIT_CUTOFF_BUFFER_MIN = 15


def _session_open(symbol: str, now=None) -> tuple[bool, str | None]:
    """Entry-gate check (MCP 3.1.1 spec section 3/6): a symbol whose exchange
    can't be resolved fails OPEN so a bad/unmapped symbol can't silently
    bypass the gate. Exits are never gated this way -- check_exits() must
    keep monitoring/closing an already-open position regardless of session,
    only new entries are blocked here."""
    exch = _SYMBOL_EXCHANGE.get(symbol.upper())
    if not exch:
        return False, f"no exchange mapping for {symbol} -- cannot verify session"
    cal_exch = _CALENDAR_EXCHANGE.get(exch, exch)
    if not market_calendar.is_trading(cal_exch, now, instrument=symbol):
        return False, f"{cal_exch} session not open ({market_calendar.segment_status(cal_exch, now)})"
    return True, None


def _exit_cutoff_for(symbol: str):
    """Per-instrument TIME_CUTOFF (MCP 3.1.1 spec section 3/4): a single
    generic cutoff for every symbol either force-exits MCX positions hours
    before their real 23:30 close or leaves NSE/BSE positions open past
    theirs. Falls back to position.py's DEFAULT_EXIT_CUTOFF_IST (15:15) if
    the exchange can't be resolved or the calendar has no window for it --
    the previous, symbol-blind behaviour, not a new failure mode."""
    exch = _SYMBOL_EXCHANGE.get(symbol.upper())
    close = market_calendar.session_close(_CALENDAR_EXCHANGE.get(exch, exch), instrument=symbol) if exch else None
    if close is None:
        return _position.DEFAULT_EXIT_CUTOFF_IST
    close_dt = datetime(2000, 1, 1, close.hour, close.minute)
    cutoff_dt = close_dt - timedelta(minutes=_EXIT_CUTOFF_BUFFER_MIN)
    return cutoff_dt.time()


def _live_leg_premium(symbol: str, token: str | None) -> float | None:
    """A genuinely live (seconds-fresh, not <=5-min-stale-captured) LTP for
    one leg, via the same broker quote endpoint app.connectors.angelone
    already exposes for read-only use elsewhere. None on anything short of
    a clean OK -- never a stale/fabricated number -- so the caller falls
    back to the captured-chain path."""
    if not token:
        return None
    exch = _SYMBOL_EXCHANGE.get(symbol.upper())
    if not exch:
        return None
    try:
        q = fetch_market_quote(exch, token)
        if q.get("status") == "OK" and q.get("ltp") is not None:
            return float(q["ltp"])
    except Exception as e:
        _log.debug("_live_leg_premium(%s, %s): %r", symbol, token, e)
    return None


def _current_quotes(positions: dict) -> dict:
    quotes = {}
    chains: dict = {}
    for pid, pos in positions.items():
        if pos.status != "OPEN":
            continue
        # Live quote first -- falls through to the captured-chain path
        # below when a leg has no stored token (a position opened before
        # this existed) or the live call fails for any reason (auth,
        # network, broker-side issue). Never blocks or raises either way.
        cur_primary = _live_leg_premium(pos.symbol, pos.primary_token)
        cur_hedge = _live_leg_premium(pos.symbol, pos.hedge_token)
        if cur_primary is not None and cur_hedge is not None:
            quotes[pid] = (cur_primary, cur_hedge)
            continue
        if pos.symbol not in chains:
            try:
                chains[pos.symbol] = get_chain(pos.symbol, "AUTO", allow_network=False)
            except Exception:
                chains[pos.symbol] = None
        chain = chains[pos.symbol]
        if chain is None:
            continue
        side = "ce" if pos.primary_option_type == "CE" else "pe"
        primary_leg = next((getattr(r, side) for r in chain.rows
                            if r.strike == pos.primary_strike and getattr(r, side)), None)
        hedge_leg = next((getattr(r, side) for r in chain.rows
                          if r.strike == pos.hedge_strike and getattr(r, side)), None)
        if primary_leg and primary_leg.ltp is not None and hedge_leg and hedge_leg.ltp is not None:
            quotes[pid] = (primary_leg.ltp, hedge_leg.ltp)
    return quotes


def check_exits(now=None) -> list[dict]:
    """Monitors every OPEN position for STOP_LOSS/PROFIT_TARGET/TIME_CUTOFF,
    closes and notifies on any that trigger. A position whose current quote
    can't be resolved right now is left OPEN, never closed blind. `now`
    defaults to real IST time; pass it explicitly in tests (mirrors
    position.check_exit's own convention)."""
    positions = _position.load_all()
    quotes = _current_quotes(positions)
    results = []
    for pid, pos in positions.items():
        if pos.status != "OPEN":
            continue
        quote = quotes.get(pid)
        if quote is None:
            results.append({"position_id": pid, "symbol": pos.symbol, "status": "NO_QUOTE"})
            continue
        cur_primary, cur_hedge = quote
        decision = _position.check_exit(pos, current_primary_premium=cur_primary,
                                        current_hedge_premium=cur_hedge, now=now,
                                        cutoff=_exit_cutoff_for(pos.symbol))
        if not decision["should_exit"]:
            results.append({"position_id": pid, "symbol": pos.symbol, "status": "OPEN",
                            "pnl": decision["pnl"]})
            continue
        _position.close_position(pos, exit_reason=decision["reason"], pnl=decision["pnl"])
        _notify("CLOSE", pos.symbol,
               f"HEDGING CLOSE -- {pos.symbol}\n"
               f"reason: {decision['reason']}  pnl: {decision['pnl']}\n"
               f"SELL {pos.primary_option_type} {pos.primary_strike} / "
               f"BUY {pos.primary_option_type} {pos.hedge_strike}\n"
               f"PAPER only.")
        results.append({"position_id": pid, "symbol": pos.symbol, "status": "CLOSED",
                        "reason": decision["reason"], "pnl": decision["pnl"]})
    return results


def _reconcile_capital() -> dict | None:
    """Self-healing capital/position reconciliation, run every scan tick
    (see capital.reconcile()'s docstring for why this exists). Returns the
    reconcile report only when a mismatch was found and corrected --
    None on a clean reconcile, so scan()'s result stays uncluttered on the
    common (healthy) path."""
    positions = _position.load_all()
    open_margin = sum(p.margin_locked for p in positions.values() if p.status == "OPEN")
    cap = _capital.load()
    check = _capital.reconcile(open_margin, cap)
    if check["ok"]:
        return None
    _log.warning("hedging capital ledger desync: expected allocated_margin=%.2f actual=%.2f diff=%.2f -- resyncing",
                check["expected_allocated_margin"], check["actual_allocated_margin"], check["diff"])
    _capital.save(_capital.resync(cap, open_margin))
    _notify("RECONCILE", "SYSTEM",
           f"HEDGING capital ledger desync detected and auto-corrected.\n"
           f"expected margin (from open positions): {check['expected_allocated_margin']:.2f}\n"
           f"ledger had: {check['actual_allocated_margin']:.2f}  (diff {check['diff']:.2f})\n"
           f"Investigate root cause if this recurs -- a healthy ledger should never desync.")
    return check


def _kill_switch_breach(positions: dict, cfg: dict, cap, now=None) -> dict | None:
    """Capital-survival kill switch (MCP 3.3 section 45/46): "when daily
    loss limit reached, DISARM -- do not attempt recovery trades" and
    "if live strategy performance degrades materially, AUTO PAUSE
    STRATEGY". Two independent triggers, either disarms:

    1. today's (IST) realized P&L across every position CLOSED today <=
       -(max_daily_loss_pct * starting_capital).
    2. the most-recently-closed positions, taken in closed_at order, form
       a losing streak of >= max_consecutive_losses -- stops counting at
       the first non-loss, so this is "how many losses in a row right
       now", not "how many losses today" (a losing streak that started
       late yesterday and continues today is still a real streak).

    Returns None when neither trips (the common, healthy path)."""
    now = now or datetime.now(_IST)
    now_ist = now.astimezone(_IST) if now.tzinfo else now.replace(tzinfo=_IST)
    today = now_ist.date()

    closed = [p for p in positions.values() if p.status == "CLOSED" and p.closed_at]

    def _closed_ist_date(p):
        ts = datetime.fromisoformat(p.closed_at)
        return (ts.astimezone(_IST) if ts.tzinfo else ts.replace(tzinfo=_IST)).date()

    today_pnl = sum(p.gross_pnl or 0.0 for p in closed if _closed_ist_date(p) == today)
    daily_limit = cap.starting_capital * cfg.get("max_daily_loss_pct", 0.05)
    if today_pnl <= -daily_limit:
        return {"reason": "DAILY_LOSS_LIMIT", "today_pnl": round(today_pnl, 2),
                "limit": round(daily_limit, 2)}

    by_close = sorted(closed, key=lambda p: p.closed_at, reverse=True)
    streak = 0
    for p in by_close:
        if (p.gross_pnl or 0.0) < 0:
            streak += 1
        else:
            break
    max_streak = cfg.get("max_consecutive_losses", 3)
    if streak >= max_streak:
        return {"reason": "CONSECUTIVE_LOSS_LIMIT", "streak": streak, "limit": max_streak}
    return None


def _scan_unlocked(now=None) -> dict:
    """One full cron tick: exits first (never let a stale-priced position
    sit past its rule because a new one was being evaluated), then a
    capital/position reconciliation check, then the kill switch, then
    new-entry evaluation per configured symbol -- only if armed and the
    kill switch didn't just trip. `now` defaults to real IST time; pass it
    explicitly in tests."""
    if not is_armed():
        return {"armed": False, "note": "hedging is disarmed -- no entries evaluated, exits still monitored",
                "exits": check_exits(now=now)}
    exits = check_exits(now=now)
    reconciliation = _reconcile_capital()
    cfg = get_config()

    positions = _position.load_all()
    cap = _capital.load()
    breach = _kill_switch_breach(positions, cfg, cap, now=now)
    if breach is not None:
        disarm()
        if breach["reason"] == "DAILY_LOSS_LIMIT":
            msg = (f"HEDGING KILL-SWITCH -- auto-disarmed.\n"
                   f"today's realized P&L: {breach['today_pnl']:.2f} <= -{breach['limit']:.2f} "
                   f"(daily loss limit)\nNo recovery trades. Manual re-arm required after review.")
        else:
            msg = (f"HEDGING KILL-SWITCH -- auto-disarmed.\n"
                   f"{breach['streak']} losses in a row (limit {breach['limit']})\n"
                   f"Manual re-arm required after review.")
        _notify("KILL_SWITCH", "SYSTEM", msg)
        out = {"armed": False, "exits": exits, "entries": [], "kill_switch": breach,
              "ts": datetime.now(timezone.utc).isoformat()}
        if reconciliation is not None:
            out["reconciliation"] = reconciliation
        return out

    entries = [evaluate_symbol(sym, cfg, now=now) for sym in cfg["symbols"]]
    out = {"armed": True, "exits": exits, "entries": entries,
          "ts": datetime.now(timezone.utc).isoformat()}
    if reconciliation is not None:
        out["reconciliation"] = reconciliation
    return out


def scan(now=None) -> dict:
    """Locked entry point for one full cron tick -- see _LOCK_PATH's
    comment for why this needs to be a FILE lock and why it must live
    here, not only in scan_and_record(). Found 2026-09-29: the actual
    cron entry point (scripts/hedging_scan.py) imports and calls scan()
    directly, never scan_and_record() -- a lock placed only on
    scan_and_record() left the real cron path completely unprotected,
    and a scan_and_record()-triggered call (e.g. POST /scan-now) could
    still race a concurrent cron-triggered scan() past the open-position
    checks. This cost a real (paper) position record and left the
    capital ledger double-locking margin for a position that no longer
    existed -- found, and the ledger manually recomputed from the actual
    position-store state, the same day this lock was first added (in the
    wrong place). Skips (returns a SKIPPED marker) rather than blocking
    when another scan is already in flight -- a scan that's still
    running is stale data anyway, not worth waiting on."""
    os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
    with open(_LOCK_PATH, "w") as lock_f:
        try:
            fcntl.flock(lock_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            _log.info("scan: another scan is already running, skipping")
            return {"status": "SKIPPED", "note": "another scan is already running"}
        try:
            return _scan_unlocked(now=now)
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def scan_and_record(now=None) -> None:
    """Runs scan() (locked, see its own docstring) and persists the result
    for polling -- the multi-symbol chain fetch inside scan() can
    legitimately take tens of seconds (real, measured latency on
    app.optionchain.resolve.get_chain, not a bug), long enough that a
    mobile client's connection gets dropped mid-request (seen live: nginx
    499s on /scan-now). The API route backs this with a background task
    and returns immediately instead."""
    try:
        result = scan(now=now)
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    result["recorded_at"] = datetime.now(timezone.utc).isoformat()
    db.set_setting(LAST_SCAN_KEY, json.dumps(result, default=str))


def last_scan_result() -> dict | None:
    raw = db.get_setting(LAST_SCAN_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception as e:
        _log.warning("last_scan_result: corrupt hedging_last_scan value: %r", e)
        return None
