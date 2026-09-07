# IMBALANCE_NEXT_CANDLE_1R3 — multi-timeframe + spike + OI/PCR + S/R research

**Headline (spec §11, decided up front): `NOT VALIDATED — GENUINE L2 REQUIRED`.**

This environment has **no aggressor-classified data** — no tick feed, no
per-trade side, no trade delta, no order-book event stream. The Angel mode-3
SnapQuote capture that was just wired carries best-5 depth but *still* no
aggressor side. So every "imbalance" measured here is a **PROXY** and is
labelled as such. Nothing below is promoted to genuine L2, nothing is `PROVEN`,
nothing touches the frozen H1/H7 classifier or the live trading / entry / SL /
target / risk / broker / execution path. No production wiring.

- Module: `backend/scripts/imbalance_next_candle_1r3_research.py` (read-only, standalone)
- Event table: `backend/data/imbalance_next_candle_1r3_events.csv` (13,030 rows, git-ignored)
- Related: `ORDERFLOW_L2_IMBALANCE_GATE.md`, `ORDERFLOW_STAGE9_L2_RESEARCH.md` §6b, `L2_SNAPQUOTE_CAPTURE.md`

## Proxies used (none is genuine L2)

| proxy | definition | data | sessions |
|---|---|---|---|
| `passive_book` | `bid_qty / ask_qty` (resting size) as-of the candle close | `quote_snapshots` FUTURE, ~25–30 s snapshots | **3** (2026-09-02…04), NIFTY/CRUDEOIL/NATURALGAS, one regime |
| `vol_range` | abnormal candle: `range ≥ 1.5×median` **and** `volume ≥ T×median` | `market_candles` FUTURE 1m→5m→30m | **4** (2026-09-01…04) |
| `range_only` | `range ≥ T×median` (no volume, no book) | Kaggle NIFTY-50 5m cash index | **2,652** (2015-01…2026-05) — *shape only*, informs structure questions, not L2 |

## Mechanics (per spec §3)

N = imbalance candle, N+1 = next candle. After N+1 closes: **GREEN N+1** → buy-stop @ N+1 high, SL @ N+1 low, `R = high−low`; **RED N+1** → sell-stop @ N+1 low, SL @ N+1 high. Targets **1:2 / 1:3 (primary) / 1:4**. **25-minute max lifetime**; if entry never triggers → `NO_TRIGGER`; if triggered but neither target nor SL by +25 min → `TIMEOUT`, scored **mark-to-market at the 25-min bar close** (not the favourable excursion). No bar after +25 min is used. Multi-TF (1m/5m/30m) resampled per session with no partial/future candle; MTF "confirmation" = a same-direction abnormal candle on the other TF whose window closed at/before N's close.

## Results (PROXY — descriptive only)

### Futures passive_book / vol_range proxy (3–4 sessions, one regime)

| cut | signals | trig | tgt% | SL% | TO% | E[R] @1:3 | PF |
|---|--:|--:|--:|--:|--:|--:|--:|
| passive_book ≥200% | 2191 | 1904 | 19.4 | 68.3 | 12.2 | **−0.03** | 0.96 |
| passive_book ≥300% | 1720 | 1501 | 20.0 | 67.9 | 12.1 | −0.01 | 0.98 |
| passive_book ≥400% | 1397 | 1219 | 20.0 | 67.9 | 12.1 | −0.01 | 0.98 |
| passive_book ≥500% | 1197 | 1050 | 20.0 | 67.8 | 12.2 | −0.01 | 0.99 |
| vol_range ≥200% | 844 | 733 | 17.7 | 67.0 | 15.3 | −0.03 | 0.96 |
| vol_range ≥400% | 363 | 316 | 19.3 | 64.9 | 15.8 | +0.06 | 1.08 |

Chronological: TRAIN `E[R] −0.13` → VALIDATION `+0.07` → OOS `−0.03` (OOS = **1 session**, 2026-09-04). Target 1:2 `E[R] −0.07` · 1:3 `−0.03` · 1:4 `−0.05`.

### Kaggle NIFTY 5m range_only proxy (2,652 sessions — structure only, no volume/L2/OI)

| cut | signals | trig | tgt% | SL% | TO% | E[R] @1:3 | PF |
|---|--:|--:|--:|--:|--:|--:|--:|
| range_only ≥200% | 9995 | 7492 | **9.2** | 35.9 | **54.9** | +0.21 | 1.51 |
| range_only ≥500% | 271 | 198 | 4.5 | 20.2 | 75.3 | +0.35 | 2.27 |

Chronological: TRAIN `+0.24` → VALIDATION `+0.20` → OOS (663 sessions, 2023-06…2026-05) `+0.13` → latest-OOS-quarter `+0.10` — **monotonic decay toward zero**. The positive E[R] is dominated by **timeouts** (55–75% of trades) marked at their 25-min close; the **1:3 target is hit <10% of the time**.

## Answers (spec §12)

| | question | answer (all PROXY) |
|---|---|---|
| **A** | Does 200%+ imbalance improve N+1 behaviour? | **No.** Futures passive-book proxy is break-even-to-negative (`E[R] −0.03`, PF 0.96, SL 68%). Range proxy shows +E[R] but the 1:3 target hits <10% and it decays OOS. Genuine L2: untestable → `NOT VALIDATED`. |
| **B** | Most stable threshold 200/300/400/500? | **None.** Futures sweep is flat (−0.03…−0.01); range sweep rises only as the sample collapses (9995→271). No knee at 200%. |
| **C** | Does 1M confirmation add value? | No. 1M-only `E[R] −0.03`; adding 1M to a 5M signal (`D`) is `−0.09`. |
| **D** | Does 5M confirmation add value? | No. 5M-only `E[R] −0.04` (40% timeout). |
| **E** | Does 30M confirmation add value? | No. 30M-only resolves almost nothing in 25 min (92% timeout, n=12). |
| **F** | Does multi-TF agreement materially improve OOS? | **No.** Configs D–G are tiny-sample (n≤127 over ≤3 days) and mixed-sign. G (`1M+5M+30M`) shows `+0.44` on **n=13** — flagged `OVERFIT/UNSTABLE`, not selected. |
| **G** | Does sideways→abnormal-spike detection improve results? | **No.** Futures `−0.32` (worse than normal `−0.02`); range `+0.26` vs `+0.21` (negligible). Contradictory across datasets → not stable. Threshold sweep for "sideways" (`pre_compression`) and "spike" (`range_x`, `vol_x`) shows no stable band. |
| **H** | Does expiry-day index OI/ΔOI add incremental value? | **Cannot be evaluated.** No expiry day fell in the futures/L2 window; the one genuine expiry-day-with-per-strike-OI case (SENSEX 2026-09-03) has no futures / L2 / volume data, so no imbalance event could be formed on it. |
| **I** | Does PCR add incremental value? | **No — `NON-CONTRIBUTORY`.** Every PCR cut is negative on the futures proxy (`PCR≥1.0 −0.11`, `PCR<1.0 −0.09`, `PCR rising −0.55` on n=34). |
| **J** | Does S/R / pivot proximity add value? | **No.** Futures "near" `−0.04` vs "away" `+0.06`; range "near" `+0.21` vs "away" `+0.22`. Contradictory / within-noise. |
| **K** | Most stable 1:3 expectancy combination? | **None qualifies.** Every positive cell fails at least one of: sufficient sample, ≥2 regimes, chronological stability, non-degenerate OOS. All marked `OVERFIT/UNSTABLE` or `UNTESTABLE` per §10. |
| **L** | Exact OOS sample size? | Genuine-L2 OOS = **0**. Futures passive-book proxy OOS = **1 session** (2026-09-04), ~888 resolved trades — degenerate. Range-only proxy OOS = 663 sessions / 1,605 resolved (no L2). |
| **M** | Target hits / SLs / timeouts within 25 min? | passive_book ≥200% @1:3: **370 target / 1301 SL / 233 timeout** (1904 triggered; +231 `NO_TRIGGER`). range_only ≥200% @1:3: **691 / 2691 / 4110** (7492 triggered; +2385 `NO_TRIGGER`). Per-cell counts in the CSV. |
| **N** | Genuinely validated or proxy? | **PROXY.** `NOT VALIDATED — GENUINE L2 REQUIRED`. |

## Option-premium (spec §7, research only, target formula unchanged)

Only 1 NIFTY event fell inside the Upstox expired-option 5m coverage window
(ends 2026-09-01) — insufficient to say anything. Recorded fields
(`prem_CE_mfe/mae`, `prem_PE_mfe/mae`, strike, distance-from-ATM) are in the CSV
for when more overlap exists.

## Cross-vendor replication — Upstox NIFTY historical (2026-09-07)

`scripts/imbalance_nc_1r3_upstox_nifty.py` runs the **unchanged** engine on the
Upstox NIFTY bars (cash index — no volume / bid-ask / depth / aggressor / OI, so
only the `range_only` proxy applies). Verdict is unchanged:
`NOT VALIDATED — GENUINE L2 REQUIRED`. Purpose: does the `range_only` N+1
behaviour replicate on a second vendor/period?

| dataset | events | sessions | ≥200% trig | tgt% | SL% | TO% | E[R]@1:3 | PF |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| **Upstox NIFTY 5m** (2022-01…2026-09) | 3,417 | 1,077 | 2,563 | **7.8** | 35.3 | **56.9** | **+0.16** | 1.40 |
| Kaggle NIFTY 5m (2015…2026, from above) | 9,995 | 2,652 | 7,492 | ~9 | 35.9 | ~55 | +0.21 | 1.51 |
| Upstox NIFTY 1m (2026-08-07…09-04) | 302 | 21 | 243 | 16.9 | 57.2 | 25.9 | +0.11 | 1.18 |

**The two vendors AGREE** on the 5m `range_only` proxy: same timeout-dominated
signature (~57% of trades never resolve in 25 min), 1:3 target hit **<10%**,
small positive `E[R] ≈ +0.16–0.21`, `PF ≈ 1.4–1.5`. Threshold sweep on Upstox
(≥200 +0.16 · ≥300 +0.23 · ≥400 +0.23 · ≥500 +0.13) is again **not monotonic /
200% not special**. `near S/R` vs `away` (+0.17 vs +0.15) and `sideways→spike`
(+0.19, n=32) add nothing — consistent with the Kaggle result.

Chronological on Upstox 5m: TRAIN +0.15 · VAL +0.14 · OOS +0.16 · HOLDOUT +0.23
· latest-20%-of-OOS+HOLDOUT +0.41. Unlike Kaggle it does **not** decay, but the
recent lift is a single 2026 slice (n=155/76 sessions) — regime, not proven
stability. The Upstox **1m** set (21 sessions) is too short: split runs
TRAIN −0.05 → VAL −0.16 → OOS +0.18 → HOLDOUT +0.50 (n≤72), pure noise; `TF D
1M+5M agree` shows +0.49 on n=24 → `OVERFIT/UNSTABLE`, not selected.

**Conclusion:** the `range_only` N+1 pattern is a *consistent* (replicated
across vendor + period) but **weak, proxy-only, timeout-dominated,
<10%-target-hit** statistical property of abnormal-range candles — **not an
order-flow / L2 edge** and not tradeable as specified. No strategy change, no
production wiring, nothing `PROVEN`.

## OCO variant — arm BOTH N+1 marks (2026-09-07)

`scripts/imbalance_nc_oco_breakout.py` (engine primitives imported unchanged).
Requested variant: N = abnormal-spike **and** imbalance candle; after N+1 closes,
mark its high **and** low and arm **both** stop orders — break N+1 high → BUY
(SL = N+1 low), break N+1 low → SELL (SL = N+1 high), first level wins,
`R = N+1 range`. Same 1:2/1:3/1:4 + 25-min timeout. Differs from the base module,
which only trades in N+1's own colour direction. Long share ≈ 50% → genuine
both-sides bracket. Verdict unchanged: `NOT VALIDATED — GENUINE L2 REQUIRED`
(imbalance is a proxy).

| cut (≥200%, 1:3) | trig | tgt% | SL% | TO% | **E[R]** | PF | vs directional E[R] |
|---|--:|--:|--:|--:|--:|--:|--:|
| futures `spike_AND_book` | 457 | 23.2 | 63.7 | 13.1 | **+0.15** | 1.24 | −0.03 |
| futures `book_only` | 2123 | 25.5 | 61.9 | 12.5 | +0.23 | 1.36 | (n/a) |
| futures `vol_range` | 814 | 24.4 | 60.9 | 14.6 | +0.22 | 1.36 | −0.03 |
| Kaggle NIFTY 5m `range_only` | 9733 | 10.9 | 33.3 | 55.9 | +0.30 | 1.78 | +0.21 |
| Upstox NIFTY 5m `range_only` | 3300 | 9.1 | 32.2 | 58.7 | +0.25 | 1.66 | +0.16 |

**Arming both sides beats the colour-directional filter everywhere** (futures
proxy −0.03 → +0.15…+0.23; index proxy +0.16/+0.21 → +0.25/+0.30) — because it
stops forcing the trade into N+1's colour when the market breaks the other way.
**But this is a backtest-mechanics gain, not a validated edge:**

- Still no aggressor data → the imbalance is a proxy → `NOT VALIDATED`.
- Futures = 3–4 sessions, one regime → the +0.15…+0.22 is within-noise.
- `spike_AND_book` is *worse* than `book_only` (+0.15 vs +0.23) — requiring the
  abnormal spike on top of the imbalance only cuts sample, adds nothing.
- Index `range_only` still hits the 1:3 target only ~9–11 % of the time with
  ~56–59 % timeouts; the +E[R] is timeout-drift + a moderate SL:target ratio.
  Kaggle decays monotonically OOS (TRAIN +0.34 → HOLDOUT +0.21 → latest +0.18);
  Upstox is noisy (TRAIN +0.28 → VAL +0.17 → HOLDOUT +0.28).
- `tf 1M` OCO resolves in ~1 bar and looks best (E[R] +0.25, TO 8 %); `tf 5M`
  is flat (E[R] −0.01, TO 44 %).
- `near S/R` vs `away` and `sideways→spike` vs `normal`: identical → no value.
- 1:2 target is more reachable (tgt ~35 % futures / ~22 % index) but lower E[R].

No config is selected (spec §10). No strategy change, no production wiring,
frozen H1/H7 + live logic untouched. Nothing `PROVEN`.

## NIFTY deep-dive — TOD / regime / gap / DOW conditioning (2026-09-07)

`scripts/nifty_spike_n1_deepdive.py` (engine imported unchanged). NIFTY 5m
abnormal-spike → OCO N+1 breakout, conditioned. Upstox NIFTY 5m (1,077 sess) +
Kaggle NIFTY 5m (2,652 sess), chronological 45/20/17/18 split.

**Correction to the OCO table above:** the OCO `range_only` figures there
(+0.30 Kaggle / +0.25 Upstox) were **inflated by pooling in 30M-timeframe
events** (`imbalance_nc_oco_breakout.py` resamples to 5M *and* 30M). On **pure
NIFTY 5m** the honest number is `E[R] +0.05 (Upstox) / +0.075 (Kaggle)` at 1:3,
**1:3 target hit only ~6–7 %**, timeout **>52 %**. Barely above zero.

**Conditioner stability scan** — cuts with `E[R] > 0` on ≥3 of 4 chronological
splits, spread ≤ 0.35R:

| cut | Upstox splits (T/V/O/H) | Kaggle splits (T/V/O/H) |
|---|---|---|
| all events | +0.05 / +0.02 / +0.02 / +0.10 | +0.10 / +0.06 / +0.05 / +0.03 |
| **`*/WIDE`** (spike on a wide-range / high-vol day) | **+0.18 / +0.06 / +0.07 / +0.27** | (via CHOP/WIDE +0.15, TREND_DN/WIDE +0.16, TREND_UP/WIDE +0.11) |
| `CHOP/*` | +0.09 / +0.00 / +0.10 / +0.07 | +0.08 / +0.03 / +0.06 / +0.07 |
| `range_x ≥ 3` | +0.09 / +0.00 / +0.17 / +0.26 | +0.03 / +0.12 / +0.07 / +0.09 |
| `GAP_UP` | (not flagged) | +0.12 / +0.08 / +0.07 / +0.06 |

**The one condition that shows up in BOTH datasets across splits: abnormal
spikes on already-wide-range (high-volatility) days break out better**
(`*/WIDE` `E[R] +0.11…+0.27` vs `*/TIGHT` ≈ 0 or negative). `CHOP` days slightly
beat trend days. `Thu/Fri` weaker than `Tue/Wed` (mild). `near-pivot`,
`gap direction`, `time-of-day` — no consistent effect.

**Next-25-min excursion (Upstox, 3,357 triggered OCO trades, in R):**
MFE_R p50 0.76 / p75 1.46 / p90 2.39 / p95 3.13 / max 12.18; MAE_R p50 −0.84 /
p10 −1.57 / min −6.14. **P(MFE≥1R)=0.40, P(≥2R)=0.15, P(≥3R)=0.06, P(≥5R)=0.004.**
→ 40 % of trades reach +1R sometime in 25 min but only 6 % reach +3R — the
1:3-in-25-min combo is structurally hard; a tighter target or a trail would
capture more of the +1R mass (1:2 already tested, lower E[R] via lower R).

**Verdict:** still a **range-shape proxy on the cash index** — no volume, L2,
aggressor or OI. `*/WIDE` and `range_x ≥ 3` are the only "candidate for further
study" cuts; everything else is within-noise or decays OOS. Not an order-flow
edge, not a production signal. Nothing `PROVEN`.

## High-Conviction Runner (HCR) — few signals, scale-out, +3R runner (2026-09-07)

`scripts/high_conviction_runner_research.py` + `app/research_strategy/hcr.py`
(read-only) + a **Research → High-Conviction Runner** panel in the frontend.

Built from the one conditioner that replicated across both NIFTY datasets and
all splits (spike on a WIDE-range day). Entry rule:

    day range ≥ 1.3× rolling-median  AND  spike range_x ≥ 3  AND  spike < 14:00
    AND day-of-week not Thu/Fri
    → N+1 close → OCO (break high = LONG / break low = SHORT), R = N+1 range
    → Leg A 60% @ +1R (locker); on fill, Leg B stop → breakeven
    → Leg B 40% @ +3R (runner); 25-min hard lifetime; blended R = 0.6·A + 0.4·B
    "close green" = blended R > 0

| dataset | signals | span | /mo | close-green% | A@+1R | B@+3R | blended E[R] | PF | max consec loss |
|---|--:|---|--:|--:|--:|--:|--:|--:|--:|
| Upstox NIFTY 5m | 82 | 2022-02 … 2026-08 | ~1.4 | **65.0 %** | 46 % | 17.5 % | **+0.40** | 2.45 | 6 |
| Kaggle NIFTY 5m | 294 | 2015-02 … 2026-03 | ~2.0 | **62.5 %** | 48 % | 13.9 % | **+0.34** | 2.18 | 6 |

Chronological (Kaggle, larger sample): TRAIN +0.40 · VAL +0.21 · OOS +0.34 ·
HOLDOUT +0.36 — **no OOS decay** (first thing this session that holds up).
Cross-vendor agreement is close. blended-R dist: p50 +0.60, p95 +1.80,
p05 −1.00 (worst trade −1R; no catastrophic tail — hard SL + BE-protected
runner).

**Honest verdict:** "few signals + big runner" ✅; "80–100 % win rate" ❌ — the
real close-green rate is **~63 %**, not 80–100 % (that combination does not
exist with a genuine +3R runner). It is still a **RANGE-SHAPE proxy on the
NIFTY cash index** — no volume / L2 / aggressor / OI, and the cash index isn't
directly tradable (future/option spread + theta erode it, cf. Stage-4). It is
the best candidate this session produced (replicates, no decay, PF > 2) but it
is **NOT VALIDATED** and is surfaced in the UI as `RESEARCH`, paper-watch only.
No production wiring, no live signal, nothing `PROVEN`.

Frontend: additive only — `app/research_strategy/` (guarded router
`/api/research/hcr/{summary,signals}`, read-only), one panel in `#view-research`,
`loadHcr()` in `app.js`. No trading / frozen / calibration / broker / cron
change; `live_trading` stays false; 635 tests pass.

## Not done / blocked

- No production wiring, no live signal, no change to frozen H1/H7 or trading logic.
- The whole matrix is under-powered: ≤4 futures sessions, one regime, snapshot cadence, zero aggressor data.
- **Re-run this module once the Angel mode-3 SnapQuote capture (`L2_SNAPQUOTE_CAPTURE.md`) — or a real aggressor feed — has accumulated ≥ 40 sessions across ≥ 2 regimes.** The event/metric code needs no change; only the proxy would be replaced by (or complemented with) a genuine aggressor-pressure measure, and the chronological split would become real.
