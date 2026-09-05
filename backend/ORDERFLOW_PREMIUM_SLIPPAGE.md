# Order-flow backtest: INDEX points vs OPTION premium points

**Status:** MEASURED, NOT FIXED. Read-only investigation only.
**Raised by:** operator, 2026-09-05 — *"target sathi index target achieve hotil
pan option strike CE/PE honar nahi — delta mule, aani index chya move peksha
option premium kami move hoto; to detect karne jaruri aahe; aadhi test kara mag
fix karu."*
**Script:** `backend/scripts/orderflow_premium_vs_index.py` (standalone, no app
imports, no writes).

---

## 1. The gap

`app/orderflow/smart_money.py` + `backtest.py` build the setup **entirely in
index points**:

| leg | value |
|-----|-------|
| entry | spike candle high (BUY) / low (SELL) — **index** |
| stop  | `stop_frac × (high−low)` — **index points** |
| target| `rr × stop_distance` — **index points** |
| outcome | walked forward on the **index** 5m bars |
| `points` in the result | **index points**, i.e. it implicitly assumes the
tradable instrument moves 1:1 with the index (**effective delta = 1.0**) |

A real order-flow trade is a **long ATM CE or PE**. Its premium moves by roughly
`delta × ΔS − theta·Δt ± IV drift`, and `delta ≈ 0.5` at entry, dropping toward 0
as the option goes OTM. So the backtest's TARGET_HIT / STOP_HIT and its
`net_points` are **index-space fiction** for anyone trading the option.

## 2. What the data says

Captured, aligned series in `market_history.db`:
`quote_snapshots(kind='INDEX'|'FUTURE')` → underlying LTP;
`quote_snapshots(kind='OPTION')` → per-strike CE/PE premium;
`option_greeks` → broker delta/theta (NSE index underlyings only).

Method: per session, per horizon H ∈ {15, 30, 45} min stepped every 5 min —
`ΔS = index(t+H) − index(t)`, keep `|ΔS| ≥ min_move`, ATM = strike nearest
`index(t)`, directional leg = CE if `ΔS>0` else PE, then
**capture = ΔP_dir / |ΔS|** (premium points won per index point) and
**effective delta = ΔP_dir / ΔS**. RTH-only, frozen pre-open quotes dropped.

### NIFTY — 3 sessions (2026-09-02..04), min_move 8 pts

| horizon | samples | capture median | capture IQR | broker Δ at entry (median) | capture when premium actually moved |
|--------:|--------:|--------------:|:-----------:|:--------------------------:|:-----------------------------------:|
| 15 min | 120 | **0.40** | 0.17–0.54 | 0.50 | 0.46 |
| 30 min | 143 | **0.40** | 0.20–0.52 | 0.50 | 0.44 |
| 45 min | 144 | **0.37** | 0.21–0.51 | 0.50 | 0.39 |

### MCX (future underlying; no broker greeks captured for MCX)

| symbol | horizon | samples | capture median | capture (premium rose) |
|--------|--------:|--------:|--------------:|:----------------------:|
| CRUDEOIL   | 30 min | 469 | **0.39** | 0.44 |
| NATURALGAS | 30 min | 16  | 0.51 (n too small) | 0.51 |

## 3. Findings

1. **Effective capture ≈ 0.4 premium points per index point** — consistently,
   across symbols and horizons. The backtest assumes 1.0. That is a
   **~2.2–2.5× overstatement** of every points figure it prints.
2. **Realized capture (~0.40) is *below* the ATM broker delta (~0.50).** The
   ~0.10 shortfall is theta bleed + bid/ask + intra-window adverse excursion
   pulling delta down. This script does **not** model spread or theta explicitly,
   so the true tradable capture is a little worse still.
3. **It is asymmetric, and not in the trade's favour once costs are added.**
   On a winner the option goes ITM and delta rises toward ~0.7 (capture-when-
   premium-rose ≈ 0.44). On a loser delta falls toward ~0.3 so the premium loss
   is smaller than `Δ_entry × stop` — but theta and spread are *fixed* costs
   that fall entirely on the (more frequent) losers. Net: the premium-space RR
   is worse than `index_RR × capture`, not better.
4. **Direction of the conclusion:** the index-space backtest is already
   net-negative on every symbol / parameter combo (see the trail+filter sweep,
   commit `542497c`). Re-scoring into premium space makes it **more** negative,
   never less. Nothing here rescues an edge.
5. **Data-quality caveats:** 2026-09-02 is the first NIFTY option-capture day —
   tiny index range (65 pts), many frozen quotes → a "premium didn't move"
   cohort (`capture = 0`) that drags the pooled median down; the "premium rose"
   sub-median (~0.44) is the cleaner read. MCX has **no** captured greeks.
   Only **3 aligned sessions** — DESCRIPTIVE ONLY, far below any reliability
   floor. **No slippage constant should be hard-coded from this yet.**

## 4. Fix options (NOT applied — for operator decision)

| # | approach | cost | fidelity |
|---|----------|------|----------|
| A | Add a `premium_capture` factor (config, default ~0.4) that scales `points` and re-derives a premium-space RR + expectancy alongside the index-space ones. Cheap, transparent, one number to tune as capture data grows. | low | rough |
| B | Re-walk the outcome on the **captured ATM option premium series** instead of the index bars (we have `quote_snapshots kind='OPTION'`). True premium P&L incl. the delta path, for sessions where option capture exists; index-only fallback otherwise. | medium | high (data-limited) |
| C | Model `ΔP ≈ delta·ΔS + 0.5·gamma·ΔS² − theta·Δt` from `option_greeks` at entry. No extra price series, but needs greeks (NSE only) and still ignores IV moves. | medium | medium |

Recommendation: **B for sessions with option capture, A as the fallback and as
the headline caveat**, once ≥10 aligned sessions exist. Until then the backtest
should at minimum **print the ~0.4 capture caveat** next to its `net_points`.

## 5. Reproduce

```
cd backend
python scripts/orderflow_premium_vs_index.py --symbol NIFTY
python scripts/orderflow_premium_vs_index.py --symbol CRUDEOIL --rth-start 540 --rth-end 1410 --min-move 3
python scripts/orderflow_premium_vs_index.py --symbol NIFTY --json   # machine-readable
```

---

## 6. Option B IMPLEMENTED (2026-09-05, commit follows this doc)

Per operator: *"option B implement kara — captured premium series var re-walk."*

**What was built** (read-only, PAPER only, no order path, NIFTY AutoScalp untouched):

- `market_hub.session_option_quotes(sym, date)` → `{(strike, "CE"/"PE"): [(ts, ltp), …]}`
  from `quote_snapshots` (market_hub stays the single DB owner).
- `app/orderflow/premium_walk.py` — `rewalk_leg()`: pick the ATM strike nearest
  the **index** entry, take `premium_entry` as-of the breakout bar and
  `premium_exit` as-of the bar the **index** resolved on, `premium_points =
  exit − entry` (long option). `premium_mfe/mae` from the tick path;
  `premium_thin` when ≤2 captured ticks in the window (stale-quote guard).
- `backtest(..., basis="index"|"premium")`. `basis="premium"` keeps the index
  entry/stop/target as the **exit trigger** and only re-prices P&L; WIN/LOSS is
  by premium-P&L sign, so an index target the option didn't follow shows as a
  loss. Per-trade fallback to index basis when no series covers the window,
  reported in `basis_coverage` (`premium_repriced`, `premium_thin_quotes`,
  `index_fallback`, `premium_coverage`).
- API `?basis=`, service cache-keyed on it, dashboard "Basis" selector +
  coverage line. Tests: `test_orderflow_premium_walk.py` (8). Suite 556 pass.

**Results over the 3–4 captured sessions (2026-09-01..04):**

| symbol | index-basis net | premium-basis net | W/L/F (premium) | coverage |
|--------|----------------:|------------------:|:---------------:|:--------:|
| NIFTY      | −450 pts | **−51 pts** | 6 / 15 / 5 | 23/26 repriced, 3 fallback, 5 thin |
| NATURALGAS | −49 pts  | **+10 pts** | 39 / 45 / 19 | 100/103, 17 thin |
| CRUDEOIL   | −406 pts | **+697 pts** | 36 / 39 / 20 | 93/95, 20 thin |

**Read this carefully — the positive MCX numbers are NOT an edge:**

1. **Mechanism is real:** a long option has capped downside (≈ premium paid) and
   uncapped upside; on a winner delta expands, so premium-basis softens every
   index loss (NIFTY −450 → −51 is the clean read).
2. **CRUDEOIL +697 is artifact-laden.** The net is carried by ~8 winners of
   +50…+85 premium pts on 3 **trending** sessions, with **long holds** (up to
   2.5 h — the trade only exits when the *index* hits its level, there is no
   premium stop), some **exact-duplicate** trades (two adjacent spike candles →
   same ATM strike, same entry/exit ticks → the win counted twice), and MCX
   **illiquidity** (median 28 ticks/window, 20 of 95 trades ≤2 ticks → `thin`).
   The `median` premium P&L is **0.0**.
3. **3 sessions, one regime, no MCX greeks.** `reliable=False` everywhere.
   DESCRIPTIVE ONLY. **No edge claim. No config change. Not armed for live.**

**Known limitations of the current B implementation (future work):**
- entry/exit premium sampled *as-of the 5m bar* (same bar-granularity caveat as
  the whole backtest); no bid/ask, no explicit theta line item beyond what the
  LTP path already contains.
- no premium-side stop — exit is purely index-triggered. A real long-option
  trader would likely cut the premium sooner; adding an optional premium stop is
  the obvious next lever.
- exact-duplicate correlated setups are not de-duplicated (pre-existing, affects
  index basis too).
- run it as `backtest(..., basis="premium")` or `GET /api/orderflow/backtest?
  …&basis=premium`; always check `basis_coverage` before reading the numbers.
