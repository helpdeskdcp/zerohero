"""
Pipeline glue — the ONLY place the Signal/Scalp pipelines touch the order
adapter. Additive and opt-in: unless `req["execution"]["enabled"]` is set the
pipelines behave exactly as before.

    req["execution"] = {
        "enabled": bool,
        "mode": "PAPER" | "SHADOW" | "LIVE",     # default PAPER
        "manager": OrderManager | None,          # runner passes its singleton
        "config": {...},                         # exec_* / auto_exit / ...
        "ltp_provider": callable(token)->float,  # feed.get_ltp
        "instrument": {...}, "symboltoken": "...", "tradingsymbol": "...",
        "market_data_ts": "...",
    }

`run_execution` prearms + submits an APPROVED contract and returns an
`execution` dict for the contract / response. It never raises and never blocks
on anything but the single broker submit call.
"""
from __future__ import annotations


def run_execution(contract: dict, req: dict, *, connector: dict | None = None):
    ex = (req or {}).get("execution") or {}
    if not ex.get("enabled"):
        return None
    try:
        from .order_manager import OrderManager
        from .staleness import Clocks

        om = ex.get("manager")
        if om is None:
            om = OrderManager(mode=ex.get("mode", "PAPER"),
                              config=ex.get("config") or {},
                              ltp_provider=ex.get("ltp_provider"))
        mdts = ex.get("market_data_ts") or (connector or {}).get("fetched_at")
        payload = {
            **contract,
            "trade_id": contract.get("trade_id") or contract.get("signal_id"),
            "symboltoken": ex.get("symboltoken") or req.get("symboltoken"),
            "tradingsymbol": ex.get("tradingsymbol") or req.get("tradingsymbol"),
            "market": contract.get("market") or req.get("market"),
            "market_data_ts": mdts,
        }
        state = om.prearm(payload, instrument=ex.get("instrument"))
        clk = Clocks(signal_ts=contract.get("created_ts"), market_data_ts=mdts,
                     last_reconcile_ts=contract.get("created_ts"))
        res = om.submit(state, clocks=clk)
        return {
            "enabled": True, "mode": om.mode, "status": res.status,
            "trade_id": state.trade_id, "reasons": res.reasons,
            "provisional": state.is_provisional, "monitor_qty": state.monitor_qty,
            "state": state.to_dict(),
            "monitor": res.monitor.snapshot() if res.monitor else None,
        }
    except Exception as e:                       # noqa: BLE001 — execution must never break a signal
        return {"enabled": True, "status": "ERROR", "error": f"{type(e).__name__}: {str(e)[:200]}"}
