"""
Unified Signal Dashboard -- READ-ONLY aggregator.

One row per traded symbol, every engine's current read side-by-side. Adds NO
blending / scoring logic of its own -- it only reads what each engine already
produces and lays them next to each other, plus a display-only agreement tally.

Sources (all already read-only):
  * AutoScalp    -- latest live_market_snapshots row per symbol (the real signal)
  * HCS          -- app.hcs.engine.evaluate() (A+ quality gate over that snapshot)
  * Confluence   -- mathematical_confluence market-map (levels / bias)
  * Order-flow   -- app.orderflow.service.h1h7_state (shadow market-state)

No order, no live-signal emission, no change to any engine / frozen logic /
calibration / cron. `live_trading` stays false.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter

router = APIRouter(prefix="/api/signals", tags=["signals-dashboard"])

_TTL = 15.0
_cache: dict = {"ts": 0.0, "data": None}

_BULL = {"BUY_CE", "BULLISH", "SUPPORT_REVERSAL", "RESISTANCE_BREAKOUT", "H1_CONT", "H1_CONT_OBSERVE"}
_BEAR = {"BUY_PE", "BEARISH", "SUPPORT_BREAKDOWN", "RESISTANCE_REVERSAL"}


def _dir(*tokens) -> str:
    t = {str(x or "").upper() for x in tokens}
    if t & _BULL and not (t & _BEAR):
        return "BULLISH"
    if t & _BEAR and not (t & _BULL):
        return "BEARISH"
    return "NEUTRAL"


def _symbols() -> list[str]:
    try:
        from . import runtime
        syms = (runtime.autoscalp.get_config() or {}).get("symbols")
        if syms:
            return [str(s).upper() for s in syms]
    except Exception:
        pass
    return ["NIFTY", "BANKNIFTY", "CRUDEOIL", "NATURALGAS", "SENSEX"]


def _hcs_by_symbol() -> dict:
    try:
        from .hcs import engine as _hcs
        r = _hcs.evaluate()
        return {x["symbol"]: x for x in (r.get("results") or [])}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def _confluence_by_symbol(symbols: list[str]) -> dict:
    try:
        from .mathematical_confluence import api as _mc
        m = _mc.api_market_map(symbols=",".join(symbols))
        out = {}
        for row in (m.get("market_map") or []):
            s = str(row.get("instrument") or "").upper()
            if s:
                out[s] = row
        return out
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def _orderflow_state(sym: str) -> dict:
    try:
        from .orderflow import service as _of
        from . import market_hub
        dates = market_hub.session_dates(sym, tf="5m", limit=1)
        if not dates:
            return {"status": "no_session"}
        out = _of.h1h7_state(sym, dates[-1], tf="5m", only_last=True)
        ev = (out.get("events") or [])
        last = ev[-1] if ev else None
        return {
            "status": "OK",
            "state": (last or {}).get("state") or "NEUTRAL",
            "action": (last or {}).get("action") or "NO_ACTION",
            "research_status": (last or {}).get("research_status"),
            "as_of": (last or {}).get("timestamp"),
        }
    except Exception as e:
        return {"status": "unavailable", "reason": f"{type(e).__name__}: {e}"}


def build() -> dict:
    now = time.time()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL:
        return {**_cache["data"], "cached": True}

    syms = _symbols()
    hcs = _hcs_by_symbol()
    conf = _confluence_by_symbol(syms)
    rows = []
    for s in syms:
        h = hcs.get(s) or {}
        c = conf.get(s) or {}
        of = _orderflow_state(s)

        # AutoScalp only casts a directional vote when it actually wants a trade.
        # A NO_TRADE / WATCH row can still carry a latent signal_type -- that is
        # not a signal and must not sway `agreement`.
        as_decision = str(h.get("decision") or "").upper()
        if as_decision in ("BUY_CE", "BUY_PE"):
            as_dir = _dir(h.get("decision"), h.get("direction"), h.get("signal_type"))
        else:
            as_dir = "NEUTRAL"
        conf_sig = c.get("signal")
        # confluence `direction` is CE/PE; only counts as a vote if it also has a signal
        cd = str(c.get("direction") or "").upper()
        conf_dir = ("BULLISH" if cd == "CE" else "BEARISH" if cd == "PE" else "NEUTRAL")
        if str(conf_sig or "").upper() in ("NO_TRADE", "NONE", ""):
            conf_dir = "NEUTRAL"
        of_dir = _dir(of.get("state"))

        votes = [d for d in (as_dir, conf_dir, of_dir) if d != "NEUTRAL"]
        bull = votes.count("BULLISH")
        bear = votes.count("BEARISH")
        agreement = ("BULLISH" if bull and not bear else
                     "BEARISH" if bear and not bull else
                     "MIXED" if bull and bear else "NEUTRAL")

        rows.append({
            "symbol": s,
            "autoscalp": {
                "decision": h.get("decision"), "direction": as_dir,
                "signal_type": h.get("signal_type"), "regime": h.get("regime"),
                "confidence": h.get("confidence"),
                "probability": h.get("calibrated_probability"),
                "entry": h.get("entry"), "stop_loss": h.get("stop_loss"),
                "target_1": h.get("target_1"), "target_2": h.get("target_2"),
                "rr": h.get("rr"), "ev_r": h.get("ev_r"),
                "as_of": h.get("as_of"),
            },
            "hcs": {
                "a_plus": h.get("a_plus"), "hcs_score": h.get("hcs_score"),
                "adaptive_probability": h.get("adaptive_probability"),
                "top_veto": (h.get("vetoes") or [{}])[0].get("filter") if h.get("vetoes") else None,
                "reason": (h.get("reasons") or [None])[0],
            },
            "confluence": {
                "signal": conf_sig, "direction": conf_dir,
                "ce_pe": c.get("direction"),
                "score": c.get("confluence_score"), "confidence": c.get("confidence"),
                "regime": c.get("market_regime"),
                "spot": c.get("spot"),
                "support": c.get("nearest_support"), "resistance": c.get("nearest_resistance"),
                "pivot": c.get("pivot"),
            },
            "orderflow": of,
            "agreement": agreement,
            "agreement_votes": {"bullish": bull, "bearish": bear,
                                "n": len(votes)},
        })

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "note": "Read-only aggregation. Each engine's own output, side-by-side. "
                "`agreement` is a display tally, NOT a blended signal. AutoScalp is the "
                "only tradeable signal; HCS/order-flow are SHADOW; confluence is analysis. "
                "live_trading=false.",
        "errors": {k: v for k, v in (("hcs", hcs.get("_error")),
                                     ("confluence", conf.get("_error"))) if v},
        "rows": rows,
        "cached": False,
    }
    _cache.update(ts=now, data=data)
    return data


@router.get("/unified")
def unified_signals():
    return build()
