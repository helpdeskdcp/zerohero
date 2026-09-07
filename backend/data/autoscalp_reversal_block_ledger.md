# AUTOSCALP — REVERSAL-block forward ledger

_regenerated: 2026-09-07 22:16 IST · cutoff (change applied): 2026-09-07T16:45:25+00:00 · read-only_

**Change:** block `SUPPORT_REVERSAL` + `RESISTANCE_REVERSAL` on NATURALGAS / CRUDEOIL / BANKNIFTY / SENSEX. NIFTY = untouched control.
**Rollback:** restore `data/autoscalp_config_pre_reversal_block_20260907T164525Z.json` into `app_settings.autoscalp_config`.

## Frozen pre-change baseline

- window: 2026-08-31 .. 2026-09-07 (6 sessions)
- 80 decided · WR 55.0% · expectancy **+0.05 pt** · net +4.2 pt · PF 1.02 · maxDD -66.3 pt
- SUPPORT_BREAKDOWN net +94.6 (PF 2.75) · SUPPORT_REVERSAL net -43.0 · RESISTANCE_REVERSAL net -47.4

## Forward (trades opened after cutoff)

_No AUTOSCALP trades closed after the cutoff yet. Re-run after the next session._

## Verdict gate

Graduate the block from **ACCEPT-weak** to **VALIDATED** when, on the 4 blocked symbols, forward `SUPPORT_BREAKDOWN` holds **PF > 1.5** over **~3+ sessions / ~40+ trades**, the leak count stays 0, and NIFTY (control) shows no divergent regime shift that would explain the change. Reject / rollback if forward expectancy on the blocked symbols is worse than the −0.43 pt/trade pre-change MCX baseline over the same horizon.
