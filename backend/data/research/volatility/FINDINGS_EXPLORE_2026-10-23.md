# H-VRP1 Stage 1 — exploratory run on on-disk captured data (2026-10-23)
Spec SPEC_VRP1_2026-10-23.md + ADDENDUM_DATA_2026-10-23.md (both committed before the run). Code run_vrp1_explore.py, numbers results_explore.json.

Usable days: 29 (24 with DTE >= 1, 5 expiry-day). Skipped 8: 4 no option mid at 09:29, 3 no index bars (Oct 20/22 etc.), 1 ATM too far from spot (captured chain is an ATM-follower, thin on some days).

| measure (DTE >= 1, n=24) | value |
|---|---|
| Spearman x vs rho | -0.31 (p 0.14) |
| mean rho, x-tercile low / mid / high | 0.60 / 0.43 / 0.28 |
| tercile diff low - high, 95% CI | +0.32 [-0.02, +0.65] |

Direction matches the hypothesis (cheaper straddle vs recent moves -> the day's move covers more of the premium), but n=24 gives intervals that touch 0, and |r| = 0.31 is below nothing we can conclude from. Verdict as pre-declared: INSUFFICIENT_SAMPLE; it neither passes nor sinks H-VRP1.

Two cautions found while reading the output:
1. rho = |09:29->15:14 move| / full straddle premium. The premium also pays for the days left to expiry, so rho is far below 1 (median 0.36; 0.99 on expiry day, 0.21 at DTE 6). The LEVEL of rho is not "overpriced", it is partly a time-horizon mismatch; only the within-DTE ranking by x could carry information. A conclusive version should normalise by DTE (e.g. compare within DTE group) — addendum B before any bigger run.
2. Captured chain days are not random: only sessions where the capture ran; thin ATM coverage on some.

Next: the same script, unchanged, re-run when the captured set reaches about 120 usable days (it grows each session), or after an Upstox pull if the owner approves the disk. No trade rule, no Stage 2.
