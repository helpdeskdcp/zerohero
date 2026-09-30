# MCP 3.1.1 — Phase 0 Audit (mandatory, pre-code)

Scope: the pasted "FINAL HARDENING PATCH" spec (63 sections). Per the spec's
own text this is an ADDITIVE hardening patch on top of MCP 3.0/3.1 — this
document is read-only reconnaissance, no code was changed to produce it.
Builds on `ARCHITECTURE_AUDIT.md` / `DEPENDENCY_MAP.md` / `DUPLICATE_LOGIC.md`
/ `RISK_GAP_REPORT.md` (the MCP 3.0 Phase 1 audit) rather than repeating them
— this file only covers what that pass didn't, or what changed since
(2026-09-29 hedging fixes: file-lock-in-scan(), capital reconcile/resync,
live per-leg quotes, SELL/BUY badges).

The only autonomous position/order engine this spec's language ("hedge",
"primary/hedge leg", "SELL 300 CE / BUY 375 CE", "arm/disarm") maps onto is
**`app/hedging/`**. AutoScalp (`app/engines/scalp_strategy.py`) and the LIVE
order adapter (`app/execution/`) are separate systems, referenced only where
the spec explicitly asks for shared/parity logic (§19, §42).

---

## 1–2. Position manager / Order manager

**EXISTING**
- `app/hedging/position.py` — `HedgePosition` dataclass, `open_position()`,
  `close_position()`, `check_exit()` (priority: EMERGENCY > STOP_LOSS >
  PROFIT_TARGET > TIME_CUTOFF, position.py:10-14, explicit and documented),
  `emergency_exit_all()`. Single JSON blob persisted via
  `db.get_setting("hedging_open_positions")` (position.py:26,82-92).
- Only two position states: `OPEN` / `CLOSED` (position.py:47). No PENDING,
  PARTIAL, HEDGING, EXIT_PENDING, RECONCILIATION_REQUIRED, ORPHAN states —
  every position is opened and fully filled in one synchronous call
  (`open_position()`), so intermediate states genuinely don't exist yet in
  this PAPER-only, single-fill design.
- There is **no order manager for hedging** — no order object, no order
  lifecycle, at all. A "trade" is a single synchronous state transition
  (`_capital.allocate()` + dataclass write), not an order that gets placed,
  acknowledged, and filled. `app/execution/order_manager.py` is a full,
  separate order-lifecycle manager (idempotency, broker ack, fills,
  reconciliation) built for the *other*, LIVE-gated feature — hedging does
  not use it and was never wired to it.

**MISSING**
- Canonical multi-state position state machine (spec §1): PLANNED /
  PENDING_ENTRY / PARTIAL / EXIT_PENDING / PARTIAL_EXIT / HEDGING /
  REPAIRING / RECONCILIATION_REQUIRED / ORPHAN / FAILED all absent. Adding
  these meaningfully requires an order manager first (state transitions
  need something to transition *through*); bolting states onto the current
  atomic open/close model would be state theater, not a real state machine.
- Decision snapshot persistence (spec §48: spot/chain/OI/greeks/candidates/
  rejected-candidates/model-output at entry time) — **absent**. Today only
  the final decision reaches storage (`HedgePosition` fields) and a
  free-text Telegram message (runner.py:194-202); the full evaluation
  (`evaluate_candidates()`'s rejected candidates, `primary_selector`'s
  losing side) is computed then discarded, not logged anywhere.
- `structure_id` / `decision_id` fields (spec §38, §48) — absent. Duplicate-
  position protection today is coarse: `max_positions_per_symbol` count
  only (runner.py:150-151), not an actual structure/decision identity check.

**DUPLICATED**
- None found this pass — `_current_quotes()` duplication between
  `hedging_routes.py` and `runner.py` was already found and removed
  2026-09-29 (see `DUPLICATE_LOGIC.md`).

**BROKEN / CONFLICTING**
- None new found.

**UNSAFE**
- `db.get_setting`/`set_setting` (db.py:857,864) round-trips the *entire*
  positions dict as one JSON blob on every read-modify-write
  (`_save_all()`, position.py:91-92) — a non-atomic read-modify-write. This
  is the exact mechanism already implicated in the 2026-09-29 "position
  record vanished" incident (see `runner.py:37-49`'s own docstring and
  `RISK_GAP_REPORT.md` item 1). The file-lock fix (`runner.py` `scan()`)
  mitigates the *concurrent-scan* trigger but the underlying non-atomic
  storage primitive is unchanged — any other future caller that reads-
  modifies-writes this same key outside `scan()`'s lock (e.g. a manual
  script, a future admin route) can reproduce the same class of loss.

---

## 3. Broker adapters

**EXISTING**
- `app/connectors/angelone.py::fetch_market_quote(exchange, symboltoken)` —
  genuinely live (seconds-fresh) LTP, used by `runner._live_leg_premium()`
  (runner.py:217-234). Exchange allowlist `NSE/NFO/MCX/BSE/BFO` (fixed
  2026-09-29 to add BFO for SENSEX/BANKEX).
- Separate, more complete broker-adapter layer exists at
  `app/execution/broker_base.py` / `angelone_broker.py` / `paper_broker.py`
  / `shadow_broker.py` — built for the LIVE order-adapter feature, triple-
  gated OFF. Hedging does **not** use or duplicate this; it only reads
  quotes via the simpler `connectors.angelone` module.

**MISSING**
- No broker-side order placement path for hedging at all (confirmed: no
  call to any order-submission function anywhere in `app/hedging/`) — this
  matches the spec's own PAPER-only framing (§18, §41) and is not a gap
  given the current mandate, just noted so a future LIVE pass knows there
  is nothing to "unwire" — it has to be built, not toggled.
- No broker margin API (`risk.py:18-24`'s `margin_note` explicitly documents
  this: `max_loss_per_lot` stands in for real SPAN margin, "never presented
  as an exact broker SPAN number"). Spec §16 wants `broker_margin` tracked
  as authoritative when available — not possible without this API.

**CONFLICTING**
- None.

---

## 4. Market-session / calendar logic

**EXISTING**
- `app/market_calendar.py` is a real, already-built, per-exchange session
  module: `segment_status()`/`is_trading()` for NSE/MCX/BSE with distinct
  windows (NSE 09:15–15:30, MCX 09:00–23:30, BSE 09:15–15:30), weekend +
  holiday-file suppression (`market_calendar.py:37-101`). This satisfies
  most of spec §3's ask (instrument-specific session resolution) — it
  already exists, just isn't wired into hedging (see UNSAFE below).

**MISSING / UNSAFE — highest-priority finding of this audit**
- `app/hedging/runner.py` **never imports `market_calendar`** (verified:
  `grep -n "market_calendar\|import" app/hedging/runner.py` shows no such
  import). Hedging has **zero in-code market-open/session-valid check**.
  The only gate against off-hours trading is the **cron schedule itself**:
  `*/5 8-15 * * 1-5` (`scripts/hedging_scan_cron.sh`, plus its own
  weekend-only check `[ "$DOW" -ge 6 ]`, no holiday check). This means:
  1. **No trading-holiday gate at all for hedging.** On an NSE holiday that
     falls on a weekday, the cron fires anyway, `evaluate_symbol()` calls
     `get_chain(..., allow_network=False)` against whatever was last
     captured, and — if armed — **can open a position using stale/holiday
     chain data with no code-level check that the market is actually
     closed.** `market_calendar.is_holiday()` exists and is not consulted.
  2. **MCX is structurally unreachable for entries and exits after 15:59
     IST**, despite `_OPTION_RESOLVER`/`_SYMBOL_EXCHANGE` explicitly
     supporting `CRUDEOIL`/`NATURALGAS` (runner.py:117-122, 210-214). If a
     user adds either to `cfg["symbols"]` (`DEFAULT_CONFIG` doesn't include
     them today, so this is currently latent, not active), the cron's
     `8-15` hour range means: no entries can ever be evaluated during MCX's
     15:30–23:30 window, **and any MCX position opened before 16:00 gets
     zero further exit checks for the rest of its live MCX session** — no
     STOP_LOSS, no PROFIT_TARGET, no TIME_CUTOFF enforcement from 16:00 to
     23:30, because `check_exits()` only ever runs inside a cron tick that
     never fires that late. This is a real, structural risk gap that would
     activate the moment MCX symbols are added to the config, not a
     hypothetical.
  3. `position.py:28`'s `DEFAULT_EXIT_CUTOFF_IST = dtime(15, 15)` is a
     single hard-coded cutoff applied identically to every symbol
     regardless of exchange — exactly the anti-pattern spec §4 calls out
     ("NOT simply: current_time > fixed_time... must be based on
     instrument/exchange/segment"). Harmless today only because
     `DEFAULT_CONFIG["symbols"]` happens to be all-NSE/BSE (same close
     time); breaks silently for MCX the same way (2) does.

---

## 5. Option-chain engine

**EXISTING** — `app/optionchain/` (data layer, `resolve.get_chain()`,
analytics) is mature and shared correctly: hedging's `runner.py` calls the
same `get_chain(symbol, "AUTO", allow_network=False)` the option-chain
frontend view uses, no duplicate chain-fetch logic. `OptionLeg` (chain.py:58-73)
already carries full greeks (`iv`, `delta`, `gamma`, `theta`, `vega`) —
confirmed by reading the dataclass, not assumed.

**MISSING** — greeks are *modeled* in the schema but barely *used*:
`primary_selector.py` uses OI only; `selector.py` uses OI/volume/spread/
cost-vs-credit and `delta` (only for the reported `net_delta_exposure`, not
as a filter/score); `gamma`/`theta`/`vega`/`iv` are read by nothing in
`app/hedging/`. This is the gap spec §8's `STRIKE_SCORE` wants closed —
the data pipe exists, the scoring logic doesn't.

---

## 6–7. Strike-selection / premium-selection logic

**EXISTING**
- Hedge-leg selection (`selector.py`) is genuinely multi-factor and
  evaluates every real chain candidate (not nearest-strike): OI, volume,
  spread%, cost-vs-credit ratio, risk-reduction — real gates, real
  rejection reasons per candidate (selector.py:106-149). Winner = cheapest
  accepted candidate (selector.py:169-173), documented as a simple,
  deliberate Phase-1 rule, not a blended score.
- Primary-leg selection (`primary_selector.py`) = largest qualifying OI
  wall ≥ `min_distance_pct` OTM (primary_selector.py:35-53). Simple,
  documented, NOT backtested (primary_selector.py:14).

**CONFLICTING**
- **Asymmetric vetting between the two legs.** The hedge leg is checked
  for spread%, volume, and liquidity before acceptance (selector.py:106-119).
  The **primary (sold) leg has none of these checks** — `_best_wall()`
  filters only on OI and distance (primary_selector.py:35-53), no
  spread/volume gate at all. A sold leg with a wide spread or thin volume
  can be selected as primary today; the exact same defect on the hedge
  side would be rejected. Given the primary leg is the one carrying
  unbounded downside pre-hedge, this asymmetry is backwards from a risk
  standpoint.

**MISSING**
- No `STRIKE_SCORE` combining distance/moneyness/greeks/IV/probability/
  slippage-estimate (spec §8) — current logic is gate-then-cheapest, not
  scored-and-ranked. No `probability_estimate` or `slippage_estimate`
  anywhere in hedging.
- No dedicated premium-selection module distinct from strike selection —
  they're the same pass in `selector.py`/`primary_selector.py`. Spec §9's
  ask (deterministic premium_entry/premium_exit_estimate/spread_cost/
  slippage/fees/theta_cost/IV_risk as its own evaluated step) doesn't exist
  as a separate concept; `credit_spread_economics()` computes net credit
  from LTP directly, not from a modeled exit/execution price (see §18
  below — same root cause).

---

## 8. Hedge engine

**EXISTING** — Structure is exactly one type: SELL primary + BUY a
further-OTM leg of the *same* option_type (a vertical credit spread) —
verified in `risk.py:27-53`'s `credit_spread_economics()` (CE hedge strike
must be `>` primary strike, PE hedge strike must be `<` primary strike;
returns `None` otherwise, i.e. it structurally cannot produce anything
else). Hedge ratio is always 1 lot : 1 lot (`decision.lots` sizes both legs
identically — no delta-adjusted ratio anywhere; `net_delta_exposure()`
*reports* net delta but nothing acts on it to adjust ratio).

**MISSING**
- Structure generator (spec §11): IRON_CONDOR, IRON_FLY, CALL_RATIO,
  PUT_RATIO, CALENDAR, DIAGONAL, PROTECTED_SHORT_* — none exist. Only the
  single vertical-credit-spread structure is implemented.
- Delta-adjusted hedge ratio (spec §28) and hedge rebalancing (spec §29) —
  absent. A hedge is opened once and never adjusted; there is no rebalance
  trigger, no cooldown logic (nothing to have a cooldown for yet).
- Hedge effectiveness measurement (spec §27: gamma/vega reduction, tail-
  loss reduction, margin effect, hedge cost as a named metric) — absent.
  `net_delta_exposure()` exists but nothing computes or displays an
  aggregate "how well is this actually hedged" figure.

---

## 9–10. Payoff / margin calculation

**EXISTING** — `risk.py::credit_spread_economics()` computes max_profit,
max_loss, net_credit, strike_width from actual leg quantities (verified:
uses `lot_size` consistently, no quantity-mismatch bug found). Margin =
`max_loss_per_lot`, explicitly and honestly labeled `APPROXIMATED_AS_MAX_LOSS`
with a margin_note admitting no broker SPAN API exists (risk.py:18-24).

**MISSING**
- Independent payoff sanity-check (spec §13: recompute via a second,
  independent function, `NO_TRADE` on mismatch) — there is only ONE payoff
  function; nothing cross-checks it. Given it's a single ~25-line pure
  function (`credit_spread_economics`) the actual risk of a silent
  calculation bug is low, but the spec's explicit ask (an independent
  second implementation) is not met.
- Breakeven points (`BREAKEVEN_UPPER`/`BREAKEVEN_LOWER`, spec §12) — not
  computed anywhere. Only max_profit/max_loss/net_credit/strike_width
  exist; breakeven = primary_strike ± net_credit is a trivial addition but
  genuinely absent today.
- `required_margin` / `used_margin` / `margin_utilization` /
  `estimated_margin` / `broker_margin` as **separate** tracked figures
  (spec §16) — `capital.py`'s `PaperCapitalState` only has
  `allocated_margin` (one number, doubling as all of these).
- Margin safety buffer / `MAX_MARGIN_UTILIZATION` / `MIN_FREE_MARGIN`
  (spec §17) — absent. `size_position()` caps by `max_risk_pct` (2%
  default) and available capital per-trade, but there is no *portfolio*
  utilization ceiling (e.g. "never let allocated_margin exceed 80% of
  starting_capital across ALL open positions combined") — three
  simultaneous max-sized positions could in principle each individually
  pass `size_position()` while collectively consuming far more of the
  account than a human would consider safe.

---

## 11. Paper-trading execution

**EXISTING** — Real, deterministic PAPER bookkeeping: `capital.py`
allocate/release with an enforcement backstop that raises rather than
letting `available_capital` go negative (capital.py:93-102). Self-healing
reconciliation against the position-store ground truth, added 2026-09-29
(`capital.reconcile()`/`resync()`, wired into every `scan()` tick via
`runner._reconcile_capital()`).

**UNSAFE — confirmed, matches spec §18's exact concern**
- Every fill in hedging (`open_position()`, `_current_quotes()`,
  `check_exit()`) uses **LTP directly** — `primary_sel.leg.premium` /
  `cand.premium` are both `leg.ltp` sourced (selector.py:97,
  primary_selector.py:81), and exits use `leg.ltp` from the chain or
  `fetch_market_quote()`'s `ltp` field (runner.py:230,265-266). There is
  **no bid/ask-based fill simulation anywhere**: no "BUY fills near ASK /
  SELL fills near BID", no slippage model, no latency model, no partial-
  fill probability (`PAPER_SLIPPAGE_MODEL`, spec §18, does not exist).
  `OptionLeg` already carries `bid`/`ask` (chain.py:62-63) — the data is
  available and simply unused for fill pricing; `selector.py` *does* use
  bid/ask for the spread% *gate* (selector.py:116) but not for what price
  the trade is modeled as executing at. This means every PAPER P&L number
  in the hedging engine today is optimistic relative to what a live fill
  would actually achieve, in both directions (entry credit overstated,
  exit debit understated).

---

## 12. Dashboard state mapping

**EXISTING** — `app/api/hedging_routes.py` exposes
`status/arm/disarm/config/last-scan/positions/emergency-exit-all/capital-
reset`. `GET /positions` correctly derives live figures
(`current_primary_premium`, `current_hedge_premium`, `unrealized_pnl`) via
the shared `runner._current_quotes()` rather than storing them (avoids a
second source of truth). Frontend (`frontend/index.html`/`app.js`) now
shows Starting Capital, SELL/BUY leg badges, Max Loss as %-of-capital
(2026-09-29, this session).

**MISSING vs spec §55/56**
- No per-position IV/delta/gamma/theta/vega display (data exists on the
  chain leg at selection time but is never persisted onto `HedgePosition`,
  so it can't be shown "CURRENT" on the dashboard the way spec §55 wants —
  only entry premiums and current LTP-derived premiums are stored).
- No breakeven display (doesn't exist to display, see §9-10 above).
- No `DATA_AGE` / quote timestamp shown per leg (spec §20's "AGE 240ms").
  `_current_quotes()` doesn't return or store a per-leg fetch timestamp.
- Engine-state dashboard (spec §56: `DISARMED / MONITOR_ONLY / PAPER_ARMED
  / LIVE_ARMED`) — today `is_armed()` is a plain boolean
  (`db.get_setting(ARMED_KEY) == "1"`, runner.py:91-92). There is no
  `MONITOR_ONLY` distinction in the state itself, though the *behavior*
  already matches it: `_scan_unlocked()` always runs `check_exits()`
  regardless of armed state (runner.py:334-337) — i.e. disarmed already
  *behaves* like MONITOR_ONLY, it's just not named/surfaced as a state.
  `LIVE_ARMED`/`SAFE_MODE`/`EMERGENCY_MODE` don't exist because there is no
  LIVE path for hedging at all yet.
- `RECON: CLEAN/REQUIRED` (spec §56) — the reconciliation *runs* every
  scan (`_reconcile_capital()`) but its clean/dirty status isn't exposed
  on `GET /status`; it's only visible via Telegram when a mismatch fires,
  or buried inside `last-scan`'s result blob if one happened to occur on
  the most recent tick.

---

## 13. Scheduler

**EXISTING** — `scripts/hedging_scan_cron.sh` (`*/5 8-15 * * 1-5`) + a
process-level `flock` (separate from `runner.py`'s own `_LOCK_PATH`,
intentionally — see runner.py:37-49's docstring on why an in-process lock
alone wasn't enough). Weekend check only (`DOW -ge 6`).

**CONFLICTING / UNSAFE** (restates §4's finding from the scheduling side)
- The cron's fixed `8-15` hour window *is* the market-session gate today,
  by omission rather than design — there's no explicit `market_calendar`
  check backing it up in Python, so the safety property ("hedging only
  runs when it should") lives entirely in a crontab line and a bash
  weekday check, not in code that's unit-tested or that a non-shell
  reviewer would necessarily think to check. Holiday-awareness is
  completely absent at this layer too (same finding as §4).

---

## 14. Persistence / database

**EXISTING** — Standard project pattern: `db.get_setting`/`set_setting`,
one JSON blob per logical key (`hedging_open_positions`,
`hedging_paper_capital`, `hedging_config`, `hedging_armed`,
`hedging_last_scan`). Consistent with `app.combos`' own established
pattern per this module's docstrings.

**MISSING**
- No `position_id`/`structure_id`/`decision_id`/`strategy_version`/
  `config_version`/`model_version`/`session_id` fields tracked at all
  (spec §47) beyond `position_id` itself (a `uuid4`, position.py:111).
  There is no versioning of *which config* (`get_config()`'s values at the
  time) produced a given position — if `DEFAULT_CONFIG` or a user's config
  patch changes later, historical positions can't be traced back to the
  parameters that actually opened them.

**UNSAFE** — restates the position-manager finding: the same
non-atomic blob read-modify-write pattern underlies every one of these
keys, not just positions. `capital.py`'s ledger has the same theoretical
exposure as `position.py`'s did before the 2026-09-29 lock fix, mitigated
today only because every capital-mutating call currently happens to be
reached exclusively through the now-locked `scan()` path or through
directly-tested single-threaded route handlers — there is no lock at the
`db.set_setting` layer itself, only at the `scan()` call-site layer.

---

## Cross-cutting findings not tied to one numbered spec area

- **AI role boundary (spec §30-31):** moot for hedging today — there is no
  AI component in `app/hedging/` at all (explicitly documented as
  "Deterministic; no AI anywhere in this module", selector.py:7). Nothing
  to constrain yet; noted so a future AI-assisted layer knows this
  boundary must be designed in from the start, not retrofitted.
- **No-trade reason taxonomy (spec §32):** partially exists as free-text
  `reason` strings (`"primary: {reason}"`, `"hedge: {reason}"`,
  `"already has an open position"`, etc. — runner.py:151-175) but is not a
  fixed enum/taxonomy the spec wants (`SESSION_INVALID`, `TIME_CUTOFF`,
  `DATA_STALE`, `PAYOFF_MISMATCH`, ...). Fine for a Telegram message,
  not machine-comparable across scans for e.g. a "why did we skip 40
  times" dashboard.
- **Emergency-exit-all (spec §39):** `POST /api/hedging/emergency-exit-all`
  (hedging_routes.py:165-179) does step 5 (exit) and step 1 partially (a
  real quote is required per leg or the position is left UNRESOLVED, never
  closed blind) but does **not**: require a confirmation token server-side
  (relies entirely on the frontend not calling it by accident), freeze new
  entries (doesn't call `disarm()` — an armed engine could theoretically
  open a *new* position on the next cron tick seconds after an emergency
  exit), or run `_reconcile_capital()` afterward to confirm the ledger
  matches the post-exit position store before reporting done.
- **Restart safety (spec §46):** confirmed safe by construction, not by an
  explicit restart hook — `is_armed()` reads persisted DB state
  (runner.py:91-92), so a process restart neither arms nor disarms
  anything; whatever was armed before restart stays armed after. This
  technically satisfies "don't auto-arm" (nothing *newly* arms on
  restart) but also means it doesn't satisfy "land in
  SAFE/MONITOR_ONLY after restart and require a fresh manual
  confirmation" (spec's actual intent) — an already-armed engine resumes
  fully armed, silently, with no post-restart health/reconciliation gate
  before it evaluates its first post-restart entry.

---

## Summary for scoping a Phase 1 implementation pass

Ranked by (blast radius) × (how latent/easy to trigger accidentally):

1. **No market-session/holiday gate in code for hedging** — cron-schedule-
   only, no holiday check, and structurally breaks MCX exits if MCX
   symbols are ever added to config (§4/§13).
2. **PAPER fills use LTP, not bid/ask** — every PAPER economics number is
   optimistic vs. a real fill (§11/§18).
3. **Primary-leg selection has no liquidity/spread gate** (only the hedge
   leg does) — backwards given the primary leg carries the open-ended risk
   (§6-7).
4. **No portfolio-level margin utilization ceiling** — only per-trade
   risk% is capped; nothing caps aggregate exposure across concurrent
   positions (§9-10/§17).
5. **Emergency-exit-all doesn't freeze new entries or re-reconcile after**
   (§39).
6. **No decision-snapshot/structure-id persistence** — historical
   positions aren't reproducible or traceable to the config that produced
   them (§48/§47).
7. Everything else (structure generator beyond vertical spreads, delta-
   adjusted hedge ratio, hedge-effectiveness scoring, breakeven display,
   STRIKE_SCORE with greeks) is a genuine feature gap vs. the spec but not
   a safety issue given the system is PAPER-only and single-structure by
   design today.

Per the spec's own §Phase-0 mandate: **no implementation should proceed
until this list is reviewed and prioritized** — items 1–2 in particular
are correctness/safety issues in the *existing* PAPER engine, not new
features, and are cheap to fix without touching the state-machine/
structure-generator/AI-scoring work that would be a much larger, riskier
change.
