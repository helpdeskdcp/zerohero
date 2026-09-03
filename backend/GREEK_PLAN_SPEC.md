# Outcome-Calibrated Trade Plan (Greeks + OI + PAPER history) — Spec (design only)

**Date:** 2026-09-03 · **Status:** DESIGN — awaiting approval · **Author:** Chanakya
**Scope:** replace the fixed-multiple entry / SL / target / trail logic in AutoScalp
with a plan that is **calibrated from actual PAPER-trade outcomes** and shaped by
**Greeks + OI as context**. Greeks still never create a BUY CE / BUY PE — direction
comes only from the existing `state_classifier`.

Changes **no code**. `live_trading` stays `false`, `paper_mode` stays `true`. No
order path, no broker order method. Companion docs: [[greek-confirm-spec]]
(`GREEK_CONFIRM_SPEC.md` — the confidence layer), [[greeks-profiles-spec]]
(`GREEKS_PROFILES_SPEC.md` — NSE/MCX Greek sourcing).

---

## 0. What the plan logic does today (verified by inspection)

**`app/engines/scalp_strategy.py:89 _plan_from_leg(sel, direction, cfg)`** — the
entire plan:

```
entry  = sel.ltp                                   # current premium at decision, immediate
atr    = sel.sr.atr  or  max(1.0, entry*0.02)      # option-leg ATR
sl     = entry - cfg.sl_atr(1.1) * atr             # + clamped up to leg support - 0.15 ATR
t1     = entry + cfg.t1_atr(1.7) * atr             # + capped at leg resistance
t2     = entry + cfg.t2_atr(2.6) * atr
trailing_stop = cfg.trail_atr(0.9) * atr           # a DISTANCE, not a trigger
max_hold_sec  = cfg.max_hold_sec(1500)
```

**`app/engines/option_engine.py:194 ev_gate(prob, entry, sl, t1, avg_win=None, avg_loss=None, …)`**
— `rr = (t1-entry)/(entry-sl)`; `ev = p·avg_win − (1−p)·avg_loss − cost`; pass if
`ev_r ≥ 0.12` and `rr ≥ 1.3`. **The live runner calls it with `avg_win=None,
avg_loss=None` (`runner.py:573`)**, so `avg_win`/`avg_loss` fall back to
`reward`/`risk` — i.e. **EV currently uses no historical outcome data at all.**

**`app/engines/paper_trading.py:93 update_trade_price()`** — exit engine, in order:
hard `TIME` stop at `max_hold_sec` → `TARGET`(T1) / `STOP`(SL) → profit-lock ladder
(`MFE ≥ 0.6R` → SL to entry+0.2R; `MFE ≥ 1.0R` → entry+0.5R) → 1R breakeven →
trailing ratchet once `MFE ≥ trailing_stop`. Ladder constants are **hard-coded**.
Only T1/T2 exist; **no T3**; `TRAIL` exit only fires after the stop has moved
favourably.

**`app/engines/option_engine.py:68 _translation()`** already computes the premium-
move primitive we need:
`expected_premium_move = |Δ|·|index_move| + 0.5·Γ·index_move²` — but only as a
`0..1` quality sub-score, never as the basis for SL/targets.

**Calibration that already exists:** `app/backtest/calibration.py` fits a logistic
`score → P(win)` per `regime|signal_type` (+ global), refit every 900 s from
`scalp_signals` CLOSED rows, min 40 samples (`runner._maybe_recalibrate`). It
calibrates **probability only** — nothing about distances or timing.

**Fixed values in force now (the ones this spec removes):** `sl_atr 1.1`,
`t1_atr 1.7`, `t2_atr 2.6`, `trail_atr 0.9`, `max_hold_sec 1500` (NIFTF profile may
override), profit-lock `0.6R/1.0R → +0.2R/+0.5R`, `ev_gate min_ev_r 0.12`,
`rr_min 1.3`, `_translation min_premium_move 8.0`.

---

## 1. Data reality — what history we can calibrate from (verified)

`ai_paper_trades`: **43 closed rows** (NIFTY ~8, NATURALGAS ~30, CRUDEOIL ~3,
BANKNIFTY ~2).

| field | populated | usable |
|---|---|---|
| `entry`, `exit_price`, `pnl`, `result` | 43/43 | ✅ |
| `mfe`, `mae` (premium points, direction-aware, ratcheted) | 43/43 | ✅ core |
| `exit_reason` (`STOP`/`TARGET`/`TRAIL`/`TIME`/`BROKER_REALISED`/`TIME_NODATA`) | 43/43 | ✅ |
| `target_1`, `target_2`, `stop_loss`, `trailing_stop` | 43/43 | ✅ (the plan that was used) |
| `max_hold_sec` | 40/43 | ✅ |
| `probability`, `confidence`, `market_regime`, `setup` | 40/43 | ✅ |
| holding time | derivable from `opened_ts`/`closed_ts` | ✅ |
| `atr_pct` | **2/43** — hard-coded `None` at `runner.py:739` | ❌ must start logging |
| `oi_evidence` | **0/43** — hard-coded `""` at `runner.py:737` | ❌ must start logging |
| entry Greeks (Δ/Γ/Θ/V), entry IV, underlying LTP at entry, ΔOI | **not stored anywhere per-trade** | ❌ new column set |
| time-to-T1 / time-to-SL (separate from total hold) | not stored | ❌ derive at close |

**Conclusion:** MFE/MAE/exit_reason/holding-time are enough to calibrate distance
and timing **pooled across symbols** *today*, but per-regime / per-Greek-bucket
calibration needs a richer per-trade record first (§2). Sample size is far below
`_MIN_ROWS=40` for NIFTY alone — every calibrated number needs a **prior +
shrinkage** fallback (§7).

---

## 2. New substrate: the Trade Outcome Record (must log before calibrating)

Add to `ai_paper_trades` (or a sidecar `trade_features` table keyed by `trade_id`)
— all captured **at entry**, none look-ahead:

```
underlying_ltp_entry        spot at entry
atm_strike_entry            round(spot/step)*step
selected_strike_offset      (strike - spot) in points and in ATR
entry_delta, entry_gamma, entry_theta, entry_vega        broker (NSE) / BS-gamma-only (MCX)
entry_iv                    oi_weighted / leg IV
entry_oi, entry_oi_change   selected leg
net_delta_exp_sign, gamma_pin_offset_atr, pcr_oi         from GreeksEngine snapshot
atr_pts, atr_pct, leg_atr_pts        index + option-leg ATR at entry
vol_regime                  LOW | NORMAL | HIGH  (atr_pct bucketed)
expected_underlying_move_fav, _adv    from the setup (S/R distance, state target)
expected_premium_move_fav, _adv       Greek projection (§4)
regime, signal_type, tod_bucket, mtf_alignment           (already in scalp_signals)
```

Add **at close** (derive in `_finalize_close`, `runner.py:816`):

```
time_to_mfe_peak_sec, time_to_exit_sec
mae_before_mfe          did it dip hard before running? (SL-survivability signal)
realised_underlying_move, realised_premium_move
iv_change_entry_to_exit
r_multiple = points / risk_ref
```

This record is what every calibrator in §3 consumes. **Phase 0 of rollout = log
this for N sessions, change nothing else.**

---

## 3. Component design (items 1–10)

Each component: **inputs available now** · **new logic** · **calibration source**.

### (1) Which CE/PE strike

`select_option` (`option_engine.py:174`) today ranks by `quality_score` + ATM
proximity. **Add a risk-adjusted expected-move term:**

```
for each candidate strike k on the wanted side:
    em_fav(k) = expected_premium_move_fav  (§4, uses that leg's Δ,Γ)
    em_adv(k) = expected_premium_move_adv
    liq_haircut(k) = f(oi, oi_change, spread)          # already have oi; add spread from depth
    edge(k) = (prob_T1(k) * em_fav(k) - (1-prob_T1(k)) * em_adv(k) - cost(k)) / em_adv(k)
    score(k) = 0.55*quality_score(k)/100 + 0.30*edge(k) + 0.15*atm_prox(k)
pick argmax score(k), subject to: 0.35 ≤ |Δ| ≤ 0.62 (already in delta_fit),
                                  premium in [premium_min, premium_max],
                                  liq_haircut ≤ cap
```

- **Now:** `analyse_leg` per-strike Δ/Γ/θ/IV/OI, `quality_score`, `delta_fit`,
  `premium_fit`, candidate loop (`scalp_strategy.py:236`).
- **New:** `edge(k)` using §4 + `prob_T1` from the calibrated bucket table (§7);
  option-leg bid/ask spread from the depth already in the chain payload.
- **Calibration:** per `|Δ|`-bucket × `vol_regime`, historical `r_multiple` and
  win-rate → which delta band actually paid. Prior: prefer 0.45–0.55.

### (2) When to enter — immediate vs entry zone

Today: always immediate at `sel.ltp`. **Add an entry-mode decision:**

```
if signal_type in {BREAKOUT, MOMENTUM_CONTINUATION} and mtf strongly aligned:
    entry_mode = MARKET          # chasing is correct here
elif signal_type in {PULLBACK, RANGE, SUPPORT_REVERSAL}:
    entry_mode = ZONE            # wait for the retrace
else:
    entry_mode = ZONE_OR_TIMEOUT # zone, but take market after entry_wait_sec
entry_zone = [ ltp - z_lo * leg_atr , ltp + z_hi * leg_atr ]     # z_lo,z_hi calibrated
```

- **Now:** `signal_type`, `mtf_alignment`, leg ATR, live LTP each tick (`_monitor`
  loop already runs; a pending-entry watcher reuses it).
- **New:** a `PENDING` trade state — `open_trade` deferred until LTP enters the
  zone or `entry_wait_sec` elapses (then market) or the setup invalidates (cancel,
  logged as `NO_FILL`).
- **Calibration:** for ZONE fills historically, did price actually revisit
  `ltp − z·leg_atr` within `entry_wait_sec`? Fit `z_lo` to the observed retrace
  depth distribution (median for the setup); `entry_wait_sec` to its p75 time.
  Prior: `z_lo=0.4, z_hi=0.15, entry_wait_sec=180`, `fill_rate` floor 0.5 or the
  mode falls back to MARKET.

### (3) Ideal option premium entry price

```
ideal_entry = clamp(  ltp - z_lo*leg_atr ,   # bottom of zone (best)
                      structural_floor ,      # leg support - 0.1 ATR (don't wait through it)
                      ltp )                   # never above current
fill_assumption = ideal_entry + slippage_ticks    # for EV; slippage calibrated from
                                                  # entry vs first-mark drift in history
```

- **Now:** `ltp`, leg `sr.support`, leg ATR.
- **New:** `slippage_ticks` estimate (mean |first monitor mark − entry| from
  history, per liquidity bucket).
- **Calibration:** realised fill vs `ideal_entry` → bias correction.

### (4) Expected option premium move (before entry)

The core estimator. Two-sided, over the **expected time-to-outcome** (not per day):

```
move_fav = expected_underlying_move_fav    # S/R distance or state target, favourable
move_adv = expected_underlying_move_adv    # distance to setup invalidation

# delta-gamma translation (both directions)
dfav = |Δ|*move_fav + 0.5*Γ*move_fav**2
dadv = |Δ|*move_adv + 0.5*Γ*move_adv**2

# theta drag over the expected horizon
theta_per_sec = Θ_per_day / SESSION_SECONDS
drag_fav = theta_per_sec * E[time_to_T1_sec | bucket]
drag_adv = theta_per_sec * E[time_to_SL_sec  | bucket]

# vega / IV path (regime-conditional, conservative)
iv_fav = Vega * E[ΔIV | winning move, bucket]      # often ~0..+0.4 vol pts
iv_adv = Vega * E[ΔIV | losing move,  bucket]      # often negative (IV bleed)

expected_premium_move_fav = max(0, dfav + drag_fav + iv_fav) * translation_realism
expected_premium_move_adv =        dadv - drag_adv - iv_adv                     # signed loss
```

- **Now:** `|Δ|*move + 0.5*Γ*move²` **already coded** in `_translation`; Θ, Vega,
  IV per leg from `analyse_leg`; `move_fav`/`move_adv` from `compute_sr` S/R levels
  + `state` target.
- **New:** `E[time_to_T1]`, `E[time_to_SL]`, `E[ΔIV|win]`, `E[ΔIV|loss]`,
  `translation_realism` (realised premium move ÷ Greek-projected, per bucket —
  captures that theoretical Δ/Γ over-predicts on illiquid strikes).
- **Calibration:** `translation_realism` and the `E[·]` terms are simple grouped
  means over the outcome record, with shrinkage to a pooled prior.

### (5) Calibrated initial SL

```
sl_dist = max(
    structural_invalidation_premium,           # premium at (spot moved to invalidation) via Δ,Γ  = dadv
    k_sl * sigma_MAE_winners(bucket)            # so most eventual winners survive it
)
sl_dist = clamp(sl_dist, sl_floor_R * 1R_ref, sl_cap_R * 1R_ref)
stop_loss = entry - sl_dist
risk_ref  = sl_dist                              # drives the profit-lock ladder
```

`sigma_MAE_winners` = the MAE distribution **of trades that ultimately won**,
per `regime|signal_type|vol_regime`. `k_sl` chosen so ≤ `stop_survive_target`
(e.g. 20 %) of historical winners would have been stopped.

- **Now:** `mae` on all 43 rows; `result`; `regime`/`setup`; `dadv` computable.
- **New:** the grouped MAE quantile table + `k_sl` solver.
- **Prior (small n):** `sl_dist = 1.0 * dadv` clamped to `[0.7, 1.3] × (1.1·leg_atr)`
  (the current fixed value is the centre of the clamp, so we degrade gracefully to
  today's behaviour).

### (6) Calibrated T1 / T2 / T3

```
mfe_R = MFE / risk_ref   over winners, per bucket
T1_R = quantile(mfe_R, q1)      # q1 ~ 0.35  -> reliably reached
T2_R = quantile(mfe_R, q2)      # q2 ~ 0.60
T3_R = quantile(mfe_R, q3)      # q3 ~ 0.80  -> runner; only if regime==TRENDING and |mtf|>=thr
T1 = entry + T1_R * risk_ref, capped at min(expected_premium_move_fav, leg_resistance)
T2 = entry + T2_R * risk_ref, capped at leg_resistance_2 / expected_premium_move_fav*1.1
T3 = entry + T3_R * risk_ref   (disabled -> None when not trending)
```

Partial-exit policy (new): 50 % at T1, 30 % at T2, 20 % runner to T3/trail.
(Paper engine currently exits 100 % at T1 — needs a scale-out branch.)

- **Now:** `mfe`, `target_1/2` history, `leg_resistance` from `analyse_leg.sr`.
- **New:** MFE/R quantile table; scale-out in `update_trade_price`.
- **Prior:** `T1_R=1.5, T2_R=2.4, T3_R=3.4` (≈ today's `1.7/2.6` ATR mapped to R).

### (7) Exact point trailing starts

Today trailing implicitly begins when `MFE ≥ trailing_stop` (a distance). Make it
an explicit **trigger**:

```
trail_start_R = calibrated from: among winners, the MFE/R level BEYOND WHICH
                price rarely returned to entry  (i.e. the "safe" ratchet point)
trail_start_price = entry + trail_start_R * risk_ref
# before trail_start: static SL from (5) + the existing 0.6R/1.0R profit-lock
# at/after trail_start: dynamic ratchet from (8)
```

- **Now:** `mfe`, `mae`, exit_reason, tick history via monitor.
- **New:** "give-back" analysis — for each winner, max retrace after each MFE
  level; pick the level where retrace-to-entry probability < 10 %.
- **Prior:** `trail_start_R = 1.0` (matches today's 1R breakeven).

### (8) Dynamic trailing distance

```
trail_dist = max(
    k_trail * leg_atr_1m,                 # follows current premium volatility
    min_trail_R * risk_ref )              # never tighter than this
k_trail, min_trail_R calibrated so trailed exits historically kept
   >= keep_fraction of MFE  (e.g. 0.6) without premature TRAIL stop-outs.
adapt: widen by *vega_factor when IV rising fast (avoid noise stop), tighten
       near expiry / high theta.
```

- **Now:** option-leg ATR (need 1m leg bars — `_leg_bars_fn` already provides leg
  bars), `trailing_stop` history, `mfe` vs realised exit.
- **New:** 1m leg-ATR feed into the monitor; `keep_fraction` optimiser over
  history.
- **Prior:** `k_trail=0.9, min_trail_R=0.5` (≈ today's `0.9·ATR`).

### (9) Time-based exit / max-hold

```
max_hold_sec = quantile( time_to_exit_sec | winners, bucket, q≈0.8 )
             clamped to [min_hold, session_time_left - buffer]
# plus: soft time-decay exit — if elapsed > E[time_to_T1] and MFE < 0.3R
#       and theta_drag high -> exit EARLY (don't feed the full TIME stop)
```

- **Now:** holding time derivable; `exit_reason='TIME'` rows show where the fixed
  1500 s hurt (NIFTY task #5 evidence).
- **New:** per-bucket time-to-outcome quantiles; the soft-decay branch in the
  monitor.
- **Prior:** keep 1500 s (NIFTY) / current MCX profile values until ≥ 20 closed
  per bucket (task #5 gate).

### (10) NO-TRADE when reward too small / risk too high

Extend `ev_gate` — and actually feed it history:

```
avg_win  = E[ r_multiple | win,  bucket ]      # was None in live -> real number now
avg_loss = E[ |r_multiple| | loss, bucket ]
prob_T1  = calibrated bucket win-rate (score-conditional, §7)
prob_SL  = calibrated bucket stop-rate
prob_time= 1 - prob_T1 - prob_SL
EV_R = prob_T1*avg_win - prob_SL*avg_loss + prob_time*E[r|time] - cost_R
REJECT (NO_TRADE) if:
   EV_R < min_ev_r                         (keep 0.12 default, calibratable)
   or RR(T1) < rr_min                      (1.3)
   or expected_premium_move_fav < min_abs_move  (liquidity/– cost can't be covered)
   or sl_dist / entry > max_risk_frac      (premium too rich for the stop)
   or fill_rate(zone) < min_fill and entry_mode==ZONE and no MARKET fallback
```

- **Now:** `ev_gate` skeleton, `rr`, `cost` hook (`est_cost_r`).
- **New:** the bucket expectation tables; wire `avg_win/avg_loss` from them
  instead of `None`.

---

## 4. Complete intended pipeline

```
MARKET DATA        WS bars + option chain (OI, depth) + broker Greeks (NSE) /
      │            BS-gamma (MCX) + FUT/INDEX price
      ▼
DIRECTION          compute_sr -> state_classifier -> BULLISH | BEARISH | NONE
      │            (unchanged; NONE => NO_TRADE, Greeks never set this)
      ▼
OI                 PCR, max_pain, OI walls, ΔOI, gamma pin (GEX v1a per-strike)
      │            -> S/R strength + expected_underlying_move_fav / _adv
      ▼
GREEKS (context)   greek_confirmation() [GREEK_CONFIRM_SPEC]: agree/conflict,
      │            score_mult, conf_cap, optional veto.  ALSO exposes per-leg
      │            Δ Γ Θ V IV + net_delta_exp sign + pin offset for the plan.
      ▼
STRIKE SELECTION   select_option + risk-adjusted edge(k) (§3.1):
      │            argmax [ quality, expected-move edge, ATM proximity ],
      │            gated by |Δ| band, premium band, liquidity haircut
      ▼
EXPECTED MOVE      expected_premium_move_fav / _adv  (§3.4):
      │            |Δ|·move + ½Γ·move²  ± theta drag over E[t]  ± vega·E[ΔIV]
      │            × translation_realism(bucket)
      ▼
ENTRY ZONE         entry_mode = MARKET | ZONE | ZONE_OR_TIMEOUT  (§3.2)
      │            ideal_entry, entry_zone = ltp ± z·leg_atr  (§3.3)
      │            PENDING state until fill / timeout / invalidation
      ▼
CALIBRATED SL      sl_dist = max( structural_invalidation , k_sl·σ_MAE_winners )
      │            clamp [sl_floor_R, sl_cap_R] ; risk_ref = sl_dist   (§3.5)
      ▼
TARGETS            T1/T2/T3 = entry + quantile(MFE/R, q1/q2/q3)·risk_ref,
      │            capped at expected_premium_move_fav / leg resistance ;
      │            T3 only if TRENDING + |mtf|≥thr ; scale-out 50/30/20   (§3.6)
      ▼
TRAILING           trail_start = entry + trail_start_R·risk_ref  (§3.7)
      │            trail_dist  = max( k_trail·leg_atr_1m , min_trail_R·risk_ref )
      │            IV-rising -> widen ; near-expiry/high-theta -> tighten   (§3.8)
      │            + soft time-decay early-exit  (§3.9)
      ▼
EV / RR GATE       avg_win/avg_loss/prob_T1/prob_SL from calibrated bucket tables ;
      │            EV_R, RR, min_abs_move, max_risk_frac, fill_rate  (§3.10)
      │            fail any -> NO_TRADE
      ▼
FINAL SIGNAL       BUY_CE | BUY_PE | WATCH | NO_TRADE  + full plan + reason + EV math
```

Greeks enter at **GREEKS (context)** and feed **STRIKE SELECTION** / **EXPECTED
MOVE** as *quantities*, and gate at **EV/RR**. No stage lets Greeks pick a
direction or emit a decision (see §8).

---

## 5. Numerical example — one hypothetical NIFTY CE (illustrative; every calibrated constant is a placeholder pending the §7 fit)

**Context:** NIFTY TRENDING_UP, `signal_type = MOMENTUM_CONTINUATION`, MTF
alignment +34, `vol_regime = NORMAL`, calibrated score → `P(win) = 0.61`.

| quantity | value | source |
|---|---|---|
| spot (index_ltp) | 24,150 | aggregator |
| ATM / step | 24,150 / 50 | `_sym_meta` |
| selected strike | **24,150 CE** (`|Δ|` 0.52, ATM) | `select_option` + edge(k) |
| current premium (leg ltp) | **₹96.0** | chain |
| Δ / Γ / Θ / Vega / IV | **0.52 / 0.0028 /pt / −6.5 /day / 4.8 per 1 vol-pt / 12.5 %** | broker optionGreek |
| leg OI / ΔOI | **2.85 M / +180 k** | chain |
| net_delta_exp sign / gamma pin | **+ / 24,200 (above spot ⇒ CE-supportive)** | GreeksEngine |
| index ATR / leg ATR (5m) / leg ATR (1m) | 22 pts / 14.0 / 6.0 | `compute_sr` / leg bars |
| expected underlying move — fav / adv | **+45 pts** (to next resistance) / **−18 pts** (to breakout invalidation) | S/R + state target |
| expected **premium** move — fav | `0.52·45 + 0.5·0.0028·45²` = 23.4 + 2.8 = 26.2 ; −θ drag (E[t_T1]≈12 min) 0.2 ; +vega·E[ΔIV≈0] 0 → **+₹26.0** (× realism 1.00) | §3.4 |
| expected **premium** move — adv | `0.52·18 + 0.5·0.0028·18²` = 9.4 + 0.5 = 9.9 ; +θ drag 0.1 ; +vega·E[ΔIV −0.3] 1.4 → **−₹11.4** | §3.4 |
| **entry_mode** | ZONE_OR_TIMEOUT (`entry_wait_sec` 180) | §3.2 |
| **ideal entry** | 96.0 − 0.4·6.0 = **₹93.6** (zone bottom), floor = leg support 92.8 | §3.3 |
| **entry zone** | **₹93.6 – ₹96.9** (`ltp − 0.4·legATR₁ₘ` … `ltp + 0.15·legATR₁ₘ`); market at 180 s if unfilled | §3.2/3.3 |
| assumed fill (EV) | 94.2 (ideal + 0.6 slippage) → use **entry = ₹94.2** | §3.3 |
| **initial SL** | `sl_dist = max( 11.4 , 1.0·σ_MAE_win 10.5 )` = 11.4 ; clamp [0.7,1.3]·(1.1·14=15.4) → 11.4 in range → **SL = 94.2 − 11.4 = ₹82.8** ; `risk_ref R = 11.4` | §3.5 |
| **T1** | `1.5R` = 94.2 + 17.1 = 111.3 ; cap min(94.2+26.0, leg_res 120) = 120.2 → **T1 = ₹111.3** (1.50R) — exit 50 % | §3.6 |
| **T2** | `2.4R` = 94.2 + 27.4 = **₹121.6** (2.40R) — exit 30 % | §3.6 |
| **T3** | `3.4R` = 94.2 + 38.8 = **₹133.0** (3.40R) — runner 20 %, enabled (TRENDING, |mtf|≥25) | §3.6 |
| **trail start** | `1.0R` → premium **₹105.6** (before: static SL 82.8 + 0.6R/1.0R lock) | §3.7 |
| **trail distance** | `max( 0.9·legATR₁ₘ 6.0 = 5.4 , 0.5R = 5.7 )` → **₹5.7**, ratchets SL to `ltp − 5.7` past trail start | §3.8 |
| **max hold** | `p80 time_to_exit | winners, bucket` ≈ **1080 s** (vs fixed 1500) ; soft-exit if >720 s and MFE<0.3R | §3.9 |
| **probability** (calibrated P(win)) | **0.61** | `backtest.calibration` |
| P(target T1) / P(SL) / P(time) | **0.61 / 0.31 / 0.08** | bucket outcome freq |
| **RR** (to T1) | `(111.3 − 94.2) / 11.4` = **1.50** | §3.10 |
| **EV** | `avg_win 1.7R`, `avg_loss 0.85R`, `cost 0.06R` → `0.61·1.7 − 0.31·0.85 − 0.08·0.2 − 0.06` = 1.037 − 0.264 − 0.016 − 0.06 = **+0.70R** ≈ **₹8.0 / unit** → **PASS** (≥ 0.12R, RR ≥ 1.3) | §3.10 |

If instead `expected_premium_move_fav` were < ~₹9 (deep-OTM, low Δ) or
`sl_dist/entry` > `max_risk_frac` (rich premium, wide stop) or `EV_R` < 0.12 → the
same pipeline returns **NO_TRADE** with the failing term in `reason`.

---

## 6. Available now vs new components

| # | Need | Already in code | New component required |
|---|---|---|---|
| 1 | Strike selection | `select_option`, per-strike `analyse_leg` (Δ/Γ/θ/IV/OI), `quality_score`, `delta_fit`, candidate loop | `edge(k)` risk-adjusted term; bid/ask spread from depth; `|Δ|`×`vol_regime` payoff table |
| 2 | Entry timing | `signal_type`, `mtf_alignment`, leg ATR, per-tick monitor loop | `entry_mode` classifier; **`PENDING` trade state** + zone/timeout watcher; `NO_FILL` accounting |
| 3 | Ideal entry price | `ltp`, leg `sr.support`, leg ATR | slippage estimate; zone-bottom clamp |
| 4 | Expected premium move | `|Δ|·move + ½Γ·move²` **in `_translation`**; Θ, Vega, IV per leg; S/R levels + state target | two-sided version over `E[t]`; `translation_realism`, `E[ΔIV|win/loss]`, `E[t_T1/t_SL]` tables |
| 5 | Calibrated SL | `mae`, `result`, `regime`, `setup` on 43 rows; `dadv` computable | winner-MAE quantile table per bucket; `k_sl` solver; `risk_ref` wired from it |
| 6 | T1/T2/T3 | `mfe`, `target_*` history, leg resistance | MFE/R quantile table; **T3**; **scale-out (50/30/20)** in `update_trade_price` |
| 7 | Trail start | `mfe`/`mae`/tick history | give-back analysis → `trail_start_R`; explicit trigger vs distance |
| 8 | Dynamic trail | `_leg_bars_fn` leg bars, `trailing_stop` history | 1m leg-ATR into monitor; `k_trail`/`min_trail_R` optimiser; IV/expiry adaptation |
| 9 | Time exit | holding time derivable; `exit_reason='TIME'` rows | per-bucket time-to-outcome quantiles; soft time-decay early-exit branch |
| 10 | EV/RR NO-TRADE | `ev_gate` skeleton, `rr`, `est_cost_r` | bucket `avg_win/avg_loss/prob_T1/prob_SL` tables; **wire them into `ev_gate` (currently `None`)**; `min_abs_move`, `max_risk_frac` checks |
| — | Data substrate | `mfe`/`mae`/`exit_reason`/`result`/`hold` (43/43) | **Trade Outcome Record (§2)** — entry Greeks/IV/OI/ATR + close-time derived fields; backfill `atr_pct`, `oi_evidence` (currently hard-coded blank) |
| — | Calibration spine | `backtest.calibration` (score→P(win), 900 s refit, `scalp_signals`) | a parallel **`plan_calibration` module**: grouped quantile/mean tables with shrinkage priors (§7); persisted like `CALIB_KEY`; refit on the same cadence |

**Nothing in "new component" needs a broker order path.** All of it is analytics
over data we already persist or will persist in §2.

---

## 7. Calibration method (how each constant is fit)

- **Bucket key:** `regime | signal_type | vol_regime` (+ `underlying` when n
  allows). Fallback chain: full key → `*|signal_type|vol_regime` →
  `*|*|vol_regime` → global prior.
- **Estimator:** grouped **quantiles** (SL/target/trail/time distances) and
  **means** (`avg_win`, `avg_loss`, `translation_realism`, `E[ΔIV]`).
- **Shrinkage (essential — n is tiny):**
  `θ_bucket = (n_bucket·θ̂_bucket + n0·θ_prior) / (n_bucket + n0)`, `n0 ≈ 20`.
  With `n_bucket = 0` you get exactly today's fixed value (the prior = current
  constant). Numbers only move as real outcomes accumulate.
- **Guardrails:** every calibrated value hard-clamped to a sane band
  (`sl_dist ∈ [0.7,1.3]·(1.1·legATR)`, `T1_R ∈ [1.0,2.2]`, `k_trail ∈ [0.5,1.5]`,
  `max_hold ∈ [300, session_left−120]`). A calibrator can tune within the band,
  never outside it.
- **Refit cadence:** piggyback `runner._maybe_recalibrate` (900 s), min samples
  gate per table; persist under `autoscalp_plan_calibration` setting key next to
  `autoscalp_calibration`.
- **Look-ahead safety:** identical rule to `backtest.calibration` — only CLOSED
  rows with `closed_ts < now`; the P6 disjoint-slice discipline carries over.
- **No-data behaviour:** all tables empty → pipeline output == today's fixed-multiple
  plan, bit-for-bit (regression lock in tests).

---

## 8. Greeks never independently create a signal

- Direction is decided **before** any plan math, by `state_classifier` inside
  `compute_sr` → `decide_from_context`. `NONE` → `NO_TRADE`, plan code not reached.
- Greeks feed the plan only as **quantities** (Δ,Γ,Θ,V,IV → expected move, strike
  edge, trail adaptation) and as a **gate** (EV/RR). There is no branch of the
  form `if greek_* : decision = BUY_*`.
- `greek_confirmation()` (companion spec) can only *lower* confidence, *scale* the
  blended score within `[0.70,1.10]`, or *veto* to `NO_TRADE` — never raise, never
  create.
- Test locks: (a) `greek_*` inputs with `direction=NONE` → no plan, no decision
  key; (b) fuzz over Greek values with a fixed non-directional context → decision
  stays `NO_TRADE`; (c) empty calibration → output equals the current
  `_plan_from_leg` golden.

---

## 9. Safety / flags / rollout

- `/api/health` stays `{"live_trading": false, "paper_mode": true}`. No order path
  touched (`safeguards.check_entry`, `open_trade`, `close_trade` unchanged except
  the additive scale-out + `PENDING` state, both PAPER-only).
- Master flag `CHANAKYA_PLAN_CALIB=0` (default off) → `_plan_from_leg` unchanged.
  Sub-flags: `plan.entry_zone`, `plan.t3`, `plan.dynamic_trail`,
  `plan.calibrated_sl`, `plan.calibrated_hold` — each independently gated, each
  defaulting to today's constant.
- NIFTY `symbol_profiles` stay **frozen** until the per-bucket tables have
  ≥ 20 closed trades (task #5 gate) and a shadow-mode comparison shows the
  calibrated plan ≥ the fixed plan on `r_multiple`.
- **Rollout:**
  1. Ship §2 logging only (Trade Outcome Record + backfill `atr_pct`/`oi_evidence`).
     No behaviour change. Collect ≥ 5 sessions.
  2. Ship `plan_calibration` + the pipeline behind `CHANAKYA_PLAN_CALIB=0`.
  3. **Shadow mode** (`plan.shadow=true`): compute the calibrated plan, persist it
     alongside the executed fixed plan, execute neither differently. Compare.
  4. Enable per sub-flag, one symbol, one component at a time, re-measuring
     `r_multiple` / win-rate / `exit_reason` mix after each.
- MCX: Δ/Γ from BS-gamma only (no broker Greeks) — `expected_premium_move` uses
  gamma + realised `translation_realism`; treat `entry_delta` as `None` and lean
  on the fallback responsiveness term in `_translation`.

---

## 10. Open questions for the approver

- **Q1** Add the entry-Greeks/IV/OI columns to `ai_paper_trades` directly, or a
  sidecar `trade_features` table keyed by `trade_id`?
- **Q2** `PENDING`/zone entry adds real state-machine surface to the paper engine.
  Worth it now, or start with MARKET-only + calibrated SL/targets/trail and add
  zone entry in a later phase?
- **Q3** Scale-out (50/30/20) changes `pnl` accounting and the calibration sample
  shape (one trade → up to 3 partial exits). OK, or keep single-exit at a
  calibrated T1 for the first version?
- **Q4** `n0` (shrinkage strength) — 20 is a guess. Set from a quick
  cross-validation over the 43 rows, or accept 20 and revisit at n≈100?
- **Q5** Bucket granularity — include `underlying` in the key from the start
  (NIFTY plans ≠ NATURALGAS plans), accepting that NIFTY leans almost entirely on
  the prior for months?
