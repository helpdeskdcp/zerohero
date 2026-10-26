# Deployment plan — NIFTY direction engine on `oi-dashboard.service` (NOT executed; needs owner approval)

Facts (read from the unit and `app/main.py`): `oi-dashboard.service`, User=root, WorkingDirectory=/root/zerohero/backend, `venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 7060 --workers 1`, EnvironmentFile=backend/.env, Restart=always. It runs **in place from the repo** (no rsync). All `/api/*` sit behind the auth gate (Bearer `CHANAKYA_API_TOKEN` or HTTP Basic from `.env`); the new routes are behind the same gate (tested against the real app, tests/test_nifty_direction_gate.py).
What changes on disk at deploy: only `app/main.py` (one try/except router block), `frontend/static/chartscan.html`, new files. **No existing database or table is touched**: the engine writes a NEW file `data/nifty_direction.db` (created on first use, migrations v1-v2). A router import error prints `[nifty_direction] router disabled` and the rest of the app starts normally.

## 0. Pre-conditions (do not restart unless all hold)
1. Outside NSE hours (after 15:30 IST) **and** no participant with an open arena trade / pending LIMIT watch (a restart drops the in-process state; the arena rule is to restart only when nobody has an active trade). Check with the authenticated arena live endpoint (`/api/arena/live`) or the owner's Terminal view.
2. `git status` shows only intended files; `git log -1` = the approved commit; full regression suite verified (see NIFTY_DIRECTION_STATUS.md).
3. MCX is open until ~23:30 IST: the Winners-Pine NG signals and any NG paper trades also die with a restart; prefer a window after 23:45 IST or a weekend.

## 1. Backup (code + new data only; nothing existing is modified)
- `git tag pre-nifty-deploy-$(date +%Y%m%d%H%M) HEAD` (rollback anchor; the repo already has `pre-nifty-direction-2026-10-26`).
- `cp -a data/nifty_direction data/nifty_direction.bak.$(date +%Y%m%d%H%M)` (small; contains master.json, replay files).
- Record the running PID/start time: `systemctl show oi-dashboard -p MainPID,ActiveEnterTimestamp`.

## 2. Configuration checks (no secrets printed)
- `NIFTY_DIR_ADAPTER` must stay **unset** (no tested live adapter) -> `/overview` answers `NO_ADAPTER_CONFIGURED`.
- `ENABLE_LIVE_ORDERS` must be unset/false (the engine refuses to run if it is truthy).
- `backend/venv/bin/python -c "import app.main"` must import cleanly and print no `router disabled` line; `venv/bin/python -m pytest tests/test_nifty_direction*.py tests/test_rules_registry.py -q` green.
- `data/nifty_direction/master.json` exists and `weights_status` is reported honestly (MISSING until the official weights CSV exists).

## 3. Migration check
`venv/bin/python -c "from app.nifty_direction import store; import sqlite3; c=sqlite3.connect(':memory:'); print(store.migrate(c))"` -> `2`. On the real file the first request that persists applies v1-v2; re-running is a no-op (tested, incl. v1->v2 upgrade).

## 4. Restart (single unit, as for every dashboard deploy)
`systemctl restart oi-dashboard.service` — do **not** `pkill uvicorn`. Wait for `systemctl is-active` = active (~15-25 s; startup imports 1.3k modules).

## 5. Health checks (authenticated; the token is taken from the environment, never pasted into logs)
1. `journalctl -u oi-dashboard -n 80 --no-pager | grep -i -E "nifty_direction|Traceback|disabled"` -> no traceback, no "router disabled".
2. `GET /api/health` -> 200 (unauthenticated by design). 
3. `GET /api/nifty-direction/status` with the token -> 200, `paper_only:true`, `live_orders_enabled:false`, `master.weights_status` as expected.
4. `GET /api/nifty-direction/overview` -> `NO_ADAPTER_CONFIGURED` (expected) ; `GET .../overview?mode=replay&as_of=<a past IST timestamp inside market hours>` -> `data_kind: HISTORICAL`, `score.actionable:false`, `health.weights_status`.
5. Open `/static/chartscan.html`: existing chart + scan table still work; the new panel shows the `data: NONE` chip (Live) / `HISTORICAL` (Replay) and the "weights" blocker. Hard-refresh once.
6. Existing services untouched: `/api/arena/*`, autoscalp status, Winners-Pine generator heartbeat resumed (`winners_pine` runner logs), arena-tg-reader (separate unit) still active.

## 6. Rollback (any check fails)
`git revert <commit(s)>` or `git reset --hard pre-nifty-direction-2026-10-26` (code only) then `systemctl restart oi-dashboard.service`; re-run checks 5.1-5.2. Data: `data/nifty_direction.db` can simply be deleted (history only). Emergency unmount without a revert: delete the `# ---- NIFTY 50 constituents direction engine` block from `app/main.py` and restart.

## Not part of this deploy
Live adapter, official weights, any order path, cron/scheduler. None exist.
