# ORDERFLOW v2 — M0: SnapQuote capture status

_started 2026-09-09 ~12:10 IST_

## M0 goal (from ORDERFLOW_ENGINE_V2_SPEC.md Part 10)

`snapquote_ticks` gains >= 8000 rows/session for the P0 instrument (NIFTY
future), >= 5 consecutive sessions, quality gates (Part 1.3) green.

## Root cause the capture was dead (0 rows despite L2_CAPTURE_ENABLED=1)

`l2capture/worker._session()` opened the WebSocket with
`websockets.connect(additional_headers=...)` guarded by `try/except TypeError`
falling back to `extra_headers`. `websockets` does **not** validate that kwarg
until the connection actually opens (inside `async with`), well past the
try/except — so on the installed **websockets 13.1** (`additional_headers` is
14.0+) every session raised `TypeError` immediately, the worker never reached
`store.start_run()`, and the DB stayed empty.

Fix (commit `cd9cf7f`): choose the header kwarg from `websockets.__version__`
before calling `connect()`. Deployed via `systemctl restart oi-dashboard`.

## Verified working (post-restart, live session)

| instrument | rate | fields |
|---|---|---|
| **NIFTY future** (P0) | **~2.4 packets/s** (293 in a 2-min window) → ~50k/session projected | ltp, ltq, cumulative volume, tot_buy_qty, tot_sell_qty, oi, bid/ask, bid_qty/ask_qty, full 5-level depth_json — all populated |
| CRUDEOIL future | ~0.8/s | depth_json populated; top-level `bid` NULL on MCX → reader must fall back to `depth_json[0]` |
| NATURALGAS future | **stuck at 1 stale packet** — MCX natgas contract/token/liquidity issue; investigate before relying on it for regime diversity (NIFTY + CRUDEOIL still cover it) |

Autoscalp / scalp feed unaffected by the restart (armed, running, tick age 0.1 s).
`live_trading` untouched. Capture runs inside the always-on service (code in the
checkout) and continues automatically each session.

## M0 remaining

- Accrue **>= 5 consecutive trading sessions** of NIFTY-future capture.
- Then run the Part-1.3 quality gates (packets/session, coverage %, clean-book %,
  volume monotonicity, independent-session count).
- Follow-up: fix or drop NATURALGAS capture; confirm the CRUDEOIL L1 fallback.

Nothing modelled until M1 (>= 60 quality sessions).
