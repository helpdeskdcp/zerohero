# PROPOSAL — TRENDING_UP score haircut (NOT APPLIED)

**Status: PROPOSAL ONLY. Nothing was changed.** No config, weight, threshold,
curve, cron, or `live_trading` setting touched. `curl /api/health` →
`live_trading:false`. This document lays out the options + the expected effect
from a read-only simulation over the 23 resolved TRENDING_UP trades; the
operator decides whether/what to apply.

Context: `CALIBRATION_OVERCONFIDENCE_AUDIT_TRENDING_UP.md` (K8) — TRENDING_UP
n=23, calibration gap **+29.4pp** (pred 59.9% / actual 30.4%), Brier 0.315
> naive 0.212, **PF 0.50, −1.31 pts/trade → −EV as traded.**

---

## 1. The lever that already exists

`app/engines/scalp_strategy.py:36` `_DEFAULT_FILTERS`:

```python
"regime_score_mult": {"RANGE": 0.7},   # blended *= mult for that regime
```

Applied at `scalp_strategy.py:226` (advisory score) and `:319` (the blended
score that feeds `_score_to_prob`). Overridable via
`config["filters"]["regime_score_mult"]`. This is a **config change**, no code
edit, no redeploy — the same mechanism that down-weights RANGE (which had a much
milder +8pp gap).

The proposed change: add `"TRENDING_UP": <m>` to that dict.

## 2. Read-only simulation — the haircut is a WEAK lever here

Marginal-effect model on the 23 historical TRENDING_UP trades: a haircut `m`
shifts each trade's frozen probability by `logit(p') = logit(p) + k·s·(m−1)`
(global `k` has been 1.0 across every calibration blob, K8 → today), then re-test
the EV gate (`ev_r ≥ 0.12` ⇒ roughly `p ≥ 1.12/(rr+1) ≈ 0.44`).

| haircut `m` | trades taken | win % of taken | net pts | PF | exp/trade |
|---|---|---|---|---|---|
| 1.00 (today) | 23 / 23 | 30.4 % | −30.2 | 0.50 | −1.31 |
| 0.90 | 23 / 23 | 30.4 % | −30.2 | 0.50 | −1.31 |
| 0.85 | 23 / 23 | 30.4 % | −30.2 | 0.50 | −1.31 |
| 0.80 | 23 / 23 | 30.4 % | −30.2 | 0.50 | −1.31 |
| 0.75 | 23 / 23 | 30.4 % | −30.2 | 0.50 | −1.31 |
| 0.70 | 22 / 23 | 27.3 % | −37.9 | 0.38 | −1.72 |
| 0.65 | 21 / 23 | 23.8 % | −58.1 | 0.05 | −2.76 |
| 0.60 | 20 / 23 | 25.0 % | −50.4 | 0.05 | −2.52 |

**Why it barely moves anything:**

1. TRENDING_UP signals score ~0.58–0.60 predicted; the EV-gate line is ~0.44.
   A moderate haircut (m ≥ 0.75) doesn't pull them under → **same 23 trades**.
2. Only **4 / 23** trades sit within 0.06 of the gate line — those are all a
   haircut can flip.
3. At m ≤ 0.70 it starts dropping trades, but the score **doesn't discriminate
   TRENDING_UP winners from losers** (K8 §4B: `retest` etc. drive the score,
   corr with outcome ≈ 0 / negative), so the dropped trades are a coin flip —
   on this sample it drops *winners* and net P&L gets **worse**.

**A score-mult haircut cannot calibrate TRENDING_UP.** It only changes trade
*selection*, via a score that has no edge inside this regime; and even at m=0
the global curve floors at ~0.42 predicted, still above the 30 % actual.

## 3. Options (ranked)

### A — mild haircut `m = 0.80`, non-NIFTY only  ·  RECOMMENDED as an interim
- **What:** `regime_score_mult["TRENDING_UP"] = 0.80` in the strategy config for
  every symbol profile **except NIFTY** (see §4 for the exact mechanics and the
  frozen-NIFTY caveat).
- **Effect (sim):** trade count unchanged; stored `probability` and `ev_r` for
  TRENDING_UP drop ~3–5 pp — i.e. **more honest numbers** and marginally smaller
  EV-driven sizing, without amputating the regime.
- **Risk:** ~none. Fully reversible. Preserves TRENDING_UP data collection
  toward a real per-regime curve.
- **Reward:** small, unproven on n=23. This is a "stop pretending TRENDING_UP is
  average" adjustment, not a fix.

### B — aggressive haircut `m = 0.60–0.65`
- Drops ~2–3 signals; on history **made P&L worse** (drops winners too). High
  overfit risk on 23 trades in one regime. **Not recommended.**

### C — per-regime EV-gate tightening (`min_ev_r` 0.12 → 0.30 for TRENDING_UP)
- A more direct lever than a score mult (cuts the thin-EV tail regardless of
  score). **But** `ev_gate` config is per-*symbol* (`symbol_profiles[sym].ev`),
  there is **no per-regime EV hook** — this needs a **code change** in
  `decide_from_context` / `ev_gate`, so it is out of scope for a config-only
  proposal. Flag for a separate change if the operator wants it.

### D — block TRENDING_UP entirely (`block_regimes += ["TRENDING_UP"]`)
- Cleanest given the regime is −EV (PF 0.50, −1.31/trade over 23). Config-only,
  reversible.
- **Costs:** (1) stops all future TRENDING_UP data → the per-regime curve that
  is the real fix can never be built; (2) the K8 precedent blocked
  RESISTANCE_BREAKOUT / AFTERNOON only after **two independent OOS slices** —
  TRENDING_UP has **one week, one regime**. Blocking on that is a bigger call
  than the evidence bar the system has used before.

### E — do nothing structural; keep collecting  ·  the proper fix
- Keep the subgroup watcher. `TRENDING_UP|SUPPORT_REVERSAL` (n=17) needs
  `_MIN_ROWS = 40` before `calibration.fit()` will emit its own curve. Once it
  does, TRENDING_UP is scored by a curve fitted to its *own* 30 % win-rate and
  the +29 pp gap closes structurally — no haircut needed.

**Recommendation: A + E.** Apply the mild `m = 0.80` non-NIFTY haircut as an
interim honesty adjustment, and keep collecting toward the per-regime curve.
Not B, C (needs code), or D (over-reaches the evidence).

## 4. If the operator applies A — exact mechanics

1. **NIFTY is frozen** (`runner.py:203`, "byte-for-byte unchanged"). The base
   `strategy` config is inherited by every symbol, so putting the haircut in
   base `strategy.filters` would also hit NIFTY. To keep NIFTY untouched, set it
   per-symbol in `symbol_profiles` for NATURALGAS / CRUDEOIL / BANKNIFTY /
   SENSEX / FINNIFTY / MIDCPNIFTY — **not** NIFTY.
2. **Merge depth:** confirm whether `symbol_profiles[sym]` merges *deep* over
   `strategy` (line 619 `**(cfg.get("strategy") or {})`). If the merge is
   shallow, a symbol-profile `filters` key **replaces** the whole
   `_DEFAULT_FILTERS` — you must then spell out the full dict
   (`block_regimes`, `block_signal_types`, `block_tod`, and
   `regime_score_mult: {"RANGE": 0.7, "TRENDING_UP": 0.80}`), not just the new
   key. Verify this before applying.
3. **Apply via** `POST /api/autoscalp/config` (persists to `app_settings`, no
   redeploy) with the per-symbol `filters` block. Keep the current config JSON
   as a backup first (`GET /api/autoscalp/config`).
4. **Verify:** next cycle, `GET /api/autoscalp/status` → a TRENDING_UP signal's
   `signal_score` should be ~20 % lower than the same setup would have scored;
   `live_market_snapshots` for a TRENDING_UP row shows the reduced score.
   `live_trading` must still read `false`.
5. **Roll back:** re-POST the backed-up config (remove the `TRENDING_UP` key).
6. **Re-measure** after ~2 weeks / ≥ 15 new TRENDING_UP trades:
   re-run `CALIBRATION_OVERCONFIDENCE_AUDIT_TRENDING_UP.md`'s queries. If the
   gap hasn't improved and the regime is still −EV, escalate to D or C.

## 5. What this proposal is / is not

**Is:** the option set + a read-only simulation showing a score-mult haircut is a
weak lever for TRENDING_UP, with a low-risk interim recommendation.

**Is not:** an applied change. No config, code, weight, threshold, curve, cron,
or `live_trading` setting was modified. The real fix (a per-regime calibration
curve) is data-gated at `_MIN_ROWS = 40`.
