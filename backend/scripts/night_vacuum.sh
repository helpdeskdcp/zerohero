#!/bin/bash
# One-off night job: compact market_history.db (22 GB file, ~11.7 GB of it free pages) -> ~10 GB. Safe by construction: it does NOTHING unless there is enough free disk, no retention job is running and the market is closed.
# Stops only oi-dashboard (the capture writer), runs VACUUM, TRUNCATE-checkpoints, starts it again and verifies health. Logs to data/ops/night_vacuum.log. Removes its own cron line at the end.
cd /root/zerohero/backend || exit 0
LOG=data/ops/night_vacuum.log; exec >> "$LOG" 2>&1
echo "=== $(date -Is) night_vacuum start"
rm_cron() { crontab -l 2>/dev/null | grep -v "scripts/night_vacuum.sh" | crontab -; }
DB=data/market_history.db
live=$(sqlite3 -readonly "$DB" "select (page_count - freelist_count) * page_size from pragma_page_count, pragma_freelist_count, pragma_page_size" 2>/dev/null)
free=$(df -B1 --output=avail / | tail -1 | tr -d ' ')
echo "live data bytes=$live free bytes=$free"
if [ -z "$live" ] || [ "$free" -lt $(( live + 1500000000 )) ]; then echo "NOT ENOUGH FREE DISK for VACUUM (need live+1.5 GB): skipped, will retry tomorrow"; exit 0; fi
if pgrep -f "[d]ata_retention.py|[p]latform_candles_retention|[l]ive_snapshots_retention" >/dev/null; then echo "a retention job is running: skipped, will retry tomorrow"; exit 0; fi
h=$(date +%H%M); if [ "$h" -ge 0900 ] && [ "$h" -le 2359 ]; then echo "market hours ($h): skipped"; exit 0; fi
before=$(stat -c %s "$DB"); systemctl stop oi-dashboard.service; sleep 3
s=$(date +%s); sqlite3 "$DB" "PRAGMA busy_timeout=120000; VACUUM; PRAGMA wal_checkpoint(TRUNCATE); PRAGMA optimize;" ; rc=$?
echo "vacuum rc=$rc seconds=$(( $(date +%s) - s )) size $before -> $(stat -c %s "$DB")"
systemctl start oi-dashboard.service; sleep 40
echo "services: $(systemctl is-active oi-dashboard.service arena-tg-reader.service oi-recorder.service | tr '\n' ' ') health: $(curl -s -m 20 -o /dev/null -w '%{http_code}' http://127.0.0.1:7060/api/health)"
echo "quick_check: $(sqlite3 -readonly "$DB" 'pragma quick_check' | head -1)"
df -h / | tail -1
rm_cron; echo "=== done"
