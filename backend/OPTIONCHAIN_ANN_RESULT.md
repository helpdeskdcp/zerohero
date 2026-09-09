# Option-Chain ANN Confirmation (Layer 4) — result

_2026-09-10. Built per `OPTIONCHAIN_GITHUB_AUDIT.md` §3 Layer 4: a **separate,
TRAIN-only** confirmation layer over the Option Structure Engine, kept **only on a
demonstrated OOS improvement**._

## Verdict: **INSUFFICIENT_SAMPLE** — the ANN is NOT enabled anywhere.

Also: even the *base* number is not usable (see below). This is the same
disciplined outcome as the math-scalper / order-pressure engines — not enough
data, and what there is sits in a single regime.

## What was built (`app/optionchain/`)

| file | role |
|---|---|
| `ann_confirm.py` | `OcAnnConfirm` — reuses `hcs.adaptive.OnlineLogit` + `hcs.adaptive_mc.Isotonic` (NOT a new model). `oc_feat()` = a bounded, snapshot-only feature vector from an `OptionStructureState` (max-pain distance/pull, PCR OI+vol, skew slope, 25Δ RR, ATM IV, IV-vs-realised, GEX sign, spot-vs-flip, wall distances, boxed, DQS, DTE, blocks-resolved, minute-of-day). Fit on a fold's TRAIN records only; TRAIN-picked P(win) threshold (max TRAIN expectancy over a grid); INSUFFICIENT → `p_win`=1.0 so it never filters. |
| `ann_backtest.py` | replays the captured chain (`option_greeks` snap_keys + `quote_snapshots` OI + spot), builds an `OptionStructureState` per snap, takes the structure's directional bias, labels it by the forward spot path (bracket: ±`move_pct` within `horizon_min`), runs a **purged walk-forward** (OOS = last N whole sessions, expanding TRAIN, `purge_min` gap). Reports **A = structure-only** vs **B = structure + ANN filter**, per fold + pooled, with Brier. `_verdict()` → GO / NO-GO / INSUFFICIENT_SAMPLE. |

`_verdict` rules (frozen before the run):
- `INSUFFICIENT_SAMPLE` if pooled OOS < 200, or < 3 folds, or any fold OOS < 40, or any fold's ANN was INSUFFICIENT.
- **NO-GO if structure-only OOS expectancy ≤ 0** — the ANN can never rescue a non-positive base.
- `GO` only if B beats A on: expectancy lift ≥ 0.05R **and** keep-fraction ≥ 0.35 (no cherry-picking) **and** win% not worse **and** B > A in ≥ 60% of folds.

## The run — NIFTY, 2026-09-02 … 2026-09-09

```
label: bracket +/-0.50% within 30 min ; drop unresolved ; min DQS 40
records (labeled directional signals): 1658 over 5 sessions
structure-only (all):  win% 99.3   expectancy 0.99R
pooled OOS  A: n=617  win% 100.0  expectancy 1.00R
pooled OOS  B: n=617  win% 100.0  expectancy 1.00R   (ANN kept everything)
folds OOS n:  522 / 15 / 80     <- fold 2 = 15  ->  INSUFFICIENT_SAMPLE
```

### Why the base is degenerate (not a real edge)

All 5 captured sessions fall inside one **uptrend week** (NIFTY ~ +2%). The
structure's max-pain magnet points toward max pain, which — with price below max
pain the whole week — is UP on nearly every snapshot; a ±0.5% forward bracket in
a one-directional week then resolves as a "win" ~every time. That is
**regime, not skill.** With no down sessions and only 5 days there is no base
rate to speak of, and the walk-forward folds (15–80 OOS signals) are far too
small for any verdict.

## What would be needed for a real test

- **≥ 25–40 sessions** of captured option-chain snapshots (`option_greeks` dense
  capture started 2026-09-02), spanning **up, down and range** regimes.
- Then: same harness, same frozen `_verdict`. Until then Layer 4 stays dark.

The layer and harness are committed and tested so the run is one command
(`python -m app.optionchain.ann_backtest --underlying NIFTY`) once the data
accrues. Nothing consumes the ANN today — `api.py` / the frontend never call it.
