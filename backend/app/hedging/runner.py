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

import json
import logging
from datetime import datetime, timezone

from .. import db, instrument_profiles, telegram_dispatcher
from ..optionchain.resolve import get_chain
from . import capital as _capital
from . import position as _position
from . import primary_selector as _primary
from . import selector as _selector

_log = logging.getLogger(__name__)

ARMED_KEY = "hedging_armed"
CONFIG_KEY = "hedging_config"
LAST_SCAN_KEY = "hedging_last_scan"

DEFAULT_CONFIG = {
    "symbols": ["NIFTY", "BANKNIFTY", "SENSEX"],
    "min_distance_pct": 1.5,
    "min_oi": 500,
    "max_spread_pct": 0.15,
    "max_hedge_cost_pct_of_credit": 0.60,
    "max_risk_pct": 0.02,
    "max_concurrent_positions": 3,
    "max_positions_per_symbol": 1,
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


def evaluate_symbol(symbol: str, cfg: dict) -> dict:
    """One symbol's evaluate-and-maybe-open pass. Never raises -- a bad
    symbol/chain/lot-size is a NO_TRADE result, not a crash that stops the
    rest of the scan."""
    try:
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
            chain, min_distance_pct=cfg["min_distance_pct"], min_oi=cfg["min_oi"], lot_size=lot_size)
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
        pos = _position.open_position(
            symbol=symbol, primary_strike=primary_sel.leg.strike,
            primary_option_type=primary_sel.leg.option_type,
            primary_entry_premium=primary_sel.leg.premium,
            hedge_strike=cand.strike, hedge_entry_premium=cand.premium,
            lots=decision.lots, lot_size=lot_size,
            max_loss_per_lot=cand.economics.max_loss_per_lot,
            margin_locked=cand.economics.max_loss_per_lot * decision.lots,
            cost_paid=cand.premium * lot_size * decision.lots)

        _notify("OPEN", symbol,
               f"HEDGING OPEN -- {symbol}\n"
               f"SELL {primary_sel.leg.option_type} {primary_sel.leg.strike} @ {primary_sel.leg.premium}\n"
               f"BUY  {primary_sel.leg.option_type} {cand.strike} @ {cand.premium}\n"
               f"lots: {decision.lots} x {lot_size}  max_loss/lot: {cand.economics.max_loss_per_lot}\n"
               f"reason: {primary_sel.reason}\n"
               f"PAPER only, not a live order.")
        return {"symbol": symbol, "status": "OPENED", "position_id": pos.position_id,
                "primary": primary_sel.leg.__dict__, "hedge_strike": cand.strike, "lots": decision.lots}
    except Exception as e:
        _log.warning("evaluate_symbol(%s): %r", symbol, e)
        return {"symbol": symbol, "status": "ERROR", "reason": f"{type(e).__name__}: {e}"}


def _current_quotes(positions: dict) -> dict:
    quotes = {}
    chains: dict = {}
    for pid, pos in positions.items():
        if pos.status != "OPEN":
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
                                        current_hedge_premium=cur_hedge, now=now)
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


def scan(now=None) -> dict:
    """One full cron tick: exits first (never let a stale-priced position
    sit past its rule because a new one was being evaluated), then new-entry
    evaluation per configured symbol -- only if armed. `now` defaults to
    real IST time; pass it explicitly in tests."""
    if not is_armed():
        return {"armed": False, "note": "hedging is disarmed -- no entries evaluated, exits still monitored",
                "exits": check_exits(now=now)}
    exits = check_exits(now=now)
    cfg = get_config()
    entries = [evaluate_symbol(sym, cfg) for sym in cfg["symbols"]]
    return {"armed": True, "exits": exits, "entries": entries,
            "ts": datetime.now(timezone.utc).isoformat()}


def scan_and_record(now=None) -> None:
    """Runs scan() and persists the result for polling -- the multi-symbol
    chain fetch inside scan() can legitimately take tens of seconds
    (real, measured latency on app.optionchain.resolve.get_chain, not a
    bug), long enough that a mobile client's connection gets dropped
    mid-request (seen live: nginx 499s on /scan-now). The API route backs
    this with a background task and returns immediately instead."""
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
