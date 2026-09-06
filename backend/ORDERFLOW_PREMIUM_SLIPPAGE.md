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
- exact-duplicate correlated setups are not de-duplicated (pre-existing, affects
  index basis too).
- run it as `backtest(..., basis="premium")` or `GET /api/orderflow/backtest?
  …&basis=premium`; always check `basis_coverage` before reading the numbers.

### 6a. Optional premium-side stop (added 2026-09-05)

`premium_walk.rewalk_leg(..., premium_stop_pct=)` and
`backtest(..., basis="premium", premium_stop_pct=)` — a hard stop on the
**option**: `0.30` = exit the moment a captured tick is ≥30% below the entry
premium, *if* that comes before the index-triggered exit. It only ever
*shortens* the hold. `basis_coverage.premium_stop_hits` counts how often it
bound; API `?premium_stop_pct=`, dashboard "Prem stop" selector.

**Re-run result: at the current RR / `stop_frac` the premium stop barely
binds.** Sweep over the captured sessions:

| pstop | NIFTY net (hits) | NATURALGAS net (hits) | CRUDEOIL net (hits) |
|------:|:---------------:|:---------------------:|:-------------------:|
| off   | −51.2 (0) | +9.7 (0) | +696.5 (0) |
| −8%   | −49.2 (8) | +9.2 (2) | +705.9 (3) |
| −10%  | −47.6 (3) | +9.7 (0) | +696.6 (1) |
| −12%  | −49.0 (2) | +9.7 (0) | +696.5 (0) |
| −20%  | −53.0 (1) | +9.7 (0) | +696.5 (0) |
| −35%+ | −51.2 (0) | +9.7 (0) | +696.5 (0) |

Why: the ATM option's **adverse excursion inside these short index-defined
windows is tiny** — CRUDEOIL premium-trade MAE distribution is p10 −5%,
median −0.4%, worst −11.3%. The *index* stop (≈10–40 pts) always resolves the
trade before a ≥12% premium drawdown occurs, so any premium stop looser than
~8% is a no-op, and an ~8–10% stop moves NIFTY by only ~+3 pts on n=26 —
noise.

### 6b. Absolute-points premium stop + pattern variants (added 2026-09-06)

**Absolute-points premium stop** — `premium_stop_pts=` (alongside
`premium_stop_pct`; whichever level is tighter binds first). API
`?premium_stop_pts=`, `basis_coverage.premium_stop_pts`.

Sweep (basis=premium, pattern=spike):

| pstop_pts | NIFTY net (hits) | NATURALGAS net (hits) | CRUDEOIL net (hits) |
|----------:|:---------------:|:---------------------:|:-------------------:|
| off  | −51.2 (0)  | +9.7 (0) | +696.5 (0) |
| 10   | −45.1 (4)  | +9.7 (0) | +618.4 (24) |
| 15   | −57.0 (2)  | +9.7 (0) | +673.6 (11) |
| 20   | −51.2 (0)  | +9.7 (0) | +719.2 (5)  |
| 30+  | ~unchanged | +9.7 (0) | ~unchanged  |

Same verdict as the % stop: **no consistent improvement.** A tight abs stop
(10 pts) *cuts CRUDEOIL winners early* (+618 vs +696, 24 hits); 20 pts is
marginally better (+719) but well within noise on n≤95 / 3–4 sessions.
NATURALGAS never triggers (premiums are small, drawdowns tiny). **The premium
stop — % or points — does not change the picture at these RR / stop_frac
settings. Keep it off; revisit only with wider targets or more data.**

**Pattern variants** — `smart_money_setups(..., pattern=)` /
`backtest(..., pattern=)`, dashboard "Pattern" selector:
- `spike` (default) — volume spike, unchanged.
- `sideways_spike` — a volume spike whose prior `consol_lookback` (5) bars
  were coiled within `consol_span_x` (1.5) × the avg bar range.
- `hammer` — hammer / shooting-star candle; the wick fixes the side (long
  lower wick → BUY above the high, long upper wick → SELL below the low). No
  volume filter — the shape is the trigger.

Results (all 3–4 captured sessions, `reliable=False` throughout):

| symbol · basis | spike net (n) | sideways_spike net (n) | hammer net (n) |
|----------------|--------------:|-----------------------:|---------------:|
| NIFTY · index    | −450 (26) | −46 (**4**)  | −460 (38) |
| NIFTY · premium  | −51 (26)  | +3 (**4**)   | −81 (38)  |
| NATGAS · index   | −49 (103) | −1 (**1**)   | +1 (59)   |
| NATGAS · premium | +10 (103) | −0.2 (**1**) | +11 (59)  |
| CRUDE · index    | −406 (95) | +92 (**2**)  | −209 (93) |
| CRUDE · premium  | +696 (95) | +74 (**2**)  | +226 (93) |

- **`sideways_spike` produces almost no signals** (1–4 per symbol) at the
  default filter — these 3–4 trending/choppy sessions rarely coil first.
  Loosening `consol_span_x` to 2.5–3.5 just recovers signals that behave like
  plain `spike` (still net-negative on index basis). **Nothing to conclude
  from n≤4.**
- **`hammer` produces a comparable signal count to `spike`** (38–96) and is a
  genuine alternative detector. On this sample it does **not** beat spike:
  NIFTY worse, CRUDE worse on premium basis (+226 vs +696), NATGAS about the
  same (+11). NATGAS hammer-premium PF looks high (6.1) but is a
  FLAT-heavy/small-points artifact.

**Overall: neither the absolute premium stop nor the two new patterns changes
the conclusion. No edge, no config change, nothing armed. The detectors and
stops are in place as tunable levers for when ≥10 clean sessions across
regimes exist.**

---

## 7. Composite-setup ablation A–G (2026-09-06) — RESEARCH ONLY

`backend/scripts/orderflow_composite_ablation.py` — standalone, no production
path (imports only pure helpers + the read-only `market_hub` / `premium_walk`).
Tests whether **sideways compression + abnormal range/volume spike + rejection
candle + breakout confirmation (+ index-profile value-area break)** has an edge
vs the isolated triggers.

Strict methodology, per operator: completed candles only; every look-back is
causal (`bars[:idx]`), including the **developing** volume profile used by gate
G; thresholds swept (spike 2 / 2.5 / 3×, comp_lb 5 / 8, comp_x 1.5 / 2.0 / 2.5,
volume filter on/off) as sensitivity analysis, **not** loosened to raise the
signal count; compression kept strict. Premium basis (P&L re-priced on the
captured ATM option; thin-quote windows dropped). Edge bar: **≥10 independent
sessions AND ≥50 trades, across regimes, walk-forward stable.**

Ablation: A spike-only · B hammer-only · C sideways+spike · D sideways+hammer ·
E sideways+spike+hammer · F = E + breakout confirmation · G = F + index-profile
value-area break.

### Result — the composite essentially does not occur in the captured data

| variant | NIFTY trades | NATGAS trades | CRUDE trades |
|---------|:-----------:|:-------------:|:------------:|
| A spike-only  | 1 | 48–192 | 16–90 |
| B hammer-only | 44 | 27–114 | 15–96 |
| C sideways+spike        | **0** (all 36 combos) | 1–9  | 1–14 |
| D sideways+hammer       | 1–2 | 1–4  | 1–5  |
| E +rejection            | **0** | 1    | **0** |
| F +confirmation         | **0** | 1    | **0** |
| G +index-profile gate   | **0** (all 36 combos) | **0** (all) | **0** (all) |

- The **profile gate itself works** (fires 2–24×/session in isolation) — G is
  0 because requiring compression AND ≥2× expansion AND wick-rejection AND a
  later-bar confirmation AND a developing-VA break on the *same* trigger candle
  never co-occurs in 4 sessions. Every component is sound; the conjunction is
  simply too selective to sample here.
- The only variants with a real trade count are the **isolated** A and B. Their
  best positive-expectancy configs do **not** hold up:
  - CRUDE A (spike 3×): n=16, exp +11.5 — but session 09-02 was −96.6 (0 % win);
    nominal walk-forward train exp −32 vs test exp +22 (opposite signs).
  - NIFTY B (hammer): n=44, exp +2.3 — but 09-04 net −32 (exp −2.0), maxDD −45.
  - NATGAS B: n=27, PF 6.5 but exp only +0.34 (tiny absolute); PF inflated by a
    single session (09-03 PF 15, asym 14 — small-sample noise).

### Verdict

| target | classification | why |
|--------|----------------|-----|
| **Composite (C–G, the thing asked)** | **(4) data / artifact — insufficient occurrences** | 0–4 trades in 4 sessions; cannot distinguish "no edge" from "rare but real". |
| **Isolated A / B** | **(3) no established edge** (charitably (2) for B) | positive-looking configs fail across sessions and the nominal train/test split; < 10 sessions, mostly < 50 trades, ≤ 2 regimes. |

**No setup reaches a statistically or operationally meaningful edge.** Per the
operator's instruction, **production behaviour is left unchanged** and **no
pattern is enabled for live trading.** Re-run the ablation when ≥ 10
independent sessions across regimes have been captured:

```
cd backend && python scripts/orderflow_composite_ablation.py
python scripts/orderflow_composite_ablation.py --symbols NIFTY --comp-x 1.5,2,2.5,3
```

---

## 8. Sequence research engine (2026-09-06) — RESEARCH ONLY, no edge proven

Per the extended 2026-09-06 spec: model COMPRESSION → ABNORMAL SPIKE →
REJECTION → CONFIRMATION → ENTRY as **sequential events over a multi-bar
window** (not one candle); do NOT assume the spike candle is the entry;
research dynamic spike definitions, entry location, structural stops,
R-multiple management, and the MFE-in-R distribution.

`backend/scripts/orderflow_sequence_research.py` — standalone, causal
(completed candles only, `bars[:idx]`, developing volume profile), premium
basis, thin quotes dropped. Full run saved to
`backend/data/orderflow_sequence_research_2026-09-06.txt`. **Nothing wired to
the live engine / API / dashboard / config; no pattern enabled for live.**

### P1 — what is an "abnormal spike" (it is NOT one universal multiple)

| symbol | best usable spike def | why |
|--------|-----------------------|-----|
| **NIFTY** | percentile ≥ p90 of prior-bar ranges (n=7/3 sess) | a fixed 2×/2.5× range expansion happens ~1×/session; 3×/4× and volume defs → 0–3 trades. Range-abnormality barely exists in this vol regime. |
| **NATGAS** | **volume ≥ 2–3× causal avg** (vol3.0: n=50, exp +0.18, PF 1.98, avg maxR 1.96) | volume-abnormality beats every range multiple; fixed 4× goes negative (too rare/late). |
| **CRUDE** | **fix 2.5× range** (n=26, exp +7.2, PF 2.2) or **vol ≥ 2×** (n=72, exp +5.3, PF 2.3, maxDD −52) or **profile-aware** (n=31, exp +7.1, PF 2.6, tightest MAE) | multiple defs work; fixed 4× → 3 trades, negative. |

Cross-symbol: **fixed 4× is too strict everywhere** (rare + late → negative).
"Most trades" (pct90, 84–142) ≠ "best trades" (vol/fix2.5, lower count, higher
expectancy & PF) — the quality-first pick is used downstream.

### P2 / P5 — the spike candle is NOT the best entry

From the ablation (A = spike-only S0 entry; B = spike → **rejection candle**
S2 entry, no pre-compression):

| symbol | A (spike/S0) | B (rejection/S2) |
|--------|--------------|------------------|
| NATGAS | n=142, exp +0.11, PF 1.77, Rcap −0.01, maxDD −3.7 | **n=64, exp +0.28, PF 5.97, Rcap +0.61, maxDD −1.1, 3R+ 34 %** |
| CRUDE  | n=84, exp +2.50, PF 1.37, Rcap +0.15, maxDD −143 | **n=29, exp +4.73, PF 2.66, Rcap +0.79, maxDD −29, avg maxR 2.54, 3R+ 45 %** |
| NIFTY  | n=7 | 0 (no rejection candle followed a qualifying spike) |

**Entering on the rejection candle after the spike is materially better** than
entering on the spike itself — half the trades, ~2–3× the expectancy, ~4× less
drawdown, more R reached — on NATGAS and CRUDE. (Q2/Q3/Q4.)

### P3 / P4 / P6 / P7 — inconclusive, and why

Every study that sits behind the **compression** gate (P4 profile modes, P6
stop structures, P7 profit management, P3 sequence window, ablation C–I)
returns **0 trades on all three symbols**. Strict pre-spike compression (prior
5–8 bars span ≤ 1.5× the median bar range) essentially never precedes a
qualifying spike in these 4 sessions. This is the sample-killer — **not**
rejection or confirmation (B, which has rejection but no compression, has a
healthy count). Whether compression is *over-constrained* or *rare-but-high-
quality* cannot be resolved without ≥10 sessions. Confirmation (E/S3) also
drives the count to 0 once stacked on compression. (Q5/Q6/Q8/Q9.)

### P8 — is a 3R target too restrictive?  YES on this data

R-reach distribution (variant A, the only one with n≥50 on NATGAS/CRUDE):

| symbol | 1R+ | 2R+ | 3R+ | 5R+ | 8R+ | median maxR | avg R captured (fix3R) | giveback |
|--------|----:|----:|----:|----:|----:|:-----------:|:----------------------:|:--------:|
| NIFTY  | 71 % | 29 % | 14 % | 0 % | 0 % | 1.36 | +0.55 | 0.84 R |
| NATGAS | 61 % | 35 % | 22 % | 2 % | 0 % | 1.52 | −0.01 | 1.76 R |
| CRUDE  | 57 % | 33 % | 27 % | 1 % | 0 % | 1.21 | +0.15 | 1.47 R |

Only ~1 in 4 trades ever tags 3R; **essentially none reach 5R**; typical trade
peaks near ~1.3–1.5R then round-trips (giveback ~1.5R). A **fixed 3R target is
too ambitious** — a ~1.5–2R target, or a partial at 1R + a structure runner,
is the direction indicated. The rejection variant B reaches 3R more often
(34–45 %) with positive R-captured — better entry ⇒ more of the move. The
runner rules themselves (H/I) are untestable here (gated to 0). (Q7.)

### P9 — leave-one-session-out

Directionally consistent but not stable: CRUDE held-out expectancies +2.2 /
+5.7 / +0.14 (no sign flip, but one near-flat session); NATGAS +0.15 / −0.03 /
+0.13 (one negative). n per fold = 20–61. Not a valid split at 4 sessions.

### VERDICTS

| | classification | notes |
|--|----------------|-------|
| **NIFTY PREMIUM** | **3. NO ESTABLISHED EDGE** (borderline 4 — insufficient occurrences) | the spike, however defined, barely occurs (best variant n=7, 3 sessions). Range-abnormality absent in this regime. |
| **NATGAS PREMIUM** | **2. PROMISING — MORE DATA REQUIRED** | mechanically positive (A n=142 exp +0.10 PF 1.77; B n=64 exp +0.28 PF 6.0) but expectancy is tiny in absolute premium points and LOSO has a negative fold. 3 traded sessions, 3 regimes. |
| **CRUDE PREMIUM** | **2. PROMISING — MORE DATA REQUIRED** | strongest of the three (A n=84 exp +2.5 PF 1.37; B n=29 exp +4.7 PF 2.66 maxDD −29; vol2.0 spike n=72 exp +5.3). LOSO positive on all folds. But large DD on wide variants, one near-flat session, only 2 regimes, 3 sessions. |

**No symbol reaches a PROVEN EDGE. Production behaviour is unchanged; no new
pattern is enabled; no live orders armed.** The concrete leads to re-test once
≥10 independent sessions across regimes exist:
1. spike def per symbol (NIFTY percentile; NATGAS/CRUDE volume≥2×; CRUDE also profile-aware),
2. **enter on the rejection candle after the spike, not the spike candle**,
3. lower the target to ~1.5–2R or partial-at-1R + structure runner,
4. treat pre-spike compression as optional/scored, not a hard gate, until it can be shown to add quality rather than just remove trades.

```
cd backend && python scripts/orderflow_sequence_research.py
python scripts/orderflow_sequence_research.py --symbols CRUDEOIL
```

---

## 9. Per-spike event ledger (2026-09-06) — DESCRIPTIVE ONLY

`backend/scripts/orderflow_spike_ledger.py` walks the operator's per-spike
diagnostic chain and emits one CSV row per abnormal-spike event
(`backend/data/orderflow_spike_ledger.csv`, 930 rows over the 4 captured
sessions). Columns: price level (close/high/low), nearest option strike +
its LTP + OI + 5-min OI delta, developing-profile location + distances to
POC/VAH/VAL, `vol_x` / `range_x` / range percentile, whether a prior level
was broken (which, by how many pts), post-break acceptance vs rejection,
next-candle direction/body/continuation, whether structure formed within
3 bars, the hypothetical entry (S0 spike-close / S2 rejection-close) with a
structural SL each, MFE in R **capped at that SL**, SL-hit bar offset, and
the distance to the next major S/R in R.

**No expectancy, no PF, no verdict, no threshold tuning** — permissive
capture (`rel_range >= 1.8` OR `vol_x >= 1.8`), all metrics in the CSV so any
stricter definition can be applied afterwards. Honours the "wait for more
sessions" hold.

Descriptive picture over the 4 sessions (structural / tight SL):

| | NIFTY | NATGAS | CRUDE |
|--|------:|-------:|------:|
| spike bar-events | 14 | 167 | 153 |
| broke a prior level | 54 % | 57 % | 51 % |
|  ...of which acceptance / rejection | 38 % / 33 % | 47 % / 27 % | 46 % / 21 % |
| structure formed ≤ 3 bars | 13 % | 9 % | 9 % |
| logical-SL hit ≤ 2 bars | 51 % | 52 % | 53 % |
| SL hit before +1R | 26 % | 39 % | 40 % |
| **MFE in R (capped at SL): median** | **1.51** | **1.17** | **1.29** |
|  ...≥ 3R | 36 % | 27 % | 32 % |
| next major S/R distance: median R | 1.87 | 2.0 | 3.14 |
| OI 5m-delta (median, where captured) | ~0 | +38,750 | +1,000 |

Consistent with §8: median reachable excursion ≈ 1.2–1.5R, ≈ 30–40 % of
events stopped out before +1R, ≈ 27–36 % reach 3R, and there is typically
~2R of room to the next level — so a ~1.5–2R target remains the indicated
direction. Spikes break levels slightly more than half the time and are
accepted more often than rejected (mild continuation bias). NATGAS spikes
coincide with large OI additions; NIFTY OI is effectively flat on the 5-min
window. Profile-location vs MFE shows no strong edge at this n.

```
cd backend && python scripts/orderflow_spike_ledger.py            # rebuild the CSV + summary
```

---

## 10. Event-anatomy research layer (2026-09-06) — RESEARCH ONLY, all INSUFFICIENT DATA

`backend/scripts/orderflow_event_anatomy.py` → one row per abnormal event
(`data/orderflow_event_anatomy.csv`, 488 events / 4 sessions; KNOWN-AT-ENTRY
columns first, OUTCOME columns after — §16 causal separation) + a conditional
forward-distribution report (`data/orderflow_event_anatomy_report_2026-09-06.txt`).
Abnormality is a *feature* not a gate (`range_x`, `range_pctile`, `range_atr`,
`vol_x`, `vol_pctile`, displacement all recorded); compression is a continuous
feature; **no score, no weights** (§14). Nothing wired to live.

### The 16 research questions (answers are HYPOTHESES — 3–4 sessions)

1. **What defines an abnormal event?** A range or volume expansion vs the
   causal rolling median / ATR / percentile. Recorded as continuous features;
   a modest expansion (`range_x` ~1.5–2.5) behaves best.
2. **Does the threshold vary by symbol/regime?** Yes, and it is
   **non-monotonic**: CRUDE `range_x ≥ 3` → P3R 9 % vs `range_x 1.5–2.0` → 30 %;
   the largest spikes are exhaustion, not continuation. NIFTY produces almost
   no range spikes at all (30 events / 4 sessions).
3. **Does pre-spike compression help?** **No.** CRUDE `coiled ≤Q1` P3R 21 % vs
   `loose >Q3` P3R 34 %; NATGAS/NIFTY non-monotonic. Confirms §8 — compression
   must not be a gate.
4/5. **Spike candle vs post-spike reaction entry?** Spike-close and
   rejection-candle entry give **near-identical** forward distributions on the
   anatomy layer. The information is not in *which candle you enter on* — it is
   in whether **acceptance** follows (see 6).
6. **Does acceptance add predictive value?** **This is the one strong,
   consistent signal.** Measured as "close beyond the broken level held for N
   subsequent completed candles":

   | accept_3 (3-bar hold) | NIFTY | NATGAS | CRUDE |
   |----------------------|:-----:|:------:|:-----:|
   | ACCEPTANCE — med MFE_R / P3R / continuation | 2.3R / 38 % / 62 % | 2.3R / 40 % / 53 % | **5.0R / 68 % / 77 %** |
   | REJECTION — med MFE_R / P3R / continuation | 0.1R / 0 % / 0 % | 0.3R / 3 % / 6 % | 0.2R / 3 % / 3 % |
   (n per bucket 43–67). Window sensitivity: the separation grows from
   accept_1 → accept_2 → accept_3. `level_interaction = C_broke_accepted` is a
   coarser proxy with the same sign (+7 pp P3R on CRUDE).
7. **Does OI behaviour add value?** **No clear effect** — every price×OI bucket
   sits within ±5 pp of baseline P3R on all three symbols.
8. **Does abnormal volume add value?** **Marginal / none.** `vol_x ≥ 2`:
   CRUDE ±1 pp, NIFTY −14 pp, NATGAS +5 pp. Not a reliable filter.
9. **Does profile location add value?** **Weak.** `acceptance_outside_value`
   and `near_POC` run a few pp above `inside_value` on CRUDE (+5 pp) but the
   opposite on NATGAS. Not a gate.
10. **Does level interaction add value?** Modest, and it tracks acceptance:
    `C_broke_accepted` / `D_failed_immediately` > `B_swept_returned`
    (CRUDE med MFE_R 1.5R / 2.0R vs 0.3R).
11. **Best structural stop MAE/MFE?** The **spike low/high** is a valid
    invalidation — median MAE_R ≈ −1.1 to −1.3 (rarely blown through by much);
    the rejection-candle stop is ~10–20 % tighter in points. Per-stop MAE_R /
    MFE_R for spike / rejection / prior-structure stops are in the CSV.
12–15. **P(kR before invalidation)** (all events, spike-SL):

    | | 1R | 2R | 3R | 5R | 8R | 10R |
    |--|--:|--:|--:|--:|--:|--:|
    | NIFTY  | 47 % | 20 % | 10 % | 3 % | 0 % | 0 % |
    | NATGAS | 51 % | 28 % | 16 % | 8 % | 3 % | 2 % |
    | CRUDE  | 52 % | 34 % | 26 % | 20 % | 9 % | 6 % |
    A fixed 3R target is realistic for **CRUDE**, aggressive for NIFTY/NATGAS —
    **but conditioned on acceptance, CRUDE P3R rises to 55–68 % and P5R to
    42–51 %**, which is the regime where a 3–5R target makes sense.
16. **Combinations worth a second-stage test** (once ≥10 sessions):
    (a) **ACCEPTANCE within 2–3 bars of a level break** — dominant, all symbols;
    (b) `C_broke_accepted` level interaction (coarser proxy);
    (c) `acceptance_outside_value` profile class (CRUDE only, small).
    Everything else — volume, OI, compression, spike magnitude, expansion
    class — showed no usable effect and does **not** warrant a second stage.
    (NATGAS: pure `expansion` candles did −7 pp vs baseline — a big body is
    not bullish for continuation.)

### FINAL CLASSIFICATION

| symbol | classification | basis |
|--------|----------------|-------|
| **NIFTY PREMIUM**  | **INSUFFICIENT DATA** | 30 events / 4 sessions / 2 regimes; range spikes barely occur. |
| **NATGAS PREMIUM** | **INSUFFICIENT DATA** | 237 events / 4 sessions / 3 regimes; acceptance signal present but tails thin (P5R ~8–16 %). |
| **CRUDE PREMIUM**  | **INSUFFICIENT DATA** | 221 events / 3 sessions / **1 regime (CHOP only)**; the acceptance signal is strongest here but has never been seen in a trending CRUDE session. |

**No edge is claimed. No production pattern created or enabled. No live
behaviour changed. No orders armed.** The one hypothesis that survives to a
second stage is *acceptance-confirmed continuation after an abnormal level
break* — to be re-tested with a proper train / validation / out-of-sample
split once ≥10 independent sessions across regimes exist.

```
cd backend && python scripts/orderflow_event_anatomy.py            # rebuild CSV + report
```

---

## 11. Continuation vs Trap research (2026-09-06) — RESEARCH ONLY, all INSUFFICIENT DATA

`backend/scripts/orderflow_continuation_trap.py` (report:
`data/orderflow_continuation_trap_report_2026-09-06.txt`). Builds on the
event-anatomy events; does not replace that dataset. Question: can abnormal
spikes be separated into CONTINUATION vs TRAP/REVERSAL, and what distinguishes
them? Strictly causal; no score, no weights; nothing wired to live; no pattern
enabled; no orders.

### The result: acceptance vs failed-acceptance is a clean, cross-symbol split

`§4 acceptance-hold sweep` — spike-close-entry forward distribution conditioned
on whether the close beyond the broken level HELD for `hold` completed candles:

| symbol | hold | ACCEPT: cont% / trap% / medMFE_R (n) | REJECT: cont% / trap% / medMFE_R (n) |
|--------|:---:|:-----------------------------------:|:------------------------------------:|
| NIFTY  | 2 | 69 / 15 / 1.34R (13) | 0 / 77 / 0.17R (13) |
| NIFTY  | 3 | 100 / 0 / 2.12R (9)  | 0 / 71 / 0.33R (17) |
| NATGAS | 2 | 81 / 7 / 1.52R (75)  | 0 / 75 / 0.65R (126) |
| NATGAS | 3 | 97 / 0 / 1.61R (63)  | 0 / 72 / 0.65R (138) |
| CRUDE  | 2 | ~83 / ~8 / — (82)    | ~0 / ~78 / — (98) |

Failed acceptance → **0 % continuation, ~72–77 % trap, median MFE ≈ 0.2–0.65R**
on all three symbols. Acceptance → majority continuation, MFE ≈ 1.3–2.1R. The
split **stabilises at hold = 3** (hold 3/4/5 near-identical) — 3 candles is the
natural confirmation window; more adds nothing.

### Hypothesis tests (H1–H8)

| H | statement | verdict | evidence |
|---|-----------|---------|----------|
| **H1** | spike + acceptance > spike alone (continuation) | **SUPPORTED, all 3 symbols** | ΔP(cont) = +36 pp (NIFTY, n13), +49 pp (NATGAS, n75), +47 pp (CRUDE, n82) |
| **H2** | failed acceptance → higher reversal | **SUPPORTED, all 3 symbols** | ΔP(trap) = +27 / +25 / +26 pp |
| H3 | post-spike reaction info > spike magnitude | inconclusive (D-entry P3R crushed by inflated R) — but see n1-direction below |
| H4 | level interaction > raw volume | level effect > volume effect on P3R spread, all 3 |
| H5 | profile location changes the distribution | continuation-% spread across profile classes ≈ 0.40 (NIFTY) — material but n-thin |
| **H6** | extremely large spikes = exhaustion | **NOT supported** — pctile ≥ 0.98 spikes show ~40 % continuation, same as moderate; big spikes are not systematically traps |
| H7 | available space determines large-R feasibility | inconclusive — NIFTY has almost no `available_R ≥ 3` events |
| **H8** | the best entry is not the earliest | **SUPPORTED** — `A_spike` never has the best P3R; `B_reaction`/`C_rejection` beat it (+11 pp NIFTY, +2–4 pp NATGAS/CRUDE) |

### Other observations

- **`n1` direction is a fast trap tell** (known 1 bar after the spike):
  first post-spike candle *disagrees* with the spike → 7–17 % continuation,
  71–79 % trap, median MFE ≈ 0.2–0.5R; *agrees* → 47–56 % continuation, P3R
  31 % (NATGAS). Nearly as strong as acceptance, available 2 bars earlier.
- **`B_swept_returned` is the trap signature** — 0 % continuation, 89–90 %
  trap on NIFTY and NATGAS.
- **Structural stop (§10):** swing / prev-structure stops give a lower
  median MAE_R (−0.79 vs −1.03) but a larger R, which collapses `available_R`.
  The spike low/high stop has median MAE_R ≈ −1.0 to −1.3 — a valid
  invalidation that is not routinely blown through.
- **Available reward (§11):** NIFTY median `available_R` ≈ 0.7 — structurally
  there is almost no room; NATGAS/CRUDE median ≈ 1.1. Very few events have
  `available_R ≥ 3`.
- **Runner (§12), on 3R-reaching events only:** fixed 3R beats both runner
  variants on this sample — E[final_R] 3.0 vs 2.3–2.4 (50 % + runner) vs
  1.7–1.8 (full runner); median max_R only ≈ 3.4–3.8, so there is rarely a
  large trend to run. n = 3 (NIFTY) / 43 (NATGAS).
- **Late entry costs R:** the acceptance-confirmation entry (D) has the best
  continuation/trap ratio but the *worst* P3R, because waiting 3 candles
  inflates entry-to-structural-SL distance. The acceptance *label* is the
  value; entering only after full confirmation gives the R back.

### FINAL STATUS

| symbol | status | basis |
|--------|--------|-------|
| **NIFTY PREMIUM**  | **INSUFFICIENT DATA** | 30 events / 4 sessions / 2 regimes; `available_R` ≈ 0.7 (no room). |
| **NATGAS PREMIUM** | **INSUFFICIENT DATA** | 237 events / 4 sessions / 3 regimes; acceptance split clean at n = 75/126 but P5R only ~11 %. |
| **CRUDE PREMIUM**  | **INSUFFICIENT DATA** | 221 events / **3 sessions / CHOP only**; acceptance split clean but never observed in a trending CRUDE session. |

**No edge claimed. No production pattern created or enabled. No live
behaviour changed. No orders.** Carried to Stage-2 (needs ≥ 10 independent
sessions across regimes + a train/validation/out-of-sample split):
**acceptance (hold ≥ 2) after an abnormal level break**, ideally combined
with `available_R ≥ 3` and/or `n1` agreeing — the `accept≥2 & avail_R≥3`
cell (NATGAS n = 14) showed 86 % continuation / 7 % trap.

```
cd backend && python scripts/orderflow_continuation_trap.py
```

---

## 12. Entry timing + structural risk + available R (2026-09-06) — RESEARCH ONLY, all INSUFFICIENT DATA

`backend/scripts/orderflow_entry_timing.py` → `data/orderflow_entry_timing_report_2026-09-06.txt`.
Builds on the continuation-vs-trap events; does not alter the dataset.
Question: *what is the earliest entry that keeps a good continuation/trap
trade-off while preserving structural reward?* Four causal entry timings, each
with its own valid structural stop and `available_R`. No score, no weights;
nothing wired to live; no pattern enabled; no orders.

### §8 Entry-quality matrix (each entry, its primary structural stop, window 3)

| entry | valid at | NIFTY cont/trap/P3R/availR/R | NATGAS | CRUDE |
|-------|----------|:---------------------------:|:------:|:-----:|
| **A** spike-close | spike close | 33/50/10 % · 0.7R · 22pt | 32/51/16 % · 0.8R · —pt | 36/52/27 % · 1.2R · 21pt |
| **B** first-reaction (n1 agrees) | 1st post-spike close | 56/25/12 % · 0.4R · 27pt | 48/30/15 % · 0.7R | 57/28/24 % · 0.8R · 32pt |
| **C** early-acceptance (1st close beyond level, no reclaim) | 1st close beyond L | **80**/20/10 % · 0.4R · 31pt | **74**/16/12 % · 0.9R | **72**/18/24 % · **1.7R** · 36pt |
| **D** full-acceptance (hold 2) | after 2 held closes | 100/0/11 % · 0.8R · 47pt | 80/0/11 % · 1.3R | 88/0/21 % · 1.7R · 43pt |

### §9 The trade-off, stated plainly

Later entry buys **accuracy** (continuation% 32→80–100 %, trap% 50→0 %) but
**pays it back in R distance** — entry-to-structural-stop grows ~2× (NIFTY
22→47 pt, CRUDE 21→43 pt), so **P3R does NOT improve and often falls** (CRUDE
27 %→21 %). The most accurate entry is not the best entry for reaching large R.

### §16 Sweet spot

- The single largest accuracy gain is **A → C**: continuation jumps to
  72–80 % at essentially the *earliest* possible bar (the first close beyond
  the level). The `close_no_reclaim` refinement is what earns it — raw
  `first_close` is only 45–74 % continuation, `close_no_reclaim` 72–80 %
  (§5 sweep). Waiting the extra 2 candles for full acceptance adds little
  accuracy and costs R.
- **C early-acceptance entry + the first-reaction candle's low/high as the
  stop** is the standout risk geometry on CRUDE: median R **12.75 pt**
  (vs 35 pt for the spike stop), median MFE_R **1.53R**, **median
  available_R ≈ 5.0** (vs 1.7). The reaction-candle extreme is a tight *valid*
  invalidation (the low the acceptance held above); it is hit more often
  (median MAE_R −1.48) but transforms the reward geometry. Needs validation.
- Multi-criterion rank (transparent, not a score): CRUDE → early-acceptance
  best; NIFTY → full-acceptance (but NIFTY has no room anywhere, availR ≈
  0.4–0.8); NATGAS → spike-close / early-acceptance ~tie.

### §4/§6/§7 Earliest reliable evidence

| signal (known 1 bar after the spike) | NIFTY | NATGAS | CRUDE |
|--------------------------------------|:-----:|:------:|:-----:|
| **n1 AGREES** → cont% / trap% / P3R | 56 / 25 / 19 % | 47 / 29 / 28 % | 56 / 28 / **43 %** (P5R 27, P8R 11) |
| **n1 DISAGREES** → trap% / cont% | 79 / 7 % | 71 / 17 % | 75 / 17 % |
| n1 re-enters the spike range | ~100 % trap (n small) | — | — |
| n1 closes back INSIDE the level | 89 % trap | — | — |

Earliest continuation evidence = **n1 agrees with the spike direction**;
earliest trap warning = **n1 disagrees / re-enters the spike range**.

### §10 Large-R — does the room exist?

Only **CRUDE** produces meaningful large-R: A-entry P3R 27 % / P5R 18 % /
P8R 8 %, rising to **P3R 43 % / P5R 27 % / P8R 11 %** on the `n1-agrees`
subset; p90 max_R ≈ 4R+. NATGAS modest (P3R 16 %, P8R 3 %). NIFTY negligible
past 3R (P5R ≈ 3 %) and structurally has no room.

### §13 OI / volume as secondary evidence

Marginal after controlling for level interaction + acceptance — `vol_x ≥ 2`
and price-dir==OI-dir move `C_broke_accepted` P3R by only a few pp on all
three symbols. **They do not add usable information at this stage.**

### §17 FINAL STATUS

| symbol | status |
|--------|--------|
| **NIFTY PREMIUM** | **INSUFFICIENT DATA** — 30 events / 4 sessions / 2 regimes; no structural room. |
| **NATGAS PREMIUM** | **INSUFFICIENT DATA** — 237 events / 4 sessions / 3 regimes; large-R tail thin. |
| **CRUDE PREMIUM** | **INSUFFICIENT DATA** — 221 events / 3 sessions / CHOP only; the sweet-spot geometry (C entry + reaction stop, n1-agree filter) is strongest here but has never been observed in a trending CRUDE session. |

**No edge claimed. No production signal. No live change. No orders. No
score.** Stage-3 hypotheses (need ≥ 10 independent sessions across regimes +
chronological train/validation/OOS): (1) **n1-agree** as the earliest gate;
(2) **early-acceptance (`close_no_reclaim`)** as the entry; (3) **first-
reaction-candle low/high** as the stop; (4) require **available_R ≥ 3**.

```
cd backend && python scripts/orderflow_entry_timing.py
```
