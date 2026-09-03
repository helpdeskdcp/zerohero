#!/usr/bin/env bash
# Keep the Math Scalper ranking cache warm.
#
# /api/smart-scalper/ranking scans the index universe, and each index does ~3
# live broker calls via mathematical_confluence.context.market_context (~21s
# cold for 5 indices). That context has a per-symbol TTL cache
# (CHANAKYA_MATH_CTX_TTL_SEC, default 45s). Calling this once a minute keeps
# every symbol inside its TTL, so an interactive /ranking request returns in
# ~40ms instead of paying the cold path.
#
# Install (root crontab) — every minute, 09:00-15:45 IST = 03:30-10:15 UTC,
# Mon-Fri, plus a couple of minutes of margin:
#   * 3-10 * * 1-5  /root/zerohero/backend/scripts/warm_math_ranking.sh
#
# Safe: read-only GET, no order path, live_trading untouched.
set -u
cd "$(dirname "$0")/.." || exit 1

set -a; . ./.env 2>/dev/null; set +a
U="${CHANAKYA_ADMIN_USERNAME:-admin}"
P="${CHANAKYA_ADMIN_PASSWORD:-admin@1234}"
PORT="${CHANAKYA_PORT:-7060}"
LOG="data/warm_math_ranking.log"

# rotate at ~1MB
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1000000 ] && mv "$LOG" "$LOG.1"

for prof in BALANCED CONSERVATIVE AGGRESSIVE; do
  code_time=$(curl -s -u "$U:$P" -o /dev/null -w "%{http_code} %{time_total}s" \
    "http://127.0.0.1:${PORT}/api/smart-scalper/ranking?profile=${prof}")
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  ${prof}  ${code_time}" >> "$LOG"
done
