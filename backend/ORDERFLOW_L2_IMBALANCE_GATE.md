# L2 order-flow imbalance confirmation gate — research report

**Status: REJECTED AT THE FEASIBILITY GATE.** Research-first, read-only. No
production wiring, no live-order change, no engine / H1-H7 / trading / execution
/ broker / risk / test change. Nothing here is `PROVEN`.

- Design + probe: `backend/scripts/orderflow_l2_imbalance_probe.py` (read-only)
- Event table: `backend/data/orderflow_l2_imbalance_probe.csv` (git-ignored, regenerated on demand)
- Prior audit this is consistent with: `ORDERFLOW_STAGE9_L2_RESEARCH.md`

---

## 1. The gate asked for

```
BUY  imbalance = bid-side / aggressive-buy pressure  ÷  ask-side / aggressive-sell pressure  ≥ 2.0
SELL imbalance = ask-side / aggressive-sell pressure  ÷  bid-side / aggressive-buy pressure  ≥ 2.0
```
Used **only as a confirmation filter** on setups that already exist; the goal is
a lift in **target-hit probability** and a **reduction in adverse movement
(MAE)**, *without* assuming it reduces stop-outs.

## 2. What genuine L2 data exists (current broker/feed)

Single wired feed: **Angel One**. Two paths.

| path | what it carries | persisted? |
|---|---|---|
| WebSocket (`app/connectors/angel_ws.py`) | **mode 1 = LTP only** (51-byte packet; larger quote/snap-quote packets explicitly ignored). No bid/ask, no sizes, no depth. | No — in-memory LTP cache only |
| REST full-quote poll (`app/histcap/…`, `market/v1/quote` mode `FULL`) → `quote_snapshots` | `ltp`, OHLC, cumulative `volume`, `oi`, `avg_price`, `last_trade_qty` (**size only, no side**), `bid`, `ask`, `bid_qty`, `ask_qty`, `tot_buy_qty`, `tot_sell_qty` (day-cumulative pending), `depth_json` (**5-level** book: price/quantity/orders per level) | Yes — `market_history.db` |

Census of `quote_snapshots` (probe §1), for the three tradable futures:

| symbol | sessions with a usable L1 book | snaps/session |
|---|---|---|
| NIFTY (fut) | **3** — 2026-09-02, -03, -04 | ~600–760 |
| CRUDEOIL (fut) | **3** — 2026-09-02, -03, -04 | ~1160–1660 |
| NATURALGAS (fut) | **3** — 2026-09-02, -03, -04 | ~1180–1680 |

Poll cadence ≈ **25–30 s** (a *snapshot*, not an update stream). `depth_json`
present on ≈ 87 % of FUTURE rows, ≈ 84 % of OPTION rows. The INDEX "book" is not
a real book and is excluded.

## 3. What is NOT available — and is never synthesised

| needed for the gate as specified | status |
|---|---|
| aggressor-classified trades (lift-offer / hit-bid), trade side | **UNOBSERVABLE** — no tick feed, no per-trade side anywhere |
| trade delta / cumulative delta / aggressive-buy vs aggressive-sell **volume** | **UNOBSERVABLE** |
| order-book add / cancel / modify **event stream** | **UNOBSERVABLE** — only ~30 s snapshots are stored |
| depth beyond 5 levels | absent |
| any L1/L2 history before 2026-09-01 | absent (broker historical API is OHLC-only) |
| ≥ 2 market regimes / ≥ ~40 independent sessions of L1/L2 | absent — 3 same-week sessions, one regime |

**The "aggressive-buy pressure / aggressive-sell pressure" half of the gate
cannot be built.** That is aggressor flow; it does not exist in this
environment and the brief forbids estimating it.

## 4. What was built instead — a PASSIVE-book variant (read-only)

Genuine **resting-liquidity** imbalance, computed three ways from real fields
(probe §3). This is *passive book pressure*, a different quantity from
aggressor pressure, and is reported as such:

```
imb_L1   = bid_qty / ask_qty
imb_D5   = Σ₅ buy.quantity / Σ₅ sell.quantity        (from depth_json)
imb_BOOK = tot_buy_qty / tot_sell_qty
```

- **Direction:** `BUY` if ratio ≥ T; `SELL` if `1/ratio ≥ T`.
- **Persistence (required, not a single snapshot):** an *event* fires only when
  the same-sign imbalance ≥ T holds for ≥ P **consecutive** ~30 s snapshots.
  Zero look-ahead — confirmation uses only snapshots up to the confirming one.
- **Scoring geometry (a reference, not an order):** entry = LTP at confirmation,
  `R` = L1 spread at entry, target = +3R, stop = −1R in the imbalance direction,
  walked forward over the ~30 s LTP snapshot path to session close.
- **Recorded per event:** imbalance ratio, direction, persistence duration (s),
  MFE_R, MAE_R, target hit, SL hit, time-to-target, time-to-SL, realised R,
  forward return at +2 / +5 / +10 min.
- **Thresholds tested:** T ∈ {1.5, 2.0 (=200 %), 2.5, 3.0}.
- **Persistence tested separately:** P ∈ {1, 2, 3, 4}.
- **Baseline:** every snapshot as a pseudo-event, both sides, same geometry.

## 5. Results (3 sessions — descriptive only, NOT validation)

| bucket | n | target-hit % | SL % | E[R] | PF | MAE_R (med) |
|---|--:|--:|--:|--:|--:|--:|
| baseline (no filter) | 7 230 | 31.4 | 68.5 | +0.26 | 1.38 | −1.00 |
| **200 % gate** (T≥2.0, P≥2, pooled) | 1 957 | 33.8 | 65.5 | +0.36 | 1.55 | −1.00 |
| └ `imb_D5` only | 363 | 35.8 | 63.6 | +0.44 | 1.69 | −1.09 |
| └ `imb_L1` only | 1 517 | 33.6 | 65.6 | +0.36 | 1.54 | −1.00 |
| └ `imb_BOOK` only | 77 | 27.3 | 71.4 | +0.10 | 1.15 | −1.08 |

Threshold sweep (P≥2, pooled): T≥1.5 → E[R] 0.33 · T≥2.0 → 0.36 · T≥2.5 → 0.33
· T≥3.0 → 0.36. **Flat — no monotonic relationship, no knee; 200 % is not a
special value.**

Persistence sweep (T≥2.0): P≥2 → E[R] 0.36 (n 1 957) · P≥3 → 0.26 (727) ·
P≥4 → 0.25 (334). **More persistence does not help.**

Per-session (200 % gate): 09-02 → E[R] 0.27 · 09-03 → 0.41 · 09-04 → 0.43.

## 6. Why this is not evidence

1. **No aggressor data** — the gate as specified is unbuildable; the passive
   variant is a substitute quantity, not the thing asked for.
2. **3 sessions, one calendar week, one regime, intraday-correlated.** A
   TRAIN/VAL/OOS/HOLDOUT walk-forward split is impossible. The Stage-6/8
   standard is ≥ ~40 independent sessions across ≥ 2 regimes.
3. **Snapshot cadence, not an L2 stream.** At ~30 s, `R` = L1 spread is smaller
   than a typical between-snapshot LTP move, so nearly every event resolves in
   1–2 snapshots and MFE_R / MAE_R saturate at ±1.00 in almost every bucket —
   the cadence cannot resolve the microstructure horizon a real imbalance
   operates on.
4. **MAE is unchanged** (−1.00R median across every bucket) — even taken at face
   value, the filter does **not** reduce adverse movement, which was one of the
   two goals.
5. The small apparent lift in target-hit % / E[R] is within-noise on correlated
   sessions and is not threshold-stable.

## 7. Verdict

- **Gate as specified (aggressor pressure ≥ 2.0): CANNOT BE IMPLEMENTED.** The
  required aggressive-buy / aggressive-sell classified flow does not exist and
  is never synthesised.
- **Passive-book depth-imbalance variant: REJECTED at the feasibility gate.** It
  cannot be shown to deliver a statistically stable improvement in target-hit
  rate / expectancy / MAE / SL frequency / profit factor on the available data,
  so per the brief it is rejected. Not disproven — **untestable** on 3 same-week
  sessions at snapshot cadence.
- Consistent with `ORDERFLOW_STAGE9_L2_RESEARCH.md`: bid-ask & depth imbalance =
  `INSUFFICIENT DATA`; aggressor flow = `UNOBSERVABLE`.
- **No production wiring. No live-order change. No frozen logic touched.**
  `PROVEN`: nothing.

## 8. What would unblock it

A real **aggressor-classified tick feed + full-depth (L2) order-book update
stream** for NIFTY / CRUDEOIL futures, persisted across **≥ 40 sessions spanning
≥ 2 volatility regimes**. With that data, `orderflow_l2_imbalance_probe.py`'s
event study becomes a genuine walk-forward validation **with no change to the
imbalance calculation** — only the aggressor-pressure measures would be added
alongside the passive ones, and the split would become real.

The full data-acquisition spec — required fields by tier, resolution, coverage
gate, storage schema, the two acquisition routes and what each does / does not
unlock, and the pre-declared acceptance criteria — is
`ORDERFLOW_STAGE9_L2_RESEARCH.md` §6b.
