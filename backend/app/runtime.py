"""
Runtime composition root — the long-lived singletons every route module reads
from: the WebSocket connection manager, the ScalpRunner, the autonomous
AutoScalpRunner, and the small cross-cutting helpers (feed-mark labelling,
payload compaction, kill-switch state) shared by more than one route group.

Split out of app/main.py (which had grown to 1269 lines / 59 routes) so route
modules under app/api/ can `from ..runtime import scalp_runner, autoscalp, ...`
without importing app.main itself (main.py imports THIS module, not the other
way round -- keep it that way or you get a circular import).

histcap_worker / smart_scalper_scheduler stay wired in main.py: they're
already a clean self-contained router-inclusion pattern
(bind_worker() -> app.include_router()) copied by nothing else here.
"""
from __future__ import annotations

import logging
import os

from fastapi import WebSocket

from . import instruments
from . import market_data
from . import market_hub
from .scalper import ScalpRunner

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------- WebSocket
class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


manager = ConnectionManager()
scalp_runner = ScalpRunner(broadcast=manager.broadcast)

# Publish the shared WS feed so read-only consumers (mathematical_confluence,
# the ranking scanner) pull live index spot from it instead of REST get_quote.
from .feed_registry import set_feed as _set_feed
_set_feed(scalp_runner.feed)

# --- Autonomous PAPER scalper (P7). Shares the ScalpRunner's WS feed; a live
# chain snapshot comes from the read-only market-data SDK. LIVE order routing is
# never reached from here. ---
from .autoscalp.runner import AutoScalpRunner


def _opt_tradingsymbol(symbol, expiry, strike, opt_type):
    """Best-effort AngelOne NFO trading symbol, e.g. NIFTY01SEP2624200CE.
    Returns None if the expiry string is not the expected DDMMMYYYY form —
    the token is the authoritative contract lock, this is only for display/audit."""
    try:
        e = str(expiry or "").strip().upper()
        if len(e) == 9 and e[:2].isdigit() and e[5:].isdigit():   # 01SEP2026
            return f"{str(symbol).upper()}{e[:5]}{e[7:9]}{int(round(float(strike)))}{opt_type}"
    except Exception:
        pass
    return None


def _merge_broker_greeks(sdk, symbol, expiry, chain_rows):
    from broker.angelone import greeks as _gk
    und = str(symbol).upper()
    try:
        cap = (sdk.greek_capabilities() or {}).get(und, {}).get("status")
    except Exception:
        cap = None
    if cap == "UNAVAILABLE":
        for row in chain_rows:
            for side in ("ce", "pe"):
                row[side].setdefault("greeks_source", "UNAVAILABLE")
        return
    try:
        g = sdk.get_option_greeks(und, expiry)
    except Exception:
        return
    if g.get("status") != "OK" or not g.get("rows"):
        src = "UNAVAILABLE" if g.get("capability") == "UNAVAILABLE" else "UNKNOWN"
        for row in chain_rows:
            for side in ("ce", "pe"):
                row[side].setdefault("greeks_source", src)
        return
    gi = _gk.index_greek_rows(g["rows"])
    for row in chain_rows:
        k = row.get("strike")
        for side, ot in (("ce", "CE"), ("pe", "PE")):
            merged = _gk.merge_leg_greeks(row[side], _gk.match_greek(gi, k, ot))
            row[side] = merged


def _autoscalp_chain(symbol, atm, window, market="NSE", expiry_mode="AUTO"):
    """Canonical ATM+/-window chain from the read-only quote SDK (best effort).
    `market` is NSE for index options, MCX for NATURALGAS/CRUDEOIL options-on-
    futures — selection_snapshot + the SDK are exchange-aware. `expiry_mode`
    is AUTO or AUTO_ROLL (skip the 0-DTE contract on expiry day).

    Sourced from market_hub.get_chain() (histcap first, throttled REST
    fallback) rather than a second, independent selection_snapshot() call —
    unifying autoscalp onto the same broker-facing chain read the
    mathematics/ranking surface already uses, instead of two parallel paths
    hitting the broker separately. Output shape is unchanged: still a list of
    {"strike", "ce": {...}, "pe": {...}} dicts, so decide_from_context and
    everything downstream sees byte-identical input regardless of which of
    histcap/REST actually answered."""
    try:
        mkt = str(market or "NSE").upper()
        et = 5 if mkt == "MCX" else 2                       # WS exchange type for the legs
        gc = market_hub.get_chain(symbol, window=window, allow_rest_fallback=True,
                                  expiry_mode=str(expiry_mode or "AUTO"))
        rows = gc.get("chain") or []
        if not rows:
            return []
        expiry = gc.get("expiry")
        out = []
        for r in rows:
            strike = r.get("strike")
            # PHASE 2/4 — greeks + OI provenance now flow through the normalized
            # snapshot (get_option_chain already merged broker greeks and did a
            # batched OI fetch). Carry them onto the strategy leg verbatim; a
            # missing value stays None (never 0), tagged by *_source / oi_status.
            out.append({
                "strike": strike,
                "ce": {"ltp": r.get("ce_ltp"), "oi": r.get("ce_oi"), "oi_chg": r.get("ce_oi_change"),
                       "vol_delta": r.get("ce_volume"), "token": r.get("ce_token"), "exchange_type": et,
                       "iv": r.get("ce_iv"), "delta": r.get("ce_delta"), "gamma": r.get("ce_gamma"),
                       "theta": r.get("ce_theta"), "vega": r.get("ce_vega"),
                       "greeks_source": r.get("ce_greeks_source"),
                       "oi_status": r.get("ce_oi_status"), "oi_source": r.get("ce_oi_source"),
                       "oi_timestamp": r.get("ce_oi_timestamp"),
                       "tradingsymbol": _opt_tradingsymbol(symbol, expiry, strike, "CE"), "expiry": expiry},
                "pe": {"ltp": r.get("pe_ltp"), "oi": r.get("pe_oi"), "oi_chg": r.get("pe_oi_change"),
                       "vol_delta": r.get("pe_volume"), "token": r.get("pe_token"), "exchange_type": et,
                       "iv": r.get("pe_iv"), "delta": r.get("pe_delta"), "gamma": r.get("pe_gamma"),
                       "theta": r.get("pe_theta"), "vega": r.get("pe_vega"),
                       "greeks_source": r.get("pe_greeks_source"),
                       "oi_status": r.get("pe_oi_status"), "oi_source": r.get("pe_oi_source"),
                       "oi_timestamp": r.get("pe_oi_timestamp"),
                       "tradingsymbol": _opt_tradingsymbol(symbol, expiry, strike, "PE"), "expiry": expiry},
            })
        # Fallback: if the chain path did not carry greeks (histcap never
        # captures them; REST with_greeks disabled), fill any still-None leg
        # from one cached get_option_greeks call. Idempotent; capability-aware;
        # no fabrication. Needs a real SDK handle regardless of which path
        # answered the chain itself.
        if out and any(row["ce"].get("delta") is None and row["ce"].get("greeks_source") in (None, "", "UNAVAILABLE")
                       for row in out):
            try:
                from .connectors.angelone import _market_sdk
                sdk = _market_sdk(require_auth=False)
                if sdk:
                    _merge_broker_greeks(sdk, symbol, expiry, out)
            except Exception:
                pass
        return out
    except Exception:
        return []


def _autoscalp_tg(text):
    try:
        from .connectors import telegram
        telegram._send(text, os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception:
        pass


autoscalp = AutoScalpRunner(feed=scalp_runner.feed, chain_provider=_autoscalp_chain,
                            broadcast=manager.broadcast, telegram_fn=_autoscalp_tg,
                            owner=f"{os.uname().nodename}:{os.getpid()}")


# ---------------------------------------------------------------- shared response shaping
def _label_marks(status: dict) -> dict:
    """Add a human-readable `label` to each feed mark (99919000 -> SENSEX).
    Accepts either a runner status (has `feed`) or a bare feed status (has
    `marks`)."""
    fs = status.get("feed") if isinstance(status, dict) and isinstance(status.get("feed"), dict) else status
    marks = (fs or {}).get("marks") if isinstance(fs, dict) else None
    if isinstance(marks, dict):
        for tok, mk in marks.items():
            if isinstance(mk, dict):
                mk.setdefault("label", instruments.label_for_token(tok))
    return status


def _compact(status: dict) -> dict:
    """Shrink a status payload for low-token consumers: collapse per-mark dicts
    to `label: ltp`, replace token arrays with counts, drop the nested config."""
    s = dict(status or {})
    fs = s.get("feed") if isinstance(s.get("feed"), dict) else s
    if isinstance(fs, dict) and isinstance(fs.get("marks"), dict):
        stale = sum(1 for m in fs["marks"].values() if isinstance(m, dict) and not m.get("fresh"))
        fs2 = {k: v for k, v in fs.items() if k not in ("desired_tokens", "active_tokens")}
        fs2["marks"] = {(instruments.label_for_token(t) if str(t).isdigit() else t):
                        round((m or {}).get("ltp", 0), 2) if isinstance(m, dict) else m
                        for t, m in fs["marks"].items()}
        fs2["n_desired"] = len(fs.get("desired_tokens") or [])
        fs2["n_active"] = len(fs.get("active_tokens") or [])
        fs2["stale_marks"] = stale
        if s.get("feed") is fs:
            s["feed"] = fs2
        else:
            s = fs2
    for k in ("config",):
        s.pop(k, None)
    if isinstance(s.get("safeguards"), dict):
        s["safeguards"].pop("config", None)
    return s


def _ks_state():
    try:
        from .execution import killswitch
        return killswitch.state()
    except Exception:
        return {"active": False, "policy": "MONITOR"}
