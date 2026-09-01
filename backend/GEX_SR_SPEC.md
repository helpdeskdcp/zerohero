# Spec — GEX (Gamma Exposure) levels in the S/R engine

**Status:** DRAFT · **Owner:** autoscalp · **Phase:** A (borrow vibe's deterministic
analytics — no LLM, no cost). Source of the idea: `/root/vibe/analysis/gex.py`
(MIT, hopit-ai/india-trade-cli).

---

## 1. Goal

Give `sr_engine.compute_sr` a **gamma-weighted option-positioning level** — the
GEX *flip point* and the GEX *pin* (max-|GEX|) strike — as:

- **v1a (always on):** a read-only block in `sr_diag.gex` — zero effect on any
  number the strategy uses. Purely: dashboard + recorded on the snapshot so we
  can later measure whether GEX-flip proximity actually predicts anything.
- **v1b (flag, default OFF):** two extra S/R **candidates** (family `"gex"`) fed
  into the existing zone clustering + a small `gex_backing` strength component.
  Enabling this changes `support`/`resistance`/strengths → it is a
  **strategy-affecting change**, gated behind PAPER evidence, NIFTY last.

Non-goal for this spec: feeding the GEX *regime* (POSITIVE=pinning /
NEGATIVE=trending) into `detect_regime` / `decide_from_context`. That is Phase 2.

## 2. Why GEX adds something over what we already have

`sr_engine` already has `oi_wall_ce/pe` + `oi_write_ce/pe` candidates, and FIX-2
is adding a `max_pain` column. As coded in vibe, the GEX **flip** (first strike
where `net_gex` goes + → −) sits near the OI balance point, so it partly overlaps
max-pain / PCR.

The genuinely **new** signal is the **gamma weighting**: `net_gex(K)` multiplies
each strike's OI imbalance by BS gamma, which peaks at-the-money and decays with
`|K − spot|`. So the GEX flip / pin are pulled toward the money relative to raw
OI walls (which can sit far OTM and rarely act as intraday S/R). A
gamma-weighted OI level near spot is exactly what pins or repels price
intraday — that is the edge we are testing.

Honest expectation: **incremental**, not transformative. v1a exists to measure it
before we let it move a single number.

## 3. Formula (zerohero adaptation)

vibe: `GEX(K) = OI · γ(K) · spot · lot_size · 100`, CE `+`, PE `−`,
`net_gex(K) = ce_gex(K) + pe_gex(K)`.

`spot · lot_size · 100` is a **constant across strikes** → it does not change the
flip point, the pin strike, or the sign of `total_net_gex`. So v1 works on the
**shape**:

```
shape(K) = γ(K) · ( OI_CE(K) − OI_PE(K) )
flip      = first K (low→high) where shape goes  > 0  →  ≤ 0, linearly interpolated
pin       = argmax_K |shape(K)|
regime_sign = sign( Σ_K shape(K) )        # + = pinning/range, − = trending/breakout
```

### 3.1 Gamma — we must compute it (the chain carries `gamma: None`)

`_autoscalp_chain` (main.py) hard-codes greeks to `None`; there is **no
Black-Scholes helper in the repo**. Add one, `math`-only:

```
N(x)  = 0.5 * (1 + erf(x / sqrt(2)))            # normal CDF
n(x)  = exp(-x*x/2) / sqrt(2*pi)               # normal PDF
d1    = (ln(S/K) + (r + σ²/2)·T) / (σ·sqrt(T)) ,  r = 0 (intraday)
γ(K)  = n(d1) / (S · σ · sqrt(T))
```

- `T` = years to expiry from the chain's `expiry` (DDMMMYYYY) to now (IST),
  floored at ~1/525600 (1 min) to avoid div-by-zero on 0-DTE.
- `σ` (flat IV for all strikes, v1): **bisection-solve once** from the ATM
  option's LTP against the BS price (`N` from `math.erf`), bounded `σ ∈
  [0.03, 3.0]`, ~15 iterations. If the solve fails or the ATM LTP is missing →
  **fallback** `σ ≈ (atr_5m / price) · sqrt(bars_per_year)` (realized-vol proxy,
  `bars_per_year` for 5m NSE ≈ 75·252). If that is also unavailable → **GEX
  unavailable**, `sr_diag.gex.status = "no_vol"`, no candidates emitted.
- v2 (later): per-strike IV smile instead of flat σ.

### 3.2 Guards (mirror the S/R-audit phantom-wall fix)

- `< 5` chain rows with usable OI → GEX unavailable (`status = "thin_chain"`).
- A strike needs `OI_CE > 0 or OI_PE > 0` to contribute.
- `shape` never crosses zero → `flip = None` (only `pin` emitted).
- `flip` / `pin` outside `spot ± 4·ATR` → recorded in diag but **not** emitted as
  a candidate (same distance sanity the engine already applies elsewhere).

## 4. Integration points (exact)

All in `app/engines/sr_engine.py`. Additive; existing code paths unchanged when
the flag is off.

| # | location | change |
|---|---|---|
| 1 | new helper `_bs_gamma(S, K, T, sigma)` + `_solve_iv(S, K, T, price, is_call)` | `math`-only BS gamma + bounded bisection IV solve |
| 2 | new helper `_gex_profile(chain, price, atr, *, cfg)` | returns `{status, flip, pin, total_shape, regime_sign, per_strike:[{strike, ce_oi, pe_oi, gamma, shape}], sigma, sigma_src}` — **pure, read-only** |
| 3 | `_FAMILY` dict | add `"gex_flip": "gex", "gex_pin": "gex"` |
| 4 | `_W` dict | add `"gex_backing": 0.0` (real weight only via cfg — see §5) |
| 5 | `_candidates(...)` `if mode == "index" and chain:` branch | when `cfg.gex.enabled`: `cand.append((flip, "gex_flip", cfg.gex.w_flip))` and `(pin, "gex_pin", cfg.gex.w_pin)` subject to the §3.2 distance guard |
| 6 | `_strength(...)` `c` dict | add `"gex_backing"`: `1.0` if the zone's nearest strike is within `0.5·strike_step` of `flip` or `pin`, decaying to `0` by `2·strike_step`; only non-zero when `cfg.gex.enabled` |
| 7 | `compute_sr(...)` return + `sr_diag` | always add `sr_diag["gex"] = _gex_profile(...)` (v1a). `gex_flip` / `gex_pin` / `gex_regime_sign` also surfaced at top level of the return for the runner to persist |
| 8 | `_level_diag(...)` | add `gex_dist_pct` (level → flip) to the per-level trace |

`compute_sr` already takes `config: dict | None` and `symbol` — the `gex` sub-dict
rides in `config["gex"]`. `scalp_strategy.decide_from_context` already passes
`config=cfg.get("sr") or {}`, so wiring is `strat_cfg["sr"]["gex"] = {...}` in the
runner's `strat_cfg` build — **no signature changes anywhere**.

## 5. Config

`DEFAULT_CONFIG["strategy"]["sr"]["gex"]` (merged per-symbol via `symbol_profiles`):

```python
"gex": {
    "enabled": False,       # v1b master switch — OFF everywhere until evidence
    "w_flip": 0.55,         # candidate base weight (cf. oi_wall 0.5–1.3, vwap 0.6)
    "w_pin":  0.50,
    "weight": 0.05,         # gex_backing weight in _strength; spilled from the
                            #   structural pool like oi_backing is in non-index mode
    "max_dist_atr": 4.0,    # don't emit a candidate beyond this
    "iv_floor": 0.03, "iv_cap": 3.0,
},
```

`sr_diag.gex` (v1a) is emitted **regardless of `enabled`** — it is inert.

## 6. Persistence (so we can build the evidence)

`live_market_snapshots` — via the idempotent `_MIGRATIONS` pattern
(cf. `momentum`/`state_score` in FIX-3), add:

- `gex_flip REAL`, `gex_pin REAL`, `gex_regime_sign INTEGER` (−1/0/+1),
  `gex_sigma REAL`

Runner `_persist_snapshot` writes `sig.get("gex_flip")` etc. (they come up through
`ctx` / the BUY return like `vwap_status` does). No new API surface; the existing
`/api/autoscalp/snapshots` returns them automatically (`SELECT *`).

Frontend: one read-only line in the Auto-Scalp S/R panel — `GEX flip 24180 ·
pin 24200 · γ-regime PIN` — no interaction, no new endpoint.

## 7. Phasing & the evidence gate

| phase | ships | gate to advance |
|---|---|---|
| **A1 — DONE (code)** | §4 items 1,2,7,8 + §6 persist (diag + 4 scalar columns; no candidates/strength). `_gex_profile` + `_bs_gamma`/`_solve_iv` in `sr_engine.py`; `sr_diag.gex` + `gex_flip/gex_pin/gex_regime_sign/gex_sigma` on the return, threaded through `decide_from_context` ctx/BUY and `_persist_snapshot`; 4 idempotent `_MIGRATIONS` columns. 9 tests, full suite 305 passed. Deviations from draft: `flip` = first zero-crossing **either direction** (vibe's one-way rule misses a pe-heavy-below chain); regime labels neutralised to `CALL_SKEW`/`PUT_SKEW`/`NEUTRAL`; `t_years` not yet threaded from the runner (uses `_DEFAULT_T_YEARS`, v1b passes the real T); thin-chain guard is `< 4` not `< 5`; no FE line yet (columns ride `SELECT *`). | full test suite green ✔; deploy; ≥ 5 PAPER sessions recording `gex_*` |
| **A2** | analysis: does `|entry_level − gex_flip|` or `regime_sign` separate WIN vs LOSS / help the EV gate? (same method as `SNAPSHOT_DATA_AUDIT.md` §5, needs ≥ ~30 closed trades) | a **measurable** edge (win-rate or avg-R lift) on NG/CRUDE |
| **A3** | flip `gex.enabled = True` for **NG/CRUDE only** via `symbol_profiles`; §4 items 3–6 | ≥ 10 PAPER sessions, S/R-selection diff reviewed, no regression in NG/CRUDE net-R |
| **A4** | NIFTY A/B (frozen profile — needs an explicit before/after PAPER comparison, per the standing rule) | NIFTF win-rate / avg-R unchanged or better |

We do **not** ship A3/A4 on intuition. A1 is safe to ship now because it moves
nothing.

## 8. Tests (`tests/test_sr_engine.py`)

v1a:
- `test_bs_gamma_peaks_atm_and_decays` — γ(ATM) > γ(±5%), γ > 0, finite.
- `test_solve_iv_recovers_known_sigma` — price a call at σ=0.18, solve → 0.18 ± 0.01; unsolvable price → `None`.
- `test_gex_profile_shape_and_flip_on_synthetic_chain` — hand-built 9-strike chain (put-OI heavy below spot, call-OI heavy above) → `flip` between the two sides, `pin` at the max-|shape| strike, `regime_sign` matches `Σ shape`.
- `test_gex_profile_thin_chain_is_unavailable` — 3 rows → `status="thin_chain"`, no crash.
- `test_gex_profile_no_vol_fallback_then_unavailable` — ATM LTP missing + no ATR → `status="no_vol"`, `flip/pin None`.
- `test_gex_diag_present_on_every_ok_return` — `sr_diag["gex"]` always a dict with `status`.
- `test_gex_disabled_by_default_changes_no_number` — run `compute_sr` on a fixed tape/chain with and without the (default-off) gex cfg → `support`, `resistance`, `support_strength`, `resistance_strength` **byte-identical** (the A1 safety guarantee).

v1b (added with A3):
- `test_gex_candidate_emitted_when_enabled_and_in_range`
- `test_gex_candidate_suppressed_beyond_max_dist_atr`
- `test_gex_backing_component_bounded_0_1`
- `test_nifty_frozen_profile_unaffected_when_gex_disabled` (regression)

## 9. Risks / decisions

- **Overlap with OI walls / max-pain** — real (§2). Mitigation: A2 explicitly
  measures *marginal* value; if the flip just tracks max-pain, we keep only the
  γ-weighted **pin** (which does not) and drop the flip candidate.
- **Flat-σ error** — a single ATM IV misprices wing gamma. Acceptable for v1
  (OI dominates the shape); v2 adds the smile. Recorded `gex_sigma` /
  `sigma_src` lets us audit it.
- **0-DTE `T → 0`** blows γ up. Floor `T` at 1 min and cap γ contribution; on
  NSE expiry day the runner already switches to `expiry_day_profile` — consider
  `gex.enabled=False` on that profile.
- **Compute cost** — one IV bisection (~15 iters) + γ over ~9 strikes per cycle:
  microseconds, and it now runs in `asyncio.to_thread` with the rest of
  `decide_from_context` (feed-staleness fix), so zero event-loop impact.
- **No new dependency** — `math` only. vibe's `analysis/options.compute_greeks`
  is **not** imported (keeps zerohero's venv clean; vibe's `brokers/` never
  touched).
- **PAPER only** — nothing here reaches an order path; `live_trading` unchanged.

## 10. Not doing

- Importing any vibe module into zerohero (formula ported, ~60 lines, `math`-only).
- vibe's LLM agents / debate anywhere near the scalp loop (Phase B, separate spec).
- GEX regime → `decide_from_context` (Phase 2, after A2 shows the sign has value).
- Per-strike IV smile (v2).
- lot_size / absolute GEX magnitude (irrelevant to flip/pin/sign; add only if a
  dashboard "real GEX number" is wanted).
