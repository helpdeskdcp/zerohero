# HCS forward test — A+ gate vs the base signal

READ-ONLY. Replays the HCS A+ gate over **every resolved autoscalp signal**
(`scalp_signals`, WIN/LOSS) and compares the A+ subset to the full set. Also
records any *live* A+ signal per session to `data/hcs_forward_test.jsonl`.

- Replay + live log: `venv/bin/python scripts/hcs_forward_test.py [YYYY-MM-DD]`
- API: `GET /api/hcs/forward-test`  ·  UI: Research → HCS Engine panel (line)
- Limitation: `scalp_signals` rows carry no option chain → the OI-structure and
  option-spread modules are `UNOBSERVABLE` in the replay and **those vetoes
  could not fire** — the replay is a *lower bound* on the veto layer.

## Result — 2026-09-07 (80 resolved signals, 6 sessions, one regime)

| set | n | win % | E[R~] (W=+1/L=−1) | avg points |
|---|--:|--:|--:|--:|
| all resolved | 80 | **55.0** | +0.10 | +0.05 |
| **HCS A+** | **10** | **30.0** | **−0.40** | +1.03 |
| HCS-rejected | 70 | 58.6 | +0.17 | −0.09 |

Chronological: A+ 1st half (Sep 2–4) **0 / 5 wins**; A+ 2nd half (Sep 7) 3 / 5.

The 10 A+ signals:

| session | symbol | dec | HCS | calib p | conf | outcome |
|---|---|---|--:|--:|---|---|
| 2026-09-02 | NATURALGAS | BUY_CE | 68.0 | 0.562 | MEDIUM | LOSS |
| 2026-09-02 | NATURALGAS | BUY_CE | 68.9 | 0.562 | MEDIUM | LOSS |
| 2026-09-02 | NATURALGAS | BUY_PE | 70.7 | 0.581 | MEDIUM | LOSS |
| 2026-09-03 | NATURALGAS | BUY_CE | 70.3 | **0.820** | HIGH | LOSS |
| 2026-09-03 | NATURALGAS | BUY_CE | 71.9 | **0.809** | HIGH | LOSS |
| 2026-09-04 | NIFTY | BUY_PE | 72.0 | 0.604 | MEDIUM | LOSS |
| 2026-09-07 | BANKNIFTY | BUY_PE | 69.5 | 0.564 | MEDIUM | **WIN** (+16.3) |
| 2026-09-07 | NIFTY | BUY_PE | 68.9 | 0.579 | MEDIUM | **WIN** (+1.55) |
| 2026-09-07 | BANKNIFTY | BUY_PE | 73.2 | 0.575 | MEDIUM | LOSS |
| 2026-09-07 | CRUDEOIL | BUY_CE | 70.1 | 0.603 | MEDIUM | **WIN** (+1.3) |

Live A+ recorded for 2026-09-07: **0** (nothing cleared the gate today).

## Honest verdict

**The HCS A+ gate as currently tuned has NEGATIVE lift on the only data that
exists** (A+ win 30 % vs base 55 %). It is not yet a quality filter:

1. 6 of 10 A+ hits were NATURALGAS on Sep 2–3 — a poor stretch the confluence
   score + calibrated probability waved through (two even at HIGH confidence,
   p ≈ 0.82 — the one-week global curve grossly over-predicted).
2. Sample is n = 10 A+ over one week / one regime → **INDICATIVE ONLY, NOT
   VALIDATED**. The 2nd-half (Sep 7) 3/5 is not evidence either way.
3. The forward-test did its job: it caught that the engine is not ready. No
   promotion. Consistent with `HCS_CALIBRATION_REPORT.md`.

## Next

- Keep it SHADOW; keep recording live A+ per session (`hcs_forward_test.py`).
- Re-tune the gate (`A_PLUS` thresholds) and the `score._W` weights **against
  real resolved outcomes**, only once the log spans ≥ 40 A+ signals across
  ≥ 2 volatility regimes.
- Fix the dead live `oi` component (2026-09-05 audit) so `oi_structure` actually
  contributes.
