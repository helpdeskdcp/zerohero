#!/usr/bin/env bash
# Weekday 08:20 and 08:50 IST (server cron runs in Asia/Kolkata): remind to do the HUMAN Dhan login if the token will not cover today's MCX session.
set -u
cd "$(dirname "$0")/.." || exit 1
set -a; . ./.env 2>/dev/null; set +a
./venv/bin/python scripts/dhan_login_reminder.py >> data/ops/dhan_login_reminder.log 2>&1
