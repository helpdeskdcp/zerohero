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
# No hardcoded fallback -- fail loudly rather than silently using a public
# default credential (see ZEROHERO_FULL_AUDIT_2026-09-19.md, HIGH finding).
U="${CHANAKYA_ADMIN_USERNAME:-}"
P="${CHANAKYA_ADMIN_PASSWORD:-}"
PORT="${CHANAKYA_PORT:-7060}"
LOG="data/warm_math_ranking.log"
if [ -z "$U" ] || [ -z "$P" ]; then
  echo "$(date '+%F %T') ERROR: CHANAKYA_ADMIN_USERNAME/CHANAKYA_ADMIN_PASSWORD not set in .env -- refusing to run" >> "$LOG"
  exit 1
fi

# rotate at ~1MB
[ -f "$LOG" ] && [ "$(wc -c < "$LOG")" -gt 1000000 ] && mv "$LOG" "$LOG.1"

for prof in BALANCED CONSERVATIVE AGGRESSIVE; do
  code_time=$(curl -s -u "$U:$P" -o /dev/null -w "%{http_code} %{time_total}s" \
    "http://127.0.0.1:${PORT}/api/smart-scalper/ranking?profile=${prof}")
  echo "$(date -u +%Y-%m-%dT%H:%M:%SZ)  ${prof}  ${code_time}" >> "$LOG"
done
