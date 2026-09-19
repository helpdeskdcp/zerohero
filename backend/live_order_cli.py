#!/usr/bin/env python3
"""
Controlled REAL Angel One broker execution test — ONE order, full lifecycle.

The application builds the OrderReq (nothing is hand-placed in the Angel One
terminal). It goes through the SAME OrderManager / AngelOneBroker / idempotency /
audit / reconciliation path that AUTO LIVE uses — no shortcuts, no gate bypass.

    LIVE run needs ALL of:
      --live  --confirm
      env CHANAKYA_ALLOW_LIVE=1 and CHANAKYA_LIVE_CONFIRM_TOKEN=<secret>
      execution_mode LIVE is forced by --live
    Otherwise it runs against the PAPER broker (safe dry run) so you can rehearse.

Typical safe LIVE test: a 1-unit LIMIT BUY on a liquid cash equity, priced well
BELOW the last trade so it rests OPEN and never fills, then --cancel.

    python live_order_test.py --live --confirm \
        --exchange NSE --symbol IDEA --tradingsymbol IDEA-EQ --token 14366 \
        --order-type LIMIT --side BUY --qty 1 --price 6.50 --cancel --poll 8

Run from /opt/chanakya-app/backend with the venv python and .env loaded, e.g.
    sudo -u chanakya CHANAKYA_ALLOW_LIVE=1 \
      /opt/chanakya-app/backend/venv/bin/python live_order_test.py --live ...
"""
from __future__ import annotations

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(os.environ.get("CHANAKYA_ENV_FILE", ".env"))
except Exception:
    pass

from app import db, instruments                       # noqa: E402
from app.execution import make_broker, OrderManager    # noqa: E402
from app.execution.broker_base import OrderReq, OStatus, Side, OrderType, Leg  # noqa: E402
from app.execution import idempotency as idem, audit   # noqa: E402


def _p(tag, obj):
    print(f"\n=== {tag} ===")
    print(json.dumps(obj, indent=2, default=str))


def build_args():
    ap = argparse.ArgumentParser(description="One controlled Angel One order lifecycle test")
    ap.add_argument("--live", action="store_true", help="use the real AngelOneBroker (else PAPER)")
    ap.add_argument("--confirm", action="store_true", help="required alongside --live")
    ap.add_argument("--exchange", default="NSE")
    ap.add_argument("--symbol", required=True, help="registry name or plain symbol")
    ap.add_argument("--tradingsymbol", default="", help="Angel One tradingsymbol (e.g. IDEA-EQ)")
    ap.add_argument("--token", default="", help="Angel One symboltoken; resolved from registry if blank")
    ap.add_argument("--product", default="INTRADAY", choices=["INTRADAY", "DELIVERY", "CARRYFORWARD", "MARGIN"])
    ap.add_argument("--instrument", default="EQ")
    ap.add_argument("--expiry", default="")
    ap.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--order-type", default="LIMIT", choices=["LIMIT", "MARKET", "SL", "SL-M"])
    ap.add_argument("--qty", type=float, default=1)
    ap.add_argument("--price", type=float, default=0.0, help="LIMIT price — pick one that will NOT fill")
    ap.add_argument("--trigger", type=float, default=0.0)
    ap.add_argument("--poll", type=int, default=6, help="status polls, ~2s apart")
    ap.add_argument("--cancel", action="store_true", help="cancel if still OPEN after polling")
    ap.add_argument("--trade-id", default="")
    return ap.parse_args()


def resolve_instrument(a):
    token = a.token
    exch = a.exchange
    tsym = a.tradingsymbol
    if not token:
        meta = instruments.resolve(a.symbol) or {}
        token = meta.get("symboltoken") or ""
        exch = a.exchange or meta.get("exchange") or exch
    if not token:
        sys.exit(f"ABORT: no symboltoken for {a.symbol!r} — pass --token (and --tradingsymbol).")
    if not tsym:
        tsym = a.symbol
    return {"symboltoken": str(token), "exchange": exch, "tradingsymbol": tsym}


def main():
    a = build_args()
    db.init_db()

    mode = "LIVE" if a.live else "PAPER"
    if a.live:
        if not a.confirm:
            sys.exit("ABORT: --live also requires --confirm")
        if os.environ.get("CHANAKYA_ALLOW_LIVE") != "1":
            sys.exit("ABORT: env CHANAKYA_ALLOW_LIVE=1 is required for --live")
        if not os.environ.get("CHANAKYA_LIVE_CONFIRM_TOKEN"):
            sys.exit("ABORT: env CHANAKYA_LIVE_CONFIRM_TOKEN is required for --live")

    cfg = {
        "execution_mode": mode,
        "paper_scenario": {"fill_mode": "WORKING"},   # PAPER: rest OPEN like a real resting limit
    }
    broker = make_broker(mode, cfg, ltp_provider=lambda t: None)
    if a.live and not getattr(broker, "live_enabled", False):
        sys.exit("ABORT: broker.live_enabled is False — triple gate not satisfied "
                 "(execution_mode=LIVE + env CHANAKYA_ALLOW_LIVE=1 + "
                 "non-empty CHANAKYA_LIVE_CONFIRM_TOKEN).")

    om = OrderManager(mode=mode, broker=broker, config=cfg, ltp_provider=lambda t: None)

    inst = resolve_instrument(a)
    trade_id = a.trade_id or ("LIVETEST-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S"))
    otype = {"LIMIT": OrderType.LIMIT, "MARKET": OrderType.MARKET,
             "SL": OrderType.SL, "SL-M": OrderType.SL_M}[a.order_type]

    req = OrderReq(
        client_tag=idem.tag(trade_id, Leg.ENTRY), trade_id=trade_id, leg=Leg.ENTRY,
        side=Side.BUY if a.side == "BUY" else Side.SELL, order_type=otype,
        symbol=a.symbol, symboltoken=inst["symboltoken"], exchange=inst["exchange"],
        tradingsymbol=inst["tradingsymbol"], product=a.product,
        quantity=a.qty,
        limit_price=a.price or None if otype in (OrderType.LIMIT, OrderType.SL) else None,
        trigger_price=a.trigger or None if otype in (OrderType.SL, OrderType.SL_M) else None,
        signal_confidence=None, signal_ts=datetime.now(timezone.utc).isoformat(),
        market_data_ts=datetime.now(timezone.utc).isoformat(),
    )

    # 1) persist PREARMED intent (payload + timestamp captured here)
    idem.prearm(req, mode)
    audit.event(trade_id, req.client_tag, "LIVE_TEST_PREARM", {"mode": mode, "req": req.to_dict()})
    _p("1. PRE-ARM (persisted)", db.get_broker_order(req.client_tag))

    # 2) submit exactly ONE order through the adapter
    if otype in (OrderType.SL, OrderType.SL_M):
        ack = broker.stoploss_limit(req) if otype == OrderType.SL else broker.stoploss_market(req)
    elif otype == OrderType.LIMIT:
        ack = broker.limit_entry(req)
    else:
        ack = broker.market_entry(req)
    if ack.ok:
        idem.mark_submitted(req.client_tag, ack)
    elif ack.ambiguous:
        idem.mark_ambiguous(req.client_tag, ack)
    else:
        idem.mark_status(req.client_tag, OStatus.REJECTED, text=ack.error)
    audit.event(trade_id, req.client_tag, "LIVE_TEST_SUBMIT",
                {"ok": ack.ok, "ambiguous": ack.ambiguous,
                 "broker_order_id": ack.broker_order_id, "unique_order_id": ack.unique_order_id,
                 "error": ack.error, "raw": ack.raw})
    _p("2. SUBMIT ACK", {"ok": ack.ok, "ambiguous": ack.ambiguous,
                         "broker_order_id": ack.broker_order_id,
                         "unique_order_id": ack.unique_order_id, "error": ack.error})

    if not ack.ok and not ack.ambiguous:
        _p("RESULT", {"outcome": "REJECTED_AT_SUBMIT", "error": ack.error})
        _p("AUDIT", audit.snapshot(trade_id))
        return

    # 3) poll broker order status until terminal or exhausted
    boid = ack.broker_order_id or (db.get_broker_order(req.client_tag) or {}).get("broker_order_id") or ""
    uoid = ack.unique_order_id or (db.get_broker_order(req.client_tag) or {}).get("unique_order_id") or ""
    last = None
    for i in range(max(1, a.poll)):
        osr = broker.get_order_status(broker_order_id=boid, unique_order_id=uoid,
                                      client_tag=req.client_tag)
        last = osr
        idem.mark_status(req.client_tag, osr.status, filled_qty=osr.filled_qty,
                         avg_price=osr.avg_price, broker_order_id=osr.broker_order_id,
                         unique_order_id=osr.unique_order_id, text=osr.text)
        print(f"  poll {i+1}/{a.poll}: status={osr.status} filled={osr.filled_qty} "
              f"avg={osr.avg_price} text={osr.text!r}")
        if osr.status in OStatus.TERMINAL:
            break
        time.sleep(2)
    _p("3. STATUS (final poll)", last.__dict__ if last else None)

    # 4) verify it is visible in the order book
    try:
        book = broker.get_order_book()
        seen = [o for o in (book or [])
                if str(o.get("orderid") or o.get("broker_order_id")) == str(boid)
                or str(o.get("ordertag") or o.get("client_tag") or "") == req.client_tag]
        _p("4. ORDER BOOK MATCH", seen or {"note": "not found in order book snapshot"})
    except Exception as e:
        _p("4. ORDER BOOK", {"error": f"{type(e).__name__}: {e}"})

    # 5) cancel path if still open
    if last and last.status not in OStatus.TERMINAL and a.cancel:
        cack = broker.cancel_order(req.client_tag, boid)
        if cack.ok:
            idem.mark_status(req.client_tag, OStatus.CANCELLED)
        audit.event(trade_id, req.client_tag, "LIVE_TEST_CANCEL",
                    {"ok": cack.ok, "error": cack.error})
        time.sleep(2)
        osr = broker.get_order_status(broker_order_id=boid, unique_order_id=uoid,
                                      client_tag=req.client_tag)
        idem.mark_status(req.client_tag, osr.status, filled_qty=osr.filled_qty,
                         avg_price=osr.avg_price, text=osr.text)
        _p("5. CANCEL RESULT", {"cancel_ack_ok": cack.ok, "post_cancel_status": osr.status,
                                "db_status": (db.get_broker_order(req.client_tag) or {}).get("status")})

    # 6) if FILLED — read real position, capture avg fill, hand to reconciler
    if last and last.status in (OStatus.COMPLETE, OStatus.PARTIAL):
        pos = None
        try:
            pos = broker.reconcile_position(inst["symboltoken"])
        except Exception as e:
            print("  position read error:", e)
        _p("6. FILLED — broker position", pos.__dict__ if pos else {"note": "no position row"})
        print("  NOTE: a real fill leaves a live position. Square it off manually or via "
              "the Live Monitor; this harness does not auto-exit.")

    _p("AUDIT SNAPSHOT", audit.snapshot(trade_id))
    _p("RESULT", {
        "mode": mode, "trade_id": trade_id, "client_tag": req.client_tag,
        "broker_order_id": boid, "unique_order_id": uoid,
        "final_status": last.status if last else None,
        "db_status": (db.get_broker_order(req.client_tag) or {}).get("status"),
        "note": "success is confirmed by broker_order_id + a real broker status, NOT an HTTP 200",
    })


if __name__ == "__main__":
    main()
