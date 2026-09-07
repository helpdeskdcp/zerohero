# Unified Signal Dashboard — architecture

**Read-only aggregation. No blending / new scoring logic. No order path. No change
to any engine, frozen H1/H7, calibration, or cron. `live_trading` stays false.**

## Goal

One screen, one row per traded symbol, showing **every engine's own current read
side-by-side** — instead of hunting across 10 tabs. The old detail/control/
research pages are **kept and still reachable** (nav is just re-ordered), because
several are operational (arm/disarm the engine, health, trade ledger).

## Data flow

```
GET /api/signals/unified   (app/signals_dashboard.py, 15 s TTL cache)
        |
        +-- AutoScalp read  <- app.hcs.engine.evaluate()
        |      (which itself reads the latest live_market_snapshots row per symbol:
        |       decision, entry/SL/T1/T2, regime, signal_type, confidence, probability)
        |
        +-- HCS verdict     <- same app.hcs.engine.evaluate() result
        |      (a_plus, hcs_score, adaptive_probability, top veto, reason)
        |
        +-- Confluence      <- app.mathematical_confluence.api.api_market_map()
        |      (signal_type, CE/PE direction, confluence_score, spot,
        |       nearest support/resistance, pivot)
        |
        +-- Order-flow      <- app.orderflow.service.h1h7_state(sym, latest_date, only_last)
               (H1/H7 shadow state, action AVOID/NO_ACTION, research_status)

  -> one row per symbol:
       { symbol, autoscalp{...}, hcs{...}, confluence{...}, orderflow{...},
         agreement, agreement_votes }
```

Every sub-source is wrapped so **one failure can't 500 the dashboard** — a bad
source returns `{status:"unavailable"}` / lands in `errors{}`, the rest still
render.

## `agreement` — a DISPLAY tally, not a signal

`agreement` counts how many of {AutoScalp direction, Confluence CE/PE, Order-flow
state} point the same way. A source only casts a vote when it has a *live* read:
AutoScalp votes only on `BUY_CE` / `BUY_PE` (a `NO_TRADE` row's latent
`signal_type` is ignored), and Confluence votes only when its `signal` is not
`NO_TRADE` / `NONE`.

- `BULLISH` / `BEARISH` — all non-neutral votes agree
- `MIXED` — votes disagree
- `NEUTRAL` — no engine has a directional read

It is **not** a combined score and **nothing trades off it**. AutoScalp is the
only tradeable signal; HCS and order-flow are SHADOW; confluence is analysis
(`confluence_score` is explicitly uncalibrated). A validated blended meta-signal
does not exist (see `HCS_FORWARD_TEST.md` — the HCS A+ gate had negative lift).

## Frontend

- New default view `#view-signalshub` (`loadSignalsHub()` in `app.js`) — one
  `.rcard` per symbol, 4 columns (AutoScalp / HCS / Confluence / Order-flow) +
  an agreement badge. Refresh button; 15 s server cache.
- Its own `.sig-grid` (not the shared 3-col `.research-grid`, which would orphan
  the 4th panel): 4 cols ≥1000px, 2 cols ≥560px, 1 col below, with column
  rules between engines. Directional tint (`pos`/`neg`) and the agreement pill
  are scoped under `#signalshubGrid` — no bare `.pos`/`.neg` rule exists.
- Nav re-ordered into three groups:
  - **primary:** Signals (new), Auto-Scalp (engine control — kept)
  - **operational:** Live Monitor, Paper Trades, System & Health (kept)
  - **analysis / research:** Overview, Signal Ledger, Math Scalper, Order Flow,
    Research, Scalping, Run Pipeline (kept, moved down)
- Mobile tab bar trimmed to: Signals, Auto, Monitor, Trades, Health.
- **No HTML section or `loadX()` function was deleted** — only nav buttons moved.
  Every old page is one click away.

## Files

| file | change |
|---|---|
| `app/signals_dashboard.py` | **new** — the aggregator + `GET /api/signals/unified` |
| `app/main.py` | +1 guarded `include_router` block |
| `frontend/index.html` | new `#view-signalshub` section; nav + tabbar re-ordered |
| `frontend/static/js/app.js` | `loadSignalsHub()`; default view `signalshub` |
| `frontend/static/css/style.css` | `.nav-sep` label |
| `tests/test_signals_dashboard.py` | **new** — 4 tests (classifier, shape, cache, fault-tolerance) |

657 backend tests pass. `git diff` shows `app/engines`, `app/autoscalp`,
`app/backtest`, `app/orderflow`, `app/execution`, `app/mathematical_confluence`
all untouched.
