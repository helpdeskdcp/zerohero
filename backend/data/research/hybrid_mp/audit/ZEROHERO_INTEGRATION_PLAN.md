# ZeroHero integration plan — Hybrid MP v13 (research engine) — 2026-10-14

## 0. Current state (what is and is not connected)
| piece | state |
|---|---|
| Engine, SMC layer, option model, alerts, profile, gap (`app/research_engines/hybrid_mp/`) | committed, research-only, imported by nothing in the trading path |
| Read-only dashboard API `/api/hybrid_mp/state|backtest` + page `/static/hybrid_mp.html` | wired in `main.py`, deployed (restart 2026-10-14 20:09), tested through `TestClient` and fetched live; **feature flag `HYBRID_MP_ENABLED` (default 1) added in this change — not yet deployed** |
| Autoscalp / paper engine / Telegram / orders | **not connected, by design** (strategy NO-GO; no edge) |
| Platform candle stream weekday fix (Friday) | committed `39a3412`, **not deployed** — restart needed before Fri 2026-10-16 09:00 IST |

## 1. Principles
Read-only audit first · paper/shadow default · additive code behind flags · no schema migration · no change to execution, auth, risk, audit paths · never claim integration that is not wired and tested.

## 2. Phased plan (each phase has its own gate; stop at the first failed gate)
1. **Deploy the pending fixes** (weekday in the platform candle engine, dashboard status, flag). Gate: full suite green (done: see FINAL_STATUS), `GET /api/platform/status` shows candles accepted on Friday after 09:00.
2. **Shadow logging** (observation only): a separate SQLite `data/hybrid_mp_shadow.db` (same pattern as `renko_shadow.db`) storing each engine SIGNAL/T1–T4/SL event with `alert_id` (idempotent insert) and the index price path; no order, no Telegram. Gate: ≥ 300 closed shadow trades **per instrument** before any statistic is quoted.
3. **Option plan overlay** in shadow only: for each signal call `option_model.plan_option_trade` with the live chain's contract (`app/platform/signals/contracts.select_candidates` gives ask, delta, gamma, lot size) and log "would-trade / infeasible + reason". Measured costs from `BROKER_REALISED` fills replace the assumed `Costs`. Gate: feasibility ≥ 50% at the intended capital (today: 0.5% at ₹12,000 / 1%).
4. **Paper execution** through the existing per-tenant paper engine (`app/platform/paper`) only if phase 2–3 show a **net** out-of-sample edge against the sign-flip null (gross > ≈ +0.03 R is indistinguishable from zero on this data). Gate: written approval.
5. Live execution: out of scope; the live path stays triple-gated OFF.

## 3. Feature flags and kill switches
`HYBRID_MP_ENABLED=0` (dashboard API off) · `PLATFORM_STREAM=0` (platform stream off) · tenants cannot be set to live modes (`TenantStore`). Defaults keep `live_trading=false`, `paper_mode=true`.

## 4. Deployment procedure (when approved)
1. `git status` — confirm only intended files; run `./scripts/run_tests.sh -q` (must be green).
2. Restart exactly once: `systemctl restart oi-dashboard.service`; wait ≥ 60 s.
3. Verify: `/api/health` (`live_trading:false`), `/api/platform/status` (`active_provider`, `loop_stalls`), `/api/hybrid_mp/state?symbol=NIFTY&tf=5` returns `data_status`, journal has no traceback.
4. Observe the next open (09:00–09:40 IST) with `scripts/platform_obs_summary.py`.

## 5. Rollback
* Code: `git revert 39a3412` (weekday/pivot/EOD fixes) and/or the later commits; restart the unit once. Reverting the weekday fix re-introduces the Friday defect — only do it if the fix itself misbehaves.
* Dashboard only: set `HYBRID_MP_ENABLED=0` in the unit environment and restart, or leave the code and just stop linking `/static/hybrid_mp.html`.
* Data: nothing to roll back — no schema change; derived caches (`data/research/smart_bos/cache/*.npz`) can be deleted and rebuilt read-only from `market_history.db`.

## 6. Open risks
Platform stream rejects Friday ticks until the fix is deployed · external TradingView parity unverified · option cost/IV inputs are assumptions · option premium history is only 11 sessions · the strategy has no demonstrated net edge.
