# Retired ad-hoc monitoring scripts (2026-09-01)

Development-era shell monitors, superseded by application endpoints:

| retired script | replaced by |
|---|---|
| autoscalp_monitor.sh        | GET /api/autoscalp/report?day=YYYY-MM-DD + daily Telegram push |
| ngcrude_session_monitor.sh  | GET /api/autoscalp/report (per-symbol rollup) |
| postclose_snap.sh           | runner._maybe_daily_report() -> Telegram at exchange close |
| session_open_wake.sh        | GET /api/autoscalp/selfcheck (market_open, segments) |
| session_report_wake.sh      | daily Telegram report (once per exchange per day) |
| first_trade_wake.sh         | autoscalp_open WS event / /api/autoscalp/signals |
| first_close_wake.sh         | autoscalp_close WS event / /api/autoscalp/signals |
| health_wake.sh              | GET /api/autoscalp/selfcheck |
| midday_wake.sh              | GET /api/autoscalp/selfcheck (poll any time) |

Not referenced by systemd, cron, or any code. Kept for provenance only.
Still active: ../nifty_holdtime_forward_test.sh (before/after hold-time
bucketing the report endpoint does not do; ends 2026-09-02 10:20 UTC).
