#!/usr/bin/env bash
# Read-only monitoring wrapper for scripts/trending_up_haircut_monitor.py.
# Cron: 30 16 * * 1-5  (16:30 IST weekdays, after the NSE close) -- appends a
# timestamped run of the TRENDING_UP haircut monitor to a log the operator reads.
# Touches NOTHING: no config, no trading logic, no calibration, no other cron.
# Remove any time with:
#   crontab -l | grep -v trending_up_haircut_monitor_cron.sh | crontab -
set -u
cd "$(dirname "$0")/.." || exit 1
LOG="data/trending_up_haircut_monitor.log"
{
  echo "=================================================================="
  echo "=== run $(date -u +%Y-%m-%dT%H:%M:%SZ)  ($(TZ=Asia/Kolkata date '+%Y-%m-%d %H:%M %Z')) ==="
  ./venv/bin/python scripts/trending_up_haircut_monitor.py
  echo
} >> "$LOG" 2>&1
