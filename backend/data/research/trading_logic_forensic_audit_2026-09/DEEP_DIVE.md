# Deep-Dive Investigation — atr_dist duplication, backtest economics, exit logic

Continuation of `AUDIT.md` (baseline `ab40e0a`). Read-only, per directive.
No production code modified. Classification taxonomy for this report:
**SAFE / INVESTIGATE / CRITICAL** (distinct from AUDIT.md's KEEP/INVESTIGATE).

---

## A) `atr_dist` duplication in `state_classifier.py::_eval_break()`

Current lines (re-verified, file has not shifted since the first audit):

```python
# line 138
atr_dist = (C[-1] - lvl) / atr if up else (lvl - C[-1]) / atr
...
# line 184
"price_action": 1.0 if close_beyond else max(0.0, min(1.0, atr_dist / brk_min)),
...
# line 190
"atr": max(0.0, min(1.0, atr_dist / 1.0)),
```

`_W = {"price_action": 0.20, ..., "atr": 0.08, ...}` — both weights confirmed
current (`app/engines/state_classifier.py` lines 25-27).

**Verdict: CORRELATED, not IDENTICAL.** Both share the single input `atr_dist`,
but apply different saturation thresholds and `price_action` has an
additional binary gate (`close_beyond`) with its own, different reference
point (`zone["zone"][1]`, the zone's outer edge) vs `atr_dist`'s reference
point (`zone["level"]`, the anchor level) — these can differ when the zone
has width, so the two are not even always monotonically locked together.

Worked example (`brk_min = 0.30` default):

| `atr_dist` | `close_beyond`? | `price_action` | `atr` component |
|---|---|---|---|
| 0.10 | No | 0.10/0.30 = **0.333** | 0.10/1.00 = **0.100** |
| 0.30 | No | 0.30/0.30 = **1.000** (saturates) | 0.30/1.00 = **0.300** |
| 0.60 | No | **1.000** (still saturated) | 0.60/1.00 = **0.600** |
| 0.60 | Yes | **1.000** (gate fired) | 0.60/1.00 = **0.600** |
| 1.50 | Yes | **1.000** | 1.00 (clamped, saturates) |

`price_action` saturates at `atr_dist = 0.30` (or immediately if `close_beyond`
fires from its own separate check); `atr` keeps rising linearly out to
`atr_dist = 1.0`. In the `atr_dist ∈ [0.30, 1.00]` range — which is exactly
the "confirmed breakout, how far did it run" zone — `price_action` is pinned
at 1.0 and contributes **zero further differentiation**, while `atr` is the
**only** one of the two still carrying information. So the failure mode is
not "one number counted twice at full weight" — it's narrower: for
`atr_dist < 0.30`, the two components are highly correlated (same input,
merely rescaled 3.33x) and effectively double-book ~0.28 combined weight
onto one measurement; for `atr_dist ≥ 0.30`, `atr` is doing real independent
work and `price_action`'s 0.20 weight is contributing a constant, not
duplicate information.

**Classification: INVESTIGATE (confirms AUDIT.md, quantified).** Not
CRITICAL — the score stays correctly bounded either way, and the two
components are genuinely redundant only in a sub-range of one input, not
across the board. A future real-cost A/B (does collapsing this into one
component change backtest PF materially) would need the same before/after
methodology already established this session (train/test swap, real costs)
before touching it. **Not touched in this task.**

---

## B) Backtest economics (`app/backtest/replay.py`, `runner.py`)

Traced `ReplayHarness` (the actual simulation engine `runner.py` drives):

- **Entry price**: `entry = float(sig.get("entry") or leg["ltp"])` (`replay.py`
  line ~379) — the deterministic engine's own planned premium, or the
  contract's real captured LTP at that cycle if the signal didn't set one.
  No bid/ask distinction — this is effectively a single-price (mid/last) fill.
- **Exit price**: `SimTrade.check_exit()` compares raw `ltp` to `cur_sl` /
  `target_1` / `max_hold_sec`; `close()` fills at that same raw `ltp`
  (`replay.py` lines ~127-142). Same single-price assumption as entry.
- **Spread**: **not modeled anywhere in `replay.py`.** Confirmed by reading
  every price-comparison line in `SimTrade` — always the bare `ltp`.
- **Slippage**: **no slippage model at all.** `grep -rn "slippage" app/backtest/*.py`
  → zero matches.
- **Brokerage/fees/STT/GST/stamp duty**: **zero references** anywhere in
  `app/backtest/` (`grep -rn "institutional_edge|STT|brokerage|GST|stamp_duty|txn_charge|slippage" app/backtest/*.py`
  → no output). `ReplayResult.summary()` (`replay.py` ~189-206) computes
  `profit_factor`/`win_rate`/`net_points` purely from `t.points = exit_price -
  entry` — **100% gross, no cost layer of any kind.**

**Sharper finding than "costs are missing" — an asymmetry, not just a gap.**
`app/engines/paper_trading.py::close_trade()` (the REAL live paper-trade
close path, lines 212-253) already imports and calls
`institutional_edge.costs.estimate_cost()` on every real close, populating
`gross_pnl` / `trading_cost` / `net_pnl` / `cost_model_status` — `"OK"` for
symbols with a calibrated cost table (confirmed: MCX NATURALGAS_OPTION
Rs113.50/lot round-trip, MCX CRUDEOIL_OPTION Rs184.50/lot,
`app/institutional_edge/costs.py` lines 16-22), `"UNCALIBRATED"` otherwise
(NIFTY/BANKNIFTY/SENSEX, by design, per this session's own prior finding).
**The live system already knows how to net real costs for NatGas/CrudeOil.
The backtest engine (`replay.py`) never calls this at all, for any symbol.**
This means a NATURALGAS/CRUDEOIL backtest run's reported PF/win-rate is not
just "missing costs in general" — it's missing a cost model that already
exists, is already calibrated, and is already wired into the sibling live
path one file away (`app/institutional_edge/costs.py` → imported by
`app/engines/paper_trading.py`, never by `app/backtest/replay.py`).

**Update — the real run finished after this task's own time budget (below,
result recovered afterward, not fabricated):** a real out-of-sample NATURALGAS
backtest (train 2026-07-14→08-10, test 2026-08-11→08-28) came back:

```
n=19 closed, wins=4, losses=7, win_rate=0.211, profit_factor=0.68,
expectancy=-0.042 pts/trade, net_points=-0.8, max_consecutive_losses=5
exit_reasons: {TRAIL:7, STOP:1, TIME:8, TARGET:3}
calibration: predicted 0.5376 vs actual 0.2105 (n=19, ECE=0.327, Brier=0.273)
```

This is **already net-negative at the gross (0-cost) level** — `profit_factor
0.68 < 1`, negative expectancy, before a single rupee of the real, calibrated
NatGas cost (Rs113.50/lot round-trip) this section already established exists
and is simply never applied. Adding that cost would only widen the gap. This
doesn't validate or invalidate an edge on its own (n=19, one train/test split
— exactly the small-sample caveat this whole session enforces elsewhere), but
it means the backtest-vs-live cost asymmetry documented above is not a
theoretical concern for NATURALGAS specifically: on this real split, the
strategy was already underwater before costs were even in question. Also
notable: even under today's fixed calibration (`04136c7`), this split's one
populated reliability bin still shows real overconfidence (53.8% predicted vs
21.1% actual) — consistent with a genuinely thin/negative underlying edge that
no calibration curve can manufacture information to fix, exactly as that
fix's own documentation says.

**Classification: INVESTIGATE (Medium — confirms and sharpens AUDIT.md's
finding).** Any backtest PF/win-rate reported for NATURALGAS/CRUDEOIL should
be read as an upper bound, not a like-for-like comparison to what the same
strategy would net in live paper trading, where real costs already apply.
**Not touched in this task** — no wiring change made to `replay.py`.

---

## C) Exit-logic audit — the REAL live path (`app/engines/paper_trading.py`)

The real exit-execution path is `update_trade_price()` (lines 100-206),
called every monitor tick from `app/autoscalp/runner.py::_monitor()` (line
1069) for every open position with a live LTP mark. This is a **separate,
independently-implemented** function from the backtest's `SimTrade.check_exit()`
in `replay.py` — they are not the same code, a discrepancy in its own right
(see below).

**Target hit**: `hit_t1 = (direction=="BUY" and ltp >= target_1) or
(direction=="SELL" and ltp <= target_1)` (line 129) — raw LTP, no spread.

**Stop loss hit**: `hit_sl` (line 127), same shape, compared against the
CURRENT `stop_loss` field (which the trailing/profit-lock logic further down
may have already ratcheted from its original value — see below).

**Trailing stop**: two distinct, both real:
1. A **profit-lock** ratchet (lines 168-182): at `MFE ≥ 0.6R` moves the stop
   to `entry + 0.2R`; at `MFE ≥ 1.0R` moves it to `entry + 0.5R`. Comment:
   "the stop only ever ratchets favourably... can turn a would-be scratch
   into a small win, never force a loss" — verified: the `if lock is not None
   and (direction=="BUY" and lock > new_sl) or ...` guard is monotonic, can
   only tighten in the favorable direction.
2. A **distance-based trail** (`trailing_stop` field, lines 187-190): once
   `MFE >= trailing_stop`, `new_sl = ltp - trailing_stop` (for BUY), same
   monotonic-only guard.
Both **SAFE** — genuinely can only loosen risk in the trader's favor, never
against.

**Timeout / max-hold**: lines 142-145 — `if max_hold and opened and
(now-opened).total_seconds() >= max_hold: return close_trade(..., exit_reason="TIME")`.
**This check runs and returns BEFORE the `hit_sl`/`hit_t1` check** (line 145
returns; the `if hit_sl or hit_t1:` block starts at line 152). The function's
own docstring states the intended order explicitly: *"Applies, in order:
time-stop -> stop/target hit -> breakeven move -> trailing ratchet."*
**This is documented, intentional ordering, not incidental** — confirmed by
matching code structure to docstring.

**Edge case flagged (INVESTIGATE, Low)**: because TIME is checked first, a
tick where `max_hold` has *just* elapsed AND `ltp` simultaneously satisfies
`hit_t1` will close with `exit_reason="TIME"`, not `"TARGET"` — the P&L value
itself is unaffected (both paths close at the same `ltp`), and `close_trade`'s
own `result = forced_result or ("WIN" if pnl>0 else ...)` still classifies
WIN/LOSS correctly from the actual P&L sign in the TIME path (no
`forced_result` is passed for TIME). So this is a **label-only** edge case
(the trade is correctly counted as a WIN, just attributed to the wrong exit
reason in a rare simultaneous-trigger tick) — not a P&L-correctness bug.

**Signal-reversal exit**: **does not exist.** No code path in
`update_trade_price()` checks the deterministic engine's current tick output
against an open position's direction to force-close on a reversal. A
position runs purely on its own target/SL/trail/timeout, independent of
whatever the engine decides on later ticks. Confirmed by reading the entire
function — no such branch. This is a **design characteristic**, not
inherently a defect, but the spec explicitly asked, so noting it as
**SAFE** (deterministic, unsurprising) rather than flagging it as a gap.

**Volatility / emergency exit**: **not present** in this function. No
separate "volatility spike -> force exit" path was found. (Scope note: not
exhaustively grepped across the whole autoscalp package for an emergency-exit
feature that might live elsewhere under a different name — flagged as an
open question, not a confirmed absence at the whole-system level.)

**Duplicate-exit prevention**: `update_trade_price()` opens with `if not t or
t["status"] != "OPEN": return t` (lines 106-107) — a real, working idempotency
guard. Combined with `_monitor()`'s per-position loop being strictly
sequential (`for t in self._open_positions():`) and the mutually-exclusive
if/else between the "no LTP -> sweep" and "has LTP -> update_trade_price"
branches (`runner.py` lines 1069-1082), there is no code path that calls
close logic twice for the same tick. **SAFE** in the current single-threaded
execution model.

**State transitions**: **no formal state machine.** `status` is a plain DB
field (`"OPEN"`/`"CLOSED"`), flipped by a direct `db.update_trade(...,
{"status": "CLOSED", ...})` write inside `close_trade()`. There is no
intermediate `CLOSING` state and no transactional/locking guard against a
hypothetical concurrent writer. **INVESTIGATE (Low)**: currently safe only
because the actual runtime is single-threaded/sequential (verified above),
not because of an explicit guard — if this code were ever called from a
concurrent context (a future refactor, a second worker process, etc.) there
is nothing in `update_trade_price`/`close_trade` themselves that would
prevent a double-close race. Not a live risk today; worth a comment or an
`UPDATE ... WHERE status='OPEN'` guard if this ever changes.

**Look-ahead in exits**: **none found.** `update_trade_price()` only consumes
its `ltp` argument (the current real-time tick, passed in by the caller) and
already-recorded state (`entry`, `stop_loss`, `target_1`, `opened_ts`,
`trailing_stop`, `risk_ref`). No candle/bar access at all in this function —
it's purely tick-driven, so there is no "current forming candle" ambiguity
that a candle-based check could leak from. **SAFE.**

### Live vs backtest exit-logic discrepancy (new finding, not in AUDIT.md)

`replay.py::SimTrade.check_exit()` (backtest) checks in the order **STOP/TRAIL
→ TARGET → TIME** (lines 127-131: `if ltp <= self.cur_sl: return ...`; `if
ltp >= target_1: return ...`; `if max_hold... : return "TIME"`) — the
**opposite priority order** from live's documented "time-stop -> stop/target
hit". Additionally, backtest's `SimTrade.mark_to()` only implements the
distance-based trailing ratchet (`trailing_stop`) — it has **no equivalent of
live's profit-lock (0.6R/1.0R MFE milestone) mechanism** at all.

**Classification: INVESTIGATE (Medium).** The backtest engine is not a
faithful re-implementation of the live exit rules — it's a separately
maintained approximation with (a) reversed TIME-vs-STOP/TARGET priority and
(b) a missing profit-lock feature. In the rare simultaneous-trigger case this
changes which exit_reason (and possibly which of two very-close prices) a
backtested trade would report vs. what live would actually have done;
combined with the missing profit-lock, a backtest may report a LOSS/scratch
in a scenario where live would have already locked in a partial win via
profit-lock before the stop was ever touched. This directly affects how much
a backtest result can be trusted as a live-behavior predictor, independent of
the cost-modeling gap in section B. **Not touched in this task.**

---

## Summary

| # | Finding | Classification |
|---|---|---|
| A | `price_action`/`atr` share `atr_dist`; correlated below 0.30 ATR, independent above it | INVESTIGATE (Low) |
| B | Backtest has zero cost/spread/slippage modeling; live already has a calibrated cost model (NatGas/CrudeOil) that backtest never reuses | INVESTIGATE (Medium) |
| C1 | TIME-before-TARGET ordering can mislabel (not misprice) a rare simultaneous-trigger exit | INVESTIGATE (Low) |
| C2 | No formal state machine / no DB-level guard against a hypothetical concurrent double-close (safe today only because execution is single-threaded) | INVESTIGATE (Low) |
| C3 | Backtest (`replay.py`) exit priority order and feature set (no profit-lock) diverges from live (`paper_trading.py`) | INVESTIGATE (Medium) |

**0 CRITICAL. 0 SAFE-only call-outs beyond what's noted inline** (trailing/
profit-lock monotonicity, duplicate-exit guard, and no-look-ahead in exits
were all explicitly verified SAFE above).

`git diff --stat -- app/` is empty at completion. Baseline commit
`ab40e0a`. **NO PRODUCTION LOGIC WAS MODIFIED DURING THIS AUDIT.**
