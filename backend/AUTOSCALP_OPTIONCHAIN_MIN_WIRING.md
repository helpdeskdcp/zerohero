# AutoScalp — Minimum Option-Chain Wiring (candidate) + walk-forward

_2026-09-10. Follow-on to `AUTOSCALP_OPTIONCHAIN_AUDIT.md`. Treats that audit as
the authoritative baseline (calibration NO-GO, ANN NOT JUSTIFIED, production
UNCHANGED, option-chain PARTIALLY WIRED)._

## 13. GO / NO-GO — **NO-GO (candidate stays dark)**

The candidate chain gate is **built, tested, and left OFF by default**. On the
only out-of-sample data available (NATURALGAS, 8-day OOS window, 10 closed
trades) the candidate reproduced the baseline **exactly** — zero decisions
changed, identical win-rate / expectancy / Brier / regime split. n=10 is far
below any threshold at which an improvement could be demonstrated. Per the task's
own rule set — *NO-GO if evidence is insufficient / historical chain data is
inadequate / improvement exists only in-sample* — this is NO-GO.

The wiring is committed so it can be switched on **per symbol** the moment there
is a resolved-trade sample that supports it. It is NOT enabled for any symbol.
NIFTY has no `chain_gates` profile and stays frozen.

## 14. Production status

**UNCHANGED.** `scalp_strategy._chain_gate_cfg` resolves `enabled = False` for
`{}` and for the shipped `strategy` default; `DEFAULT_CONFIG.symbol_profiles`
has no `chain_gates` for any symbol; with the gate off `decide_from_context`
returns are byte-identical to pre-change on the plan keys
(`entry / stop_loss / target_1 / target_2 / probability / ev / ev_r / rr`) — a
test asserts this. No execution / broker / scheduler / calibration / ANN /
threshold-optimisation / other-engine change.

---

## 1. Current wiring

(Full trace in `AUTOSCALP_OPTIONCHAIN_AUDIT.md §1`.) The point-in-time chain
already reaches `decide_from_context(bars, chain, …)` and already gates via:
`compute_sr(chain=, mode="index")` → OI-wall S/R candidates + `oi_backing` /
`oi_change_backing` strength → `state_score` → probability; `classify(chain=)` →
`_oi_at` `oi` component; ATM `analyse_leg(chain=)` → `ce_pe_confirmation`,
`select_option` + `min_option_quality`, `ev_gate`. Expiry-day is handled in the
runner (`_chain_is_expiry_day` → `expiry_day_profile` merge / 14:15 cutoff /
AUTO_ROLL / ZTH).

## 2. Exact missing wiring (now addressed as an opt-in candidate)

The one interpretable, point-in-time, chain-wide directional signal that was
**not** consumed: the **put/call OI balance (PCR) and fresh OI-writing bias over
the ATM window**. Added as `scalp_strategy._chain_bias` + a guarded gate.

Deliberately **still not wired** (Phase 3 — cannot be validated point-in-time
from the archive; see §5): PCR/max-pain as a *score* input, GEX (flip/pin/
regime), ΔOI-*unwinding* magnitude, `vega`, `bid`/`ask` (absent from the live
row).

## 3. Exact files / functions changed

| file | change |
|---|---|
| `app/engines/scalp_strategy.py` | + `_CHAIN_GATE_DEFAULTS`, `_chain_gate_cfg(cfg)`, `_conf_step(c, ±1)`, `_chain_bias(chain, atm, want, g)` (pure). In `decide_from_context`, **after `confidence` is computed and before the BUY return**, a block guarded by `cfg["chain_gates"]["enabled"]` (default **False**): CONTRADICT + marginal setup → `NO_TRADE` (`reason="option-chain bias contradicts a marginal setup …"`); CONTRADICT + strong setup → `confidence −1` notch only; CONFIRM + `confirm_bumps_confidence` → `confidence +1`. `chain_bias` dict attached to the BUY and WATCH returns for observability. **Nothing touches `blended` / `prob` / `gate` / `plan`.** |
| `app/autoscalp/runner.py` | `DEFAULT_CONFIG["strategy"]` changed from `{}` to `{"chain_gates": {"enabled": False}}` — pure documentation/discoverability; `_chain_gate_cfg` yields the same `enabled = False`, behaviour identical. |
| `tests/test_scalp_strategy_chain_gate.py` | NEW — 11 tests (§6). |

`_chain_bias` logic: ATM ±`bias_window` (3) strikes; needs ≥ `min_coverage` (0.6)
of them with CE+PE OI or → `INSUFFICIENT` (no-op). `pcr = Σpe_oi / Σce_oi`.
`doi_side` = PUT_WRITE / CALL_WRITE / FLAT from `Σ(oi_chg>0)` PE vs CE (× 1.25
dominance). Bullish (`want=="CE"`): CONFIRM if `pcr ≥ pcr_confirm` (1.30) or
`doi_side == PUT_WRITE`; CONTRADICT if `pcr ≤ pcr_contra` (0.70) and not
PUT_WRITE. Bearish mirrored. Marginal = `confidence == LOW` **or**
`ev_r < marginal_ev_r` (0.25) **or** `blended < marginal_score` (55).

## 4. Option-chain fields actually consumed by AutoScalp

Unchanged from `AUTOSCALP_OPTIONCHAIN_AUDIT.md §4` **while the gate is OFF**
(the default, all symbols). When a symbol's profile sets
`chain_gates.enabled = true`, it additionally consumes, per decision:
- ATM-window `Σ ce.oi`, `Σ pe.oi` (→ PCR),
- ATM-window `Σ (ce.oi_chg > 0)`, `Σ (pe.oi_chg > 0)` (→ writing bias).

Both are read from the same point-in-time `chain` argument; both drive only a
categorical confirmation/contradiction/confidence outcome, never a number.

## 5. Fields left research-only and why

| field | why not wired into the decision |
|---|---|
| **max-pain**, **GEX** (flip/pin/regime_sign/sigma) | computed today (max-pain in `runner._chain_oi_quality`, GEX in `sr_engine._gex_profile` — explicitly *"do NOT gate anything (v1a)"*); no point-in-time OOS evidence that gating on them improves the decision; GEX time-to-expiry isn't even expiry-aware (fixed `_DEFAULT_T_YEARS`). |
| **ΔOI unwinding (magnitude)** | only `sign(oi_chg) > 0` (fresh writing) is trustworthy on this data; `_oi_at` clamps negatives to 0. Unwinding as a distinct signal has no validated model and the state-classifier `oi` component is empirically 0.0 across all 99 resolved LIVE rows. |
| **vega**, **bid/ask** | `vega` never referenced by any decision module; `bid`/`ask` are **not present** in the live `_autoscalp_chain` row at all. |
| the isolated **`app/optionchain/`** module (`OptionStructureState` / `qualify` / `get_chain`) | research-only; only `app/main.py` mounts its read-only router. Not imported anywhere on the decision path — locked by `tests/test_autoscalp_optionchain_wiring_audit.py`. The gate above is inline in `scalp_strategy`, NOT this module, so no duplicate option-chain logic and no second signal engine. |

## 6. Tests added

`tests/test_scalp_strategy_chain_gate.py` — 11:
- `_chain_bias`: CONFIRM on put-heavy PCR (bullish); CONTRADICT on call-heavy PCR (bullish); CONFIRM on call-heavy PCR (bearish); CONFIRM on fresh put-writing at neutral PCR; NEUTRAL when balanced; INSUFFICIENT on thin coverage / thin chain / None; never crashes on malformed rows; pure/deterministic.
- gate in `decide_from_context` (upstream engines stubbed to a qualified BUY_CE): **gate OFF is a no-op** (decision + all plan keys identical, `chain_bias` is None); CONFIRM keeps BUY + attaches bias + plan untouched; CONFIRM + `confirm_bumps_confidence` raises the confidence label, plan untouched; **CONTRADICT + marginal → NO_TRADE** with the contradiction reason; **CONTRADICT + strong → BUY kept, confidence −1**, plan untouched; **INSUFFICIENT chain → no-op**; gate reads only the passed chain (two identical chains → identical bias; different chain → different verdict) — no time/global leakage.

Plus the pre-existing `tests/test_autoscalp_optionchain_wiring_audit.py` (8) still passes.

## 7. Full-suite result

**809 passed / 0 failed** (`pytest -q`, 210 s). Includes all existing
`scalp_strategy` / `autoscalp` / `sr_engine` / `state_classifier` /
`calibration_backtest` / `p61_filters_ablation` tests unchanged.

## 8. Leakage result

**No leakage.** `_chain_bias` is a pure function of `(chain, atm, want, cfg)` —
the chain is the point-in-time snapshot `decide_from_context` already receives
(`runner.py:602` `chain_provider(now)`; replay `state["chain"]` contemporaneous
with `state["ts"]`). It reads no timestamps, no globals, no future rows, no
trade outcome. Regression test `test_gate_reads_only_the_passed_chain_no_leakage`
+ `test_chain_bias_is_pure_deterministic`. The existing replay no-look-ahead
guarantees (`ReplayContext.candles` closed-bars-only, `_LegCache.fn_at(ts)`
truncation, frozen TRAIN→TEST calibration) are untouched.

## 9. Walk-forward / OOS result

Archive `/root/oi_dashboard/oi_history.db` (`cycles` 2026-07-13..08-28, `mode=ro&immutable=1`).
Single anchored chronological split, `NATURALGAS` (highest-frequency archive symbol),
`decide_every_sec = 60`, **no threshold tuned on the OOS window**:

| | BASELINE (gate OFF) | CANDIDATE (gate ON) |
|---|---|---|
| TRAIN 2026-07-13..08-14 → TEST 2026-08-17..08-26 | | |
| closed trades | 10 | **10** |
| wins / losses | 5 / 4 | **5 / 4** |
| win rate | 0.50 | **0.50** |
| expectancy (pts) | 0.085 | **0.085** |
| avg win / avg loss | 0.38 / −0.26 | **0.38 / −0.26** |
| profit factor | 1.81 | **1.81** |
| loss rate | 0.40 | **0.40** |
| BUY_CE / BUY_PE | 7 / 3 | **7 / 3** |
| WATCH | 0 | **0** |
| NO_TRADE (decisions / entries) | 1958 / 10 | **1958 / 10** |
| max drawdown (pts) | −0.5 | **−0.5** |
| Brier / ECE | 0.2606 / 0.0337 | **0.2606 / 0.0337** |
| by regime | TU n7 wr.571 / RANGE n2 / REV n1 | **identical** |

**The candidate changed nothing on the OOS window.** With n=10 closed OOS
trades — and the gate firing only on a CONTRADICT-at-marginal or a
CONFIRM-with-bump, neither of which occurred on those 10 (degraded-archive OI
coverage in the ±3 window often fails `min_coverage`, and the setups that traded
were not marginal) — there is no signal to evaluate. NIFTY (frozen config)
produced **0** closed OOS trades in an equivalent window.

## 10. Baseline vs candidate signal quality

Identical (§9). No degradation, no improvement — the gate was inert on the
sample. It cannot manufacture significance from 10 trades and does not try to.

## 11. Brier / calibration impact

Brier 0.2606 / ECE 0.0337 on the OOS slice — **identical** baseline vs candidate.
The gate does not touch `probability`, so this is expected. The absolute value
(Brier ≈ base-rate) and the n (10) both say: **the dataset does not support a
valid OOS calibration analysis** — consistent with the audit's Phase-1 NO-GO.

## 12. Expiry-day validation

Unchanged. The gate is orthogonal to expiry handling — it runs inside
`decide_from_context` after the setup qualifies; the runner's `expiry_day_profile`
merge, 14:15 cutoff, `AUTO_ROLL` and zero-to-hero are untouched, and
`tests/test_autoscalp.py` expiry-day tests (`_expiry_chain_provider`, ZTH,
cutoff, AUTO_ROLL) all pass in the 809. `_chain_bias` does not read `expiry` /
`dte`.

---

_Production remains unchanged unless OOS evidence supports the change._
