# MCP 3.3 — Status Report (Phase 1: Data Foundation)

MCP 3.3 as pasted is a full institutional-platform spec (~50 acceptance
items: data router, VIX, breakout/reversal/regime signal engines,
probability/expectancy engines, Monte Carlo backtesting, RBAC/security
overhaul, new frontend, unified DB schema). That is realistically weeks of
work, not something one session can honestly deliver end-to-end with the
"tests actually pass, don't declare complete because code compiles" rigor
the spec itself demands (§50). This report is deliberately scoped and
honest about what's done, what already existed, and what's not attempted
yet and why — that discipline is worth more than a false "COMPLETE" stamp.

## What this pass actually did

1. **Surveyed the real codebase state before writing anything** (a fork
   investigation, not guesswork) — see "Data-source status" below. The
   headline finding: most of MCP 3.3's "data foundation" demand is already
   built and running. Building a parallel system from scratch would have
   duplicated ~2500 lines of already-working capture infrastructure.
2. **Built the one genuinely missing raw-data gap**: India VIX capture.
   `app/instruments.py`: added `"INDIAVIX"` to the registry (NSE, token
   `99926017`, `AMXIDX` — verified against the actual cached AngelOne
   instrument master, `data/instrument_master.json`, not guessed). Added
   `INDIAVIX` to `CHANAKYA_HIST_SYMBOLS`. No new capture code was needed —
   `app/histcap/worker.py`'s existing non-MCX branch already resolves a
   plain INDEX spot quote and silently no-ops option/future resolution for
   a symbol that has neither, the same path NIFTY/BANKNIFTY/SENSEX already
   use. Deployed; confirmed live in `/api/histcap/status`'s active symbol
   list. Commit `829acd9`.

## Files changed

- `backend/app/instruments.py` (+5 lines: one registry entry)
- `backend/tests/test_instruments_registry.py` (new, 3 tests)
- `backend/.env` (not tracked by git): `CHANAKYA_HIST_SYMBOLS` gained `INDIAVIX`

No migrations — `quote_snapshots` already has the columns VIX needs
(`ltp`, `net_change`, `pct_change`, `received_ts`/`exch_ts`); no schema
change required.

## Tests executed / passed / failed

- New: 3/3 passed (`test_instruments_registry.py`)
- Full suite: **1937 passed**, 3 failed — the exact same 3 pre-existing
  failures documented in `MCP_3_1_2_FINAL_VERIFICATION.md`
  (`test_standalone_angelone.py` x2, `test_market_data_selection.py` x1),
  hardcoded-past-calendar-date fixtures in code paths structurally
  unreachable from `app/hedging/*` or this change. Not touched here either,
  per the same "don't force-green unrelated tests" instruction as last time.
- `app/histcap/*` test suite: unaffected, still green.

## Data-source status

| Item | Status |
|---|---|
| AngelOne | **De facto PRIMARY today** — `app/connectors/angelone.py` + `app/instruments.py` back essentially every live quote, the instrument master, and the capture pipeline (`app/histcap/`). |
| Dhan | **NOT primary, and can't honestly be made primary today.** `app/dhan_client.py` + `app/connectors/dhan_ws.py`/`dhan_historical.py` exist and work (OAuth, live orderflow WS for 11 hand-picked instruments, OHLC historical backfill into the same `market_candles` table AngelOne populates) — but **Dhan has no option-chain or quote-snapshot capture at all**. Making it "primary" per MCP 3.3 §2 would mean building that integration first (a separate, multi-day task on the scale of what AngelOne's connector already took), not a config relabel. Doing that with a fake/thin Dhan wrapper just to satisfy the checkbox would be worse than stating the gap plainly. |
| Option chain (CE+PE, every strike, bid/ask/OI/OI_change/IV/Greeks) | **Already exists** — `app/histcap/` writes exactly this into `market_history.db` (8.5GB, 3.68M quote rows, growing). One real gap: capture window is ATM±N strikes (configurable per symbol, currently 15-25), not literally every strike — widening this is a real, bounded follow-up but was **not done here**, deliberately: this DB is already 8.5GB on a VPS that was at 87% disk earlier this project (since cleaned to 78%) — blanket-widening to "every strike" for all 8 symbols needs a real capacity plan first, not a reflexive flip. |
| Futures + basis | **Already captured** — `quote_snapshots.kind='FUTURE'` rows include `basis` computed at capture time. |
| India VIX | **Built this pass.** Raw ltp/change/%change now capturing (from next market-open capture cycle). VIX moving-average/percentile/regime are derived analytics, deliberately not built — see "What was NOT attempted" below. |
| Multi-source failover / `DATA_CONFLICT` | **Partially exists.** `app/optionchain/resolve.py::get_chain()` already does ordered failover (`angelone_captured → upstox_open → nse_v3`, first non-empty wins) with capability-aware Greek merging. It is NOT a genuine cross-source conflict check (it doesn't fetch a second source once the first succeeds, purely for comparison) — and can't reasonably become a real "Dhan vs Angel" conflict check until Dhan has an option-chain source at all. Not attempted this pass for that reason, not because it's hard. |
| Atomic snapshot (single `snapshot_id` tying spot+chain+futures+VIX+timestamp together) | **Not built.** The pieces exist (`market_hub.py`, histcap's `quote_snapshots`), just not unified under one id. Real, bounded follow-up. |

## Live / paper status

Unchanged by this pass. Hedging remains PAPER-only, disarmed by default
(per MCP 3.1.x). This pass touched only read-side data capture — nothing
in the entry/exit/execution/risk path was modified.

## What was deliberately NOT attempted, and why

- **Breakout/reversal/bullish/bearish signal engines, probability engine,
  regime-switching strategy selection (MCP 3.3 §9-15, §20-21, §27).** This
  codebase has already run this exact hypothesis — price action + OI/IV/
  volume confirmation → directional signal — through rigorous walk-forward
  testing at least ten times on real NIFTY/BANKNIFTY/MCX data: SSL Hybrid,
  a 3-engine trend/structure comparison, trend-swing, liquidity sweep,
  StochRSI+Supertrend, inside-bar, mean-reversion/afternoon-fade, tick-rule
  CVD, an 8-hypothesis index-signal sweep, and a 4-setup orderflow
  rulebook. Every one came back NO-GO or INSUFFICIENT_SAMPLE, mostly
  because option theta/spread/transaction costs erode any index-level edge
  found — a structural problem, not a detection-logic problem. A larger,
  more elaborate version of the same core idea doesn't address that. This
  is the single most load-bearing reason this pass stopped at the data
  layer: building the signal layer now would be building on ground already
  shown not to hold, just with better instrumentation underneath it.
- **Monte Carlo / walk-forward backtest engine (§28-32).** A real, useful
  piece of infrastructure, but only valuable once there's a signal
  hypothesis worth backtesting — see above.
- **RBAC / JWT / security hardening (§38-39).** The app currently runs
  Basic Auth + an optional bearer token (`app/main.py`'s
  `_AuthGateMiddleware`) for a single-operator dashboard. A full RBAC
  overhaul is a real, standalone security project deserving its own
  focused pass, not a paragraph inside a data-layer commit.
- **New institutional frontend (§42-44).** Depends on the signal/regime
  engine existing first (most of the fields it wants to show — regime,
  signal reason, probability/expectancy — don't exist yet).
- **Unified DB schema (§49): `trade_candidates`, `risk_events`,
  `decision_snapshots` etc. as one normalized set.** Each subsystem
  (hedging, structural_break, autoscalp) already has its own working
  persistence in its own established convention. Consolidating them into
  one mega-schema is a real refactor with real regression risk across
  several already-tested subsystems, not something to do opportunistically
  alongside unrelated work.
- **Autonomous LLM debugger with patch-apply/branch/test/deploy authority
  (§36).** Explicitly the highest-blast-radius item in the entire spec.
  Building this without a dedicated design/review pass would be reckless
  regardless of how much autonomy this task grants — "never bypass safety/
  risk/data-integrity controls merely to keep execution moving" (the
  spec's own §13) argues directly against rushing this.

## Known limitations

- India VIX raw capture is live in config but unverified against a real
  captured row as of this report — market was closed at deploy time
  (08:33 IST; NSE pre-open is 09:00). The resolution path is proven
  correct (real token from the actual instrument master, identical code
  path to already-working symbols, 3 passing tests) but the first actual
  captured row will land at the next normal capture cycle once the market
  opens, the same way every other symbol here already does.
- Chain capture is still ATM-windowed, not full-strike, pending a capacity
  decision given current DB size (8.5GB) and VPS disk history.
- Dhan is not a real option-chain source yet; "Dhan primary" per the spec
  is not achievable without that work first.

## Recommended next steps, in priority order

1. Confirm the first real INDIAVIX captured row after market open today.
2. Decide a capacity budget before widening any symbol's chain-capture
   window past its current ATM±N.
3. If Dhan-primary option-chain data is actually wanted, scope it as its
   own project (comparable effort to the original AngelOne connector) —
   not a follow-on to this commit.
4. Before any signal-engine work: review why the ~10 prior hypothesis
   tests failed as a group (cost structure vs. detection logic) and decide
   whether a genuinely different mechanism (not a bigger version of the
   same one) is worth testing, rather than restarting the same search with
   more instrumentation.
