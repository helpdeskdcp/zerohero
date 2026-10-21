#!/bin/bash
# One-shot: restart oi-dashboard after MCX close so live tick capture is not interrupted, then remove this cron line. Safe to run only on 2026-10-20 after 23:40 IST.
[ "$(TZ=Asia/Kolkata date +%F)" = "2026-10-20" ] || exit 0
H=$(TZ=Asia/Kolkata date +%H%M); [ "$H" -ge 2340 ] || exit 0
systemctl restart oi-dashboard
sleep 15; echo "$(date -Is) restarted oi-dashboard: $(systemctl is-active oi-dashboard)" >> /root/zerohero/backend/data/forward_test/cron.log
crontab -l | grep -v oneshot_restart_oi_dashboard | crontab -
