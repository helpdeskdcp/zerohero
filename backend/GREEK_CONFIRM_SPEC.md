# Greeks as a Confirmation / Context Layer for AutoScalp — Spec (design only, not implemented)

**Date:** 2026-09-03 · **Status:** DESIGN — awaiting approval · **Author:** Chanakya
**Scope:** wire the Option Greeks (broker Δ/Γ/Θ/V/IV + OI + underlying price) into the
**existing** AutoScalp signal pipeline as a *confirmation / context* layer only.

This document changes **no code**. `live_trading` stays `false`, `paper_mode` stays
`true`. No order path, no broker order method, no SDK order import, no change to
`safeguards.check_entry` or the paper-fill path. Related: [[greeks-profiles-spec]]
(`GREEKS_PROFILES_SPEC.md`) — the NSE/MCX Greek-sourcing split this layer consumes.

---

## 0. What exists today (verified by inspection)

Live signal path:

```
app/autoscalp/runner.py:516  Runner._evaluate(sym, cfg)
  ├─ market_calendar.segment_status  -> outside hours: NO_TRADE / MARKET_CLOSED
  ├─ agg.snapshot()                  -> bars_by_tf  (needs >=20 5m bars)
  ├─ chain = await to_thread(chain_provider, ...)        # ~10 option quotes, off-loop
  └─ sig = await to_thread(decide_from_context, bars, chain, atm=, calib=, config=strat_cfg)

app/engines/scalp_strategy.py:115  decide_from_context(bars_by_tf, chain, *, atm, calib, ...)
  ├─ sr  = compute_sr(bars_by_tf, chain=chain, mode="index", ...)   # S/R, VWAP, ATR, regime, MTF
  │        └─ _gex_profile(chain, price, atr)  ->  gex_flip / gex_pin /
  │                                               gex_regime_sign / gex_sigma / per_strike[gamma,shape]
  ├─ reg = regime  ;  st = state_classifier (state_score, direction BULLISH|BEARISH, false_risk)
  ├─ mtf = regime_mtf (alignment, magnitude, conflict)
  ├─ MTF gate: strong opposing HTF read -> NO_TRADE ; conflict -> caps confidence later
  ├─ ce_a / pe_a = analyse_leg(ATM CE / PE)
  ├─ conf = ce_pe_confirmation(direction, ce_a, pe_a)
  │        if conf.agreement in (CONFLICT, OPPOSING):  return NO_TRADE          # <-- L232
  ├─ sel = select_option(cands, direction)   ;  plan = _plan_from_leg(sel, direction, cfg)
  ├─ blended = 0.62*st.state_score + 0.24*sel.final_quality + 0.14*min(100, mtf.magnitude+40)   # L257
  │            blended *= regime_score_mult[regime]                                             # L260
  │            blended *= signal_type_score_mult[state]                                         # L261
  │            blended *= tod_score_mult[tod_bucket]                                            # L263
  │            blended  = clamp(0, 100)
  ├─ prob = _score_to_prob(blended, calib, regime=, signal_type=)      # logistic prior or P5 curve
  ├─ gate = ev_gate(prob, entry, sl, t1, avg_win, avg_loss, cost=est_cost_r*risk)
  │        if not gate.pass:  return NO_TRADE                                                   # L273
  ├─ confidence = _confidence(prob, false_verdict, mtf.conflict)   # LIKELY_FALSE|conflict->LOW;
  │                                                                # >=.66 HIGH; >=.56 MEDIUM; else LOW
  │        if confidence == LOW and require_min_confidence != LOW:  return WATCH                # L280
  └─ return { decision: BUY_CE|BUY_PE, signal_type, direction, strike, token, tradingsymbol,
              expiry, entry, stop_loss, target_1, target_2, trailing_stop, max_hold_sec,
              signal_score(=blended), probability(=prob), confidence, ev, rr, regime,
              mtf_alignment, support, resistance, gex_flip, gex_pin, gex_regime_sign,
              gex_sigma, vwap, vwap_status, ce_pe, calib_version, reason }

app/autoscalp/runner.py:587  _persist_snapshot(...)  -> live_market_snapshots  (gex_* columns already stored)
app/autoscalp/runner.py:598  if decision not in (BUY_CE, BUY_PE): return
                             safeguards.check_entry(...)  ->  _open_paper(sym, sig, chain)     # PAPER only
```

**Key facts:**
- `_gex_profile` (`sr_engine.py:351`) already computes BS **gamma** per strike +
  `flip / pin / regime_sign / sigma`. These are surfaced in `ctx` and the output
  and persisted — **but never enter `blended`, `_score_to_prob`, `ev_gate` or
  `_confidence`.** They are display/diagnostic today.
- Broker Δ/Γ/Θ/V/IV are **not** fetched anywhere in this path. `get_option_greeks`
  (`broker/angelone/client.py:258`) is called only by the histcap capture worker.
- The Greeks Engine (`app/greeks_engine/`) runs **after** each histcap cycle, reads
  the DB, writes `greek_exposure`. It is not on the live decision path and emits no
  signal. (Confirmed: only callers are `main.py` router mount + `histcap/worker.py`.)
- `_score_to_prob` cap: none here (the 92% cap lives in the separate chanakya_ai
  project, not this pipeline).

So the integration = **feed a Greeks snapshot into `decide_from_context`, and let a
new pure function nudge the already-computed candidate.** Nothing upstream of the
candidate changes.

---

## 1. Data flow into the pipeline

```
WS ticks ──► aggregator bars ─────────┐
chain REST (~10 option quotes) ───────┤
broker greeks REST (NSE only) ────────┤─► decide_from_context(bars, chain, atm, calib,
FUT / INDEX underlying price ─────────┘        greeks=greeks_ctx, config=strat_cfg)
```

**Fetch — `runner._evaluate`, next to the chain fetch (~L549), same `asyncio.to_thread`
off-loop pattern** (a slow Greek REST must not starve the WS reader — see
`FEED_STALENESS_AUDIT.md`):

- **NSE** (`profile.broker_endpoint_supported`, see [[greeks-profiles-spec]]):
  `raw = await to_thread(self.greeks_provider, sym, expiry)` — a thin wrapper over
  `AngelOneClient.get_option_greeks` (already 15 s TTL cached, per-key lock,
  market-data scope — **no new broker surface**). Normalise via
  `broker.angelone.greeks.normalize_greek_row` (already used by histcap).
- **MCX**: no broker Greeks (`AB9019`). Reuse the per-strike BS **gamma** already in
  `sr["gex"]["per_strike"]` from `_gex_profile` inside `compute_sr` — expose that
  sub-dict on the `sr` return. Gamma-only.
- Join Greeks to the chain OI on `(strike, side)` and run
  `greeks_engine.compute.build_snapshot(rows, underlying, expiry, underlying_price,
  ...)` — **pure, no I/O** — to get one derived record. Wrap it as `greeks_ctx`:

```
greeks_ctx = {
  "quality":          VALID | STALE | PARTIAL | INVALID | NO_DATA,
  "source":           "BROKER" | "SYNTHETIC_BS_GAMMA",
  "stale_sec": float, "coverage_pct": float,
  "net_delta_exp":    float | None,     # Σ OI·Δ  (CE Δ>0, PE Δ<0)  -> signed
  "net_gamma_exp":    float | None,     # Σ OI·Γ  (signed)
  "net_theta_exp":    float | None,
  "gamma_conc_strike": float | None,    # pin (largest |Γ-exposure| strike)
  "gamma_herfindahl": float | None,     # 1 = pinned to one strike, ->0 spread
  "oi_weighted_iv":   float | None,
  "pcr_oi":           float | None,
  "delta_drift":      float | None,     # net_delta_exp change over last N snapshots (optional, phase 2)
}
```

`greeks_ctx` (or `None`) is passed as a new kwarg `greeks=` to `decide_from_context`.
When `None` / `NO_DATA` / `INVALID` the function behaves **exactly as today**.

---

## 2. Derived metrics for CE/PE directional confirmation

**Directional (decide agree / conflict with `st["direction"]`):**

| metric | reading |
|---|---|
| `sign(net_delta_exp)` | aggregate positioning skew = `bias_sign`. BUY_CE wants `bias_sign >= 0`; BUY_PE wants `<= 0`. Magnitude vs a rolling normaliser → `STRONG` / `WEAK`. |
| `gamma_conc_strike − spot` (pin offset) | pin **above** spot → upward magnet → confirms CE; **below** → confirms PE; `|offset| <= 0.25·ATR` **and** `gamma_herfindahl` high → **pin / range risk** → do **not** confirm a trend, cap confidence. |
| `sign(net_gamma_exp)` (= GEX `regime_sign`) | negative (short-gamma) → breakouts amplify → **strengthen** a TREND/BREAKOUT candidate; positive (long-gamma) → pin / mean-revert → **weaken** it. **A2-unproven hypothesis → behind a flag, default off.** |

**Context only (never directional — feed confidence / cost, not `bias_sign`):**

| metric | reading |
|---|---|
| `net_theta_exp` vs premium | high decay on long premium + late session → cap confidence, trim `target_2`. |
| `oi_weighted_iv` (percentile) | very high → premium expensive, wider SL in R terms → raise `est_cost_r` haircut (mechanism already exists at `scalp_strategy.py:270`); near-expiry IV-crush risk. Rising IV with direction = mild tailwind for long premium. |
| `pcr_oi` | already consumed elsewhere; leave as-is, only echo into `reason`. |

Confirmation verdict:

```
agree_dir  = (direction == BULLISH and bias_sign >= 0) or (direction == BEARISH and bias_sign <= 0)
agree_pin  = (direction == BULLISH and pin >= spot)     or (direction == BEARISH and pin <= spot)
pin_risk   = |pin - spot| <= 0.25*ATR and gamma_herfindahl >= hhi_hi

agreement = OPPOSING  if (not agree_dir and not agree_pin and bias is STRONG)
            CONFLICT  if (not agree_dir) xor (not agree_pin)
            NEUTRAL   if agree_dir and agree_pin and bias is WEAK
            AGREE     if agree_dir and agree_pin and bias is STRONG
(pin_risk always forces at least CONFLICT + conf_cap MEDIUM)
```

---

## 3. How Greeks strengthen / weaken an existing candidate

New pure module `app/engines/greek_confirm.py`:

```python
def greek_confirmation(direction: str, greeks_ctx: dict | None, *,
                       spot: float, atr: float, signal_type: str,
                       cfg: dict) -> dict:
    """direction is an INPUT (BULLISH|BEARISH|NONE). Returns a nudge only.
    Never proposes a direction, never returns a decision."""
    # -> { "agreement": AGREE|NEUTRAL|CONFLICT|OPPOSING|SKIPPED,
    #      "score_mult": float,       # bounded, see table
    #      "conf_cap":   HIGH|MEDIUM|LOW,
    #      "veto":       bool,        # -> caller turns candidate into NO_TRADE
    #      "bias_sign":  -1|0|1, "pin_vs_spot": "above"|"below"|"at",
    #      "quality":    <passthrough>, "reason": str }
```

Effect table:

| agreement | `score_mult` | `conf_cap` | `veto` |
|---|---|---|---|
| `AGREE`    (VALID) | `1.00 … 1.10` | `HIGH`   | no |
| `NEUTRAL`  (VALID) | `1.00`        | `HIGH`   | no |
| `CONFLICT` (VALID) | `0.85 … 1.00` | `MEDIUM` | no |
| `OPPOSING` (VALID) | `0.70 … 0.90` | `LOW`    | **yes** — default **OFF** (`cfg.greek_veto=false`) until evidence |
| `SKIPPED`  (STALE / PARTIAL) | `min(1.00, …)` — can only weaken | `MEDIUM` | no |
| `SKIPPED`  (INVALID / NO_DATA / greeks=None) | `1.00` | unchanged | no |

Bounds `[0.70, 1.10]` are `cfg`-tunable but hard-clamped. The multiplier for the
gamma-regime term is separate and gated by `cfg.greek_regime_term` (default off).

**Application in `decide_from_context` — three edits, all after the candidate exists:**

1. **After `ce_pe_confirmation` (L231-233):**
   ```
   gk = greek_confirmation(direction, greeks, spot=base, atr=atr,
                           signal_type=st["state"], cfg=cfg.get("greek") or {})
   if gk["veto"]:
       return out_none(f"greek OPPOSING :: {gk['reason']}", {**ctx, "greek_confirm": gk})
   ```
2. **At the `blended` down-weights (L260-263)** — same multiplicative mechanism as
   `regime_score_mult`, so calibration (`_score_to_prob`) still applies to the
   adjusted number, no parallel scoring path:
   ```
   blended *= gk["score_mult"]
   ```
3. **At the confidence clamp (L279-280):**
   ```
   confidence = _cap(confidence, gk["conf_cap"])     # Greeks can only LOWER, never raise
   ```
   `ev_gate` and `_confidence` then run once on the adjusted `blended` / `prob`,
   exactly as today.

No other line changes. `_plan_from_leg` (entry/SL/T1/T2) is untouched, except the
optional `est_cost_r` bump from §2 (config-driven, already supported).

---

## 4. STALE / PARTIAL / INVALID handling

| `greeks_ctx.quality` | behaviour |
|---|---|
| **VALID** (fresh ≤ profile `stale_sec`, coverage ≥ profile min) | full effect — `score_mult` both ways, `conf_cap`, optional `veto`. |
| **STALE** (age > profile `stale_sec`) | advisory: `score_mult = min(1.0, …)` (weaken-only), `conf_cap = MEDIUM`, **no veto**, `reason` notes `greek stale <n>s`. |
| **PARTIAL** (coverage < profile min) | same as STALE. |
| **INVALID** (non-finite aggregate) / **NO_DATA** / `greeks=None` | Greeks **ignored**; pipeline identical to today (pure OI / price-action). Log `greek_confirm: SKIPPED(<why>)`. **No penalty** (a broker outage must not kill a good candidate), **no boost**. |

Principle: **fail-open on absence, fail-safe on fresh conflict.** Missing / stale
Greeks never block a trade; only `VALID` + `OPPOSING` (and only with
`cfg.greek_veto=true`) can.

Staleness threshold comes from the per-segment profile in [[greeks-profiles-spec]]
(NSE 90 s, MCX 150 s) — not a single global.

---

## 5. Final decision flow

```
MARKET DATA        WS bars  +  chain OI  +  broker greeks (NSE) / BS gamma (MCX)  +  FUT/INDEX px
      │
OI / PRICE ACTION  compute_sr -> S/R, VWAP, ATR, regime, state_score, direction,
      │            MTF (alignment/magnitude/conflict), false_risk
      │            ce_pe_confirmation  -> CONFLICT/OPPOSING => NO_TRADE      (unchanged)
      │
AUTOSCALP CANDIDATE  select_option leg  ->  _plan_from_leg (entry/SL/T1/T2/trail/hold)
      │              blended = 0.62·state_score + 0.24·opt_quality + 0.14·mtf
      │              (regime / signal_type / tod down-weights)
      │              -> calibrated probability -> ev_gate           (fail => NO_TRADE)
      │
GREEKS CONFIRMATION  greek_confirmation(direction, greeks_ctx):
      │                VALID?  bias_sign vs direction ;  pin vs spot ;
      │                gamma-regime (flagged) ;  theta / IV context
      │                -> { agreement, score_mult, conf_cap, veto }
      │              apply:  blended *= score_mult   (before _score_to_prob)
      │                      confidence = cap(confidence, conf_cap)
      │                      veto => NO_TRADE
      │              re-run ev_gate / _confidence on the adjusted number
      │
FINAL SIGNAL       BUY_CE  |  BUY_PE  |  WATCH  |  NO_TRADE
```

Greeks enter **only at stage 4**, operating on a candidate that already has a
direction (from `state_classifier`) and has passed the EV gate.

---

## 6. Final signal payload

Existing shape (`scalp_strategy.decide_from_context` return) unchanged; **two
additions**:

```
decision      : BUY_CE | BUY_PE | WATCH | NO_TRADE            (unchanged)
direction     : BULLISH | BEARISH | NONE                      (unchanged)
probability   : calibrated P(win) AFTER greek score_mult      (float 0-1; None on NO_TRADE)
confidence    : HIGH | MEDIUM | LOW  AFTER greek conf_cap     (label; Greeks only lower it)
entry, stop_loss, target_1, target_2, trailing_stop, max_hold_sec
              : from _plan_from_leg, unchanged (optional est_cost_r haircut from IV context)
signal_score  : blended, post-mult
ev, rr        : ev_gate on the adjusted probability
regime, mtf_alignment, support, resistance, gex_flip, gex_pin,
gex_regime_sign, gex_sigma, vwap, vwap_status, ce_pe, calib_version   (unchanged)
reason        : existing text
              + " | greek: <agree|conflict|neutral|opposing|stale|skipped>"
              + " (Δexp <+/-/0>, pin <above|below|at> spot" + [", gamma <long|short>"] + ")"
--- NEW ---
greek_confirm : {
    quality       : VALID | STALE | PARTIAL | INVALID | NO_DATA,
    source        : BROKER | SYNTHETIC_BS_GAMMA,
    agreement     : AGREE | NEUTRAL | CONFLICT | OPPOSING | SKIPPED,
    bias_sign     : -1 | 0 | 1,
    pin_vs_spot   : "above" | "below" | "at",
    score_mult    : float,
    conf_cap      : HIGH | MEDIUM | LOW,
    veto          : bool,
    used          : bool          # false when quality in (INVALID, NO_DATA) or greeks=None
}
```

`greek_confirm` is echoed into the `autoscalp_signal` emit and persisted (see §8).

---

## 7. Greeks must NEVER independently create a trade signal

Enforced structurally:

- `greek_confirmation()` is invoked **only after** `decide_from_context` has a
  non-`NONE` `direction` and a surviving candidate. `direction` is an **input**; the
  function has no branch that returns or sets a direction or a `decision`.
- Its entire output surface is: a bounded score multiplier `∈ [0.70, 1.10]`, a
  confidence-label *cap* (lower-only), and a boolean `veto` (→ `NO_TRADE`). **There
  is no return value that can become `BUY_CE` / `BUY_PE`.**
- Every pre-existing early `return NO_TRADE` (market closed, thin bars, MTF block,
  `ce_pe_confirmation` CONFLICT, EV-gate fail) fires **before** the Greek call — on
  those paths Greeks are never consulted.
- No `if greeks_bullish: decision = ...` anywhere. The capture layer still stores
  only broker-sourced Greeks; synthetic gamma stays confined to `sr_engine` +
  `greeks_engine` + this confirm module.
- **Test lock** (`tests/test_greek_confirm.py`): `greek_confirmation("NONE", …)`
  and any context where the upstream decision is NO_TRADE → `agreement="SKIPPED"`,
  `score_mult==1.0`, `veto==False`; a fuzz test asserts no input combination yields
  a dict containing a `decision` key.

---

## 8. Safety — unchanged

- `/api/health` stays `{"live_trading": false, "paper_mode": true}`. No order path
  exists to change; none is added.
- `safeguards.check_entry`, `_open_paper`, `_monitor`, `_finalize_close` — untouched.
- Greeks fetch = existing read-only `AngelOneClient.get_option_greeks` (market-data
  scope, already in production use by histcap). No new broker scope, no order
  method, no SDK order import. Credentials remain environment-only.
- All new code pure / read-only. Behind `CHANAKYA_GREEK_CONFIRM=0` (**default off**).
  Sub-flags: `greek.veto` (default off), `greek.regime_term` (default off).
- NIFTY `symbol_profiles` stay **frozen** — no threshold or strategy change — until
  GEX A2 evidence justifies turning the layer on for a symbol.

---

## 9. Config keys (all optional, default-off / no-op)

| key | default | effect |
|---|---|---|
| `CHANAKYA_GREEK_CONFIRM` | `0` | master switch. `0` → `greeks=None` passed, pipeline byte-identical to today. |
| `greek.score_mult_bounds` (in `strategy` cfg) | `[0.85, 1.10]` | AGREE/CONFLICT multiplier clamp (hard cap `[0.70, 1.10]`). |
| `greek.veto` | `false` | allow `VALID` + `OPPOSING` → `NO_TRADE`. |
| `greek.regime_term` | `false` | let `sign(net_gamma_exp)` weight TREND vs pin (A2 hypothesis). |
| `greek.hhi_hi` | `0.15` | `gamma_herfindahl` threshold for pin-risk. |
| `greek.pin_atr_frac` | `0.25` | `|pin-spot| <= frac·ATR` = pin zone. |
| `greek.iv_cost_bump` | `0.0` | extra `est_cost_r` per IV-percentile band. |

Per-segment `stale_sec` / `min_coverage_pct` / `iv_expected_band` come from
[[greeks-profiles-spec]], not repeated here.

---

## 10. Test plan (for the eventual implementation)

`tests/test_greek_confirm.py` (new) + additions to `tests/test_autoscalp.py`:

1. `greek_confirmation` pure-function matrix: each `(direction, bias_sign, pin
   offset, quality)` → expected `agreement / score_mult / conf_cap / veto`.
2. Direction is input-only: `"NONE"` → `SKIPPED`, no-op; fuzz → output never
   contains a `decision` key (§7 lock).
3. `CHANAKYA_GREEK_CONFIRM=0` → `decide_from_context` output **byte-identical** to a
   pre-change golden for a seeded NIFTY context (regression lock).
4. `greeks=None` / `NO_DATA` / `INVALID` → identical to (3); `used=false`.
5. `STALE` / `PARTIAL` → `score_mult <= 1.0`, `conf_cap=MEDIUM`, `veto=false`.
6. `VALID` + `AGREE` → `blended` rises within bound, `probability` moves the
   expected direction, `confidence` not raised above `_confidence(prob,…)`.
7. `VALID` + `CONFLICT` → `blended` falls, `confidence` capped `MEDIUM`.
8. `VALID` + `OPPOSING`, `greek.veto=true` → `NO_TRADE` with `greek OPPOSING`
   reason; `greek.veto=false` → `WATCH`/weakened but not vetoed.
9. Upstream NO_TRADE (thin bars, EV fail, `ce_pe` CONFLICT) → Greek path never
   entered (`greek_confirm` absent or `used=false`).
10. MCX (`SYNTHETIC_BS_GAMMA`, gamma-only) → delta/theta terms `None`, only
    pin + gamma-regime contribute; `oi_weighted_iv` context still applies.
11. Full backend suite green; `python -m compileall` clean.

---

## 11. Rollout / evidence gates

1. **Doc approved** (this file + [[greeks-profiles-spec]]).
2. Land `greek_confirm.py` + tests + the `greeks=` plumbing, **all behind
   `CHANAKYA_GREEK_CONFIRM=0`**. No runtime change. Ship with the next authorised
   restart.
3. Enable in **shadow mode**: compute `greek_confirm`, persist it to the snapshot,
   but do **not** apply `score_mult` / `conf_cap` / `veto` (a `greek.shadow=true`
   sub-flag). Collect ≥ 5 sessions / ~30 closed NIFTY trades.
4. Offline: does `agreement` separate WIN from LOSS? Does `bias_sign` lead price?
   Is `OPPOSING` actually predictive of failure? (This is GEX **A2**, measured on
   real Δ/Γ exposure this time, not just synthetic gamma.)
5. Only if (4) is positive: turn `greek.shadow=false` for one symbol, `greek.veto`
   still off. Re-measure. Then consider `greek.veto` / `greek.regime_term` / NIFTY
   A/B.

No step here enables candidates/strength for MCX, changes a NIFTY threshold, or
adds an order path.

---

## 12. Open questions for the approver

- **Q1** Ship **shadow mode** first (compute + persist, no effect) for a clean
  evidence window, or go straight to `score_mult` active (veto still off)?
- **Q2** `score_mult` bound — `[0.85, 1.10]` (gentle) or tighter `[0.90, 1.06]` for
  the first live phase?
- **Q3** Persist `greek_confirm` as new nullable columns on `live_market_snapshots`
  (needs a small `ALTER TABLE`) or as a single `greek_confirm_json` text column?
- **Q4** Broker-Greek fetch cadence: every `_evaluate` tick (≈ per 5 s, served from
  the 15 s TTL cache so ≤ 1 real POST / 15 s / symbol) or an explicit 15–30 s
  throttle in the provider wrapper?
- **Q5** MCX — include the synthetic-gamma-only confirmation from day one, or
  NSE-only until broker Greeks prove out?
