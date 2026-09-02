# Feed-Staleness Root Cause + Fix

**Date:** 2026-09-01 · **Trigger:** 2026-09-01 session — only 2 AUTOSCALP trades
(both NATURALGAS, ~flat); NIFTY 0, CRUDEOIL 0. Many good setups were refused by the
`stale feed (> 12s)` safeguard.

---

## A. Symptom (from the day's `live_market_snapshots`)

`feed_age_sec` on open-market evaluation cycles, 2026-09-01:

| band | rows |
|---|---|
| 0–6 s (healthy) | 35 |
| 6–12 s | 663 |
| **12–20 s (stale)** | **642** |
| **20 + s** | **310** |

**~57 % of all evaluation cycles saw ticks older than the 12 s stale cutoff.**
Stale-feed entry blocks (`BLOCKED[stale feed …]`) clustered in tight runs
(11:20–12:19 IST CRUDE at 14–23 s; 15:06 IST NG). Several blocked CRUDE setups
were strong — e.g. `RESISTANCE_REVERSAL score 73.9, p 0.58, EV 0.4R` — killed
purely by feed age.

## B. Root cause — event-loop starvation

The AngelOne WS market-data feed (`AngelMarketFeed._run` → `await ws.recv()`) and
the autoscalp decision loop (`AutoScalpRunner._loop` → `tick_once` → `_evaluate`)
**run on the same asyncio event loop**.

`_evaluate` did the following **synchronously, on that loop**, once per symbol
(×3) every `decide_every_sec` (30 s):

| call | cost |
|---|---|
| `self.chain_provider(...)` = `_autoscalp_chain` → `market_data.selection_snapshot()` | **synchronous broker REST for ~10 option quotes + expiry resolve** — 1–5 s when the broker is slow |
| `decide_from_context(...)` | CPU: `compute_sr` + regime + MTF + per-leg analysis over ~100 bars × ~10 legs |
| `_persist_snapshot(...)` → `db.insert_live_snapshot` | synchronous SQLite write (fsync) |

While any of these ran, `ws.recv()` could not be scheduled, so WS frames backed
up in the socket buffer and `last_msg_age_sec` climbed. 2026-09-01 the broker
REST was slow all day (`fetch_candles` returning `DATA_UNAVAILABLE` /
`CONFIG_REQUIRED`), so every cycle blocked for seconds → the feed age **pinned**
around 14 s (not oscillating 0→14 — continuously behind).

### Evidence that the chain fetch is the culprit

Split 2026-09-01 open-market rows by whether the cycle did a chain fetch:

| group | rows | avg `feed_age_sec` | > 12 s |
|---|---|---|---|
| chain fetched | 1474 | **13.2 s** | 59 % |
| no chain (MARKET_CLOSED early return) | 199 | 10.2 s | 45 % |

MCX only (heaviest `selection_snapshot`):

| symbol / group | rows | avg age | stale |
|---|---|---|---|
| **CRUDEOIL — chain fetched** | 477 | **19.0 s** | **98 %** |
| CRUDEOIL — no chain | 127 | 13.4 s | 71 % |
| **NATURALGAS — chain fetched** | 556 | **12.5 s** | 72 % |
| NATURALGAS — no chain | 48 | **6.7 s** | **0 %** |

When the runner does **not** fetch a chain, the NG feed is a healthy 6.7 s with
**zero** staleness. Every chain fetch drags it over the cutoff. Consecutive
CRUDE snapshots also ran 32–38 s apart (cycle overrunning its 30 s budget).

This is not the broker feed being "down" — WS stays `connected`, age drops to
0 s between the blocking bursts. It is the loop not draining it.

## C. Fix — move the per-cycle blocking work off the event loop

`app/autoscalp/runner.py` `_evaluate`, wrapped in `await asyncio.to_thread(...)`
(the pattern already used here for `db.lease_acquire` and the seed candle
fetches — just not the hot path):

- `self.chain_provider(...)` — the broker REST chain fetch
- `decide_from_context(...)` — the CPU decision (pure function of the bar
  snapshot + chain; `bars` is already a copy from `agg.snapshot()`)
- `_index_future_vwap(sym)` — the (cached) NIFTY-future VWAP REST call
- `_persist_snapshot(...)` — the SQLite write (already `_lock`-guarded)

**No trading logic, threshold, safeguard, broker-order or NIFTY-profile change.**
The decision inputs and outputs are byte-identical — only *where* the work runs
changes. `_emit` (WS broadcast) stays on the loop.

Not changed (measured follow-up): `_monitor()` (does `asyncio.create_task` inside,
0–3 positions, fast), `_maybe_recalibrate` (big read but only every 15 min), the
two `db.list_trades` reads in `_evaluate` (lock-guarded, ms).

## D. Verification plan — next session (2026-09-02)

1. `data/feed_staleness_probe.sh` (read-only, 2 s sampling) runs across the
   session → `data/feed_staleness_probe.jsonl`.
2. After the session, re-run the split from §B on 2026-09-02 rows. **Pass =**
   the "chain fetched → avg ~13–19 s / 60–98 % stale" collapses toward the
   "no chain" numbers (single-digit seconds, few/no > 12 s blocks).
3. Confirm `BLOCKED[stale feed …]` count drops sharply vs the 2026-09-01 ~40.
4. Sanity: trade count / decisions unchanged in character (still gated by
   regime / EV / type filters — this fix only removes the *artificial* feed-age
   blocks, it does not loosen any real gate).

## E. Status

- Fix implemented + full backend suite **296 passed** (no regression).
- Deploy: bundled with the 2026-09-01 post-MCX-close restart (alongside
  FIX-2/FIX-3 + NIFTY-future VWAP).
- Live confirmation: **pending the 2026-09-02 session** (step D).

---

## F. Verification results — 2026-09-02 session (post greeks-engine deploy)

Deploy: `oi-dashboard` restarted 2026-09-02 ~23:42 IST at commit `5555952` (after
NSE + MCX close, both daily reports fired). `data/feed_staleness_probe.sh`
restarted post-deploy.

**Split (2026-09-02 open rows, `regime != 'MARKET_CLOSED'`), `feed_age_sec` by group:**

| group | n | avg age | % > 12 s |
|---|---|---|---|
| chain fetched (`length(chain_json)>50`) | 3348 | **0.1 s** | **0.0 %** |
| no chain | 1026 | 0.1 s | 0.0 % |

Per symbol × chain-fetched — **all** groups avg 0.0–0.1 s, 0.0 % over 12 s:

| symbol | chain avg / %>12 | Sep-1 baseline (chain) |
|---|---|---|
| CRUDEOIL | **0.1 s / 0.0 %** | 15.8 s / 99 % |
| NATURALGAS | **0.1 s / 0.0 %** | 9.2 s / 28 % |
| NIFTY | 0.0 s / 0.0 % | — |
| BANKNIFTY | 0.0 s / 0.0 % | — |

`BLOCKED[stale feed …]` on 2026-09-02: **0** (Sep-1 baseline: **49**).

**Verdict: PASS.** The chain-fetched avg feed age collapsed from 11.8 s (54 %
stale) to 0.1 s (0 % stale); the worst case CRUDEOIL/chain went 15.8 s/99 % →
0.1 s/0 %; artificial stale-feed blocks went 49 → 0. No genuine stale data was
suppressed — the WS loop simply stops being starved once the chain fetch /
`decide_from_context` / persist run in `asyncio.to_thread`. Trade character
unchanged (still gated by regime / EV / type filters).
