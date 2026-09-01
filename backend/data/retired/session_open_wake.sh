#!/bin/bash
# One-shot: sleep until NSE open, then exit so the agent is re-invoked to ping.
set -u
TARGET=$(date -d "$(date +%F) 09:15:00" +%s)
while [ "$(date +%s)" -lt "$TARGET" ]; do
  REM=$(( TARGET - $(date +%s) ))
  if [ "$REM" -gt 600 ]; then sleep 300; else sleep 15; fi
done
echo "SESSION_OPEN $(date -Iseconds)"
