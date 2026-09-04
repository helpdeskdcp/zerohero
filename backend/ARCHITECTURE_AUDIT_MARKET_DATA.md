# Market-Data Architecture Audit — load reduction + stall elimination

**Date:** 2026-09-04 · **Scope:** the live-market-data layer feeding the Math
Scalper / Mathematical-Confluence / Smart-Index-Scalper surfaces and how it
shares the AngelOne broker connection with histcap, autoscalp and the scalp
runner. **No trading-logic / `live_trading` change.**

---

## 1. What talks to the broker today

| consumer | transport | cadence | per-tick broker calls | notes |
|---|---|---|---|---|
| **scalp runner** (`scalper.py`) | **WS feed** (`AngelMarketFeed`, owns the single socket) + REST for option marks | `poll_sec` 5 (1 in fast mode) | WS: 0 · REST: option-mark `get_ltp` per open trade | feed leader; in-memory `get_ltp(token)` tick cache |
| **autoscalp runner** (`autoscalp/runner.py`) | **same WS feed** (`feed=scalp_runner.feed`) + `chain_provider` | `poll_sec` 2 | WS: 0 · chain via provider | co-located with the feed owner (the `--workers 1` reason) |
| **histcap worker** (`histcap/worker.py`) | **REST** `get_quotes_batch(mode=FULL)` + `get_candles` | quotes every **20 s**, candles every **90 s** | 7 symbols × {1 batch quote + N candle intervals + option chain window} | append-only capture to `market_history.db`; leader-elected |
| **`/api/mathematics/*`** (Math Scalper card, levels, oi) | **REST** via `market_context()` → `selection_snapshot()` | frontend poll **20 s**, per focus symbol + on demand | `resolve_index` + `get_quote` + `get_candles ONE_DAY` + `get_candles FIVE_MINUTE` + `get_option_chain` — **4–5 REST calls / symbol / miss** | 45 s per-symbol cache |
| **`/api/mathematics/market-map`** | same, **looped over 5 indices** | frontend poll 20 s | up to 5 × the above | |
| **`/api/smart-scalper/ranking`** | `scanner.scan()` → `market_context()` per universe symbol | frontend poll 20 s + **`warm_math_ranking.sh` cron** | 6 symbols × 4–5 REST | ~21 s cold |
| **`warm_math_ranking.sh`** | hits `/ranking` | **every 3 min** (was every 1 min) | full 6-symbol cold scan each fire | a cron I added to paper over the cold path — it competes with real requests |
| **greeks engine / turning-point / reversal** | REST | on demand / their own ticks | quote + greek calls | |

### The shared choke points
1. **One REST SmartConnect session** (`_market_sdk`, `_sdk_lock`). Every REST
   caller serialises through it for auth; the calls themselves then race on
   AngelOne's per-endpoint rate limits (`getCandleData` is the tightest —
   ~3 req/s and a daily cap; `quote`/`ltpData` looser but not infinite).
2. **One WS socket** (`AngelMarketFeed`) — already the right model, but **only
   scalp/autoscalp read from it.** The mathematics/ranking path re-fetches the
   same index spots over REST that the feed already has in memory.
3. **N independent pollers, no coordination.** histcap (20 s) + math poll
   (20 s) + ranking poll (20 s) + ranking cron (180 s) + 5 other view polls all
   fire their own broker calls for overlapping symbols. Nothing dedupes.

---

## 2. Why it stalls / why load is high (root causes)

| # | root cause | symptom the user saw |
|---|---|---|
| **R1** | `market_context` used `_market_sdk(require_auth=False)` — never refreshed a stale daily REST token | whole card `DATA_INSUFFICIENT` for every symbol until restart *(fixed f78c58a)* |
| **R2** | `market_context` fetched the **index spot over REST** (`get_quote`) on every cache miss instead of reading the live WS feed the app already runs | `get_quote` gets rate-limited under load → intermittent `DATA_INSUFFICIENT` per symbol (BANKNIFTY, then FINNIFTY…) — whack-a-mole *(patched with histcap fallback 2ea6073, but that's a workaround)* |
| **R3** | `getCandleData` (daily candle for prev-day OHLC + 5 m bars) called on **every** context miss, for a value that changes **once per day** | flaky prev-day → `DATA_INSUFFICIENT` even with a spot *(patched with day-cache + histcap 2ea6073)* |
| **R4** | `market-map` + `ranking` fan out `market_context` **per symbol in a Python loop**, each doing 4–5 serial REST calls | 21 s cold scan; the frontend blocks a panel on it |
| **R5** | the frontend had a `_msBusy` guard whose only reset was a conditional `finally`; a **hung** REST call (common right after a deploy) wedged it permanently | "page stalls after auto-refresh" *(fixed a0c3c6d: timeouts + unconditional release + watchdog)* |
| **R6** | `warm_math_ranking.sh` cron fires a full cold 6-symbol scan on a fixed schedule, **independent of** whether anyone is looking, competing with live requests for the rate-limited candle API | adds steady background load; the 45 s ctx TTL < 60 s cron meant it never even stayed warm |
| **R7** | every 20 s poll **rebuilt every panel's `innerHTML`** wholesale | visual "page refresh" feel, scroll jump *(fixed 2ea6073: `_msSet` diff-before-write)* |
| **R8** | one shared `_sdk_lock` around auth **and** every consumer → a slow login or a slow call under the lock stalls unrelated callers | occasional broad slowness |

R1, R3, R5, R7 are fixed. R2, R4, R6, R8 are **architectural** and are what this
document proposes to fix properly.

---

## 3. Revised architecture

> **One market-data hub. Everything reads from it. The broker is touched by
> exactly one component per data type, at a bounded rate.**

```
                          ┌──────────────────────────────────────────┐
   AngelOne WS  ───────▶  │  MarketHub  (in-process, single owner)    │
   AngelOne REST ──┐      │                                          │
                   │      │  • spot/LTP  ← WS feed (live, 0 REST)     │
                   ├────▶ │  • prev-day OHLC  ← fetched ONCE/day/sym  │
                   │      │  • intraday bars  ← 1 REST / sym / 60s    │
                   │      │  • option chain   ← 1 REST / sym / 20-30s │
                   │      │  • instrument refs ← cached 1h            │
                   │      │  fair-share rate limiter on the REST leg  │
                   └────▶ │  histcap writes market_history.db from    │
                          │  the SAME hub snapshots (no extra calls)  │
                          └───────────────┬──────────────────────────┘
                                          │  read-only, no broker calls
         ┌────────────────┬───────────────┼───────────────┬────────────────┐
   market_context     scanner.scan    autoscalp        scalp runner     /api/* reads
   (/api/mathematics) (/ranking)      (already WS)      (already WS)
```

### 3.1 Principles
- **Spot is never fetched over REST by a read path.** `MarketHub.spot(sym)`
  returns the WS feed's `get_ltp(index_token)` (subscribe the index tokens for
  the whole configured universe once at startup). Fallback order: WS → last
  histcap `quote_snapshots` (≤180 s) → REST `get_quote` **once**, result cached.
- **Prev-day OHLC is fetched at most once per symbol per IST day** (already
  half-done in `context.py` via `_PREVDAY`; move it into the hub, prime it at
  startup for the whole universe, and back it with `market_history.db`).
- **Intraday bars: one REST `get_candles` per symbol per 60 s, hub-owned**, not
  per API request. Readers get the cached list.
- **Option chain: one fetch per symbol per 20–30 s, hub-owned**, shared by
  `market_context`, the ranking scanner and the OI panel.
- **histcap stops making its own quote calls** — it persists the hub's
  snapshots. (Candle capture for backtest history stays its own slow 90 s job.)
- **`market_context` becomes a pure assembler**: `hub.spot + hub.prev_day +
  hub.bars + hub.chain` → dict. Zero broker calls, so it can't rate-limit,
  can't stall, and the 45 s cache can drop to ~5 s (or go away).
- **`market-map` / `ranking` fan out over the hub**, which already holds every
  universe symbol warm → the "21 s cold scan" becomes ~50 ms.
- **Retire `warm_math_ranking.sh`** — the hub keeps the universe warm as a
  side-effect of running; no external cron competing for the rate limit.
- **Split the lock**: `_auth_lock` (tiny, around login only) vs. the hub's own
  scheduling. A slow call never blocks auth or an unrelated reader.

### 3.2 Load delta (per minute, market hours, universe = 7 symbols)

| path | today (approx REST calls/min) | revised |
|---|---|---|
| histcap quotes | 3 (1 batch/20 s) | **0** (reads hub) |
| histcap candles | ~5 | ~5 (unchanged, backtest history) |
| math poll (1 focus) | ~15 (miss-heavy) | **0** (hub) |
| market-map (5 idx) | up to ~75 on a cold minute | **0** (hub) |
| ranking poll + cron | ~30–90 | **0** (hub) |
| hub itself | — | ~7 chain + ~7 bars + 0 spot + 0 prevday = **~14/min steady** |
| **total live-data REST/min** | **~130–190, spiky** | **~19, flat** |

~**85–90 % fewer REST calls**, and — more importantly — **constant, predictable**
load instead of spikes that trip the rate limiter.

---

## 4. Phased plan (each phase independently shippable, no `live_trading` change)

### Phase 1 — spot via the WS feed (biggest single win, low risk)
- Add `MarketHub` (thin) or extend `market_context`: resolve each universe
  index token once, `scalp_runner.feed.subscribe(index_tokens, owner="mathhub")`
  at startup, and make `context.py` read spot as **WS → histcap → REST-once**.
- Delete the per-request `get_quote` for spot.
- **Effect:** R2 gone. NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY/SENSEX spot always
  fresh, 0 REST. `DATA_INSUFFICIENT` from spot rate-limiting can't happen.

### Phase 2 — hub-owned prev-day + bars + chain
- Move `_PREVDAY` + a `bars` cache + a `chain` cache into the hub, each on its
  own refresh cadence, primed for the whole universe at startup.
- `market_context` becomes assembly-only; drop its cache TTL to ~5 s.
- **Effect:** R3, R4 gone. Cold scan ≈ warm scan. Retire `warm_math_ranking.sh`.

### Phase 3 — histcap reads the hub
- `histcap/worker.py` quote path pulls `hub.snapshot(sym)` instead of
  `get_quotes_batch`. Candle capture unchanged.
- **Effect:** removes the largest steady REST consumer; one writer, one reader.

### Phase 4 — lock split + fair-share limiter
- `_auth_lock` around `_login()` only. Hub REST leg goes through a small
  token-bucket (e.g. 2 req/s) so a burst can never exceed AngelOne limits.
- **Effect:** R8 gone; graceful degradation instead of failures under load.

### Phase 5 — frontend
- Already mostly done (a0c3c6d timeouts/watchdog, 2ea6073 `_msSet`). Add: the
  `market-map`/`ranking` calls can drop to a 10 s poll since the hub is cheap,
  and show a small "feed: live / stale Ns" chip driven by `hub.age`.

---

## 5. Risks / call-outs
- The WS feed must actually carry the index spot tokens. autoscalp already
  subscribes its watchlist; Phase 1 explicitly subscribes the **whole mathhub
  universe** so it doesn't depend on what autoscalp happens to watch.
- Multi-process (`--workers > 1`) would break the in-process hub the same way it
  breaks the feed today — stays `--workers 1` (already the case, and memory
  notes why).
- histcap's backtest candle history (Phase 3) must keep its own 1 m/3 m/5 m…
  capture — only the *quote* path moves to the hub.
- Everything stays PAPER / `live_trading=false`; the hub is read-only market
  data, no order path.
