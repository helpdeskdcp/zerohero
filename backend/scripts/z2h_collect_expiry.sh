#!/usr/bin/env bash
# Expiry Zero-to-Hero — daily forward-capture of the current SENSEX weekly.
# Runs on the trading server (needs .env + the authenticated AngelOne session).
# Cron: 15 10 * * 1-5   (10:15 UTC = 15:45 IST, ~15 min after BSE close)
#
# Captures the 14:50-15:40 IST window of TODAY for the nearest SENSEX weekly
# option expiry into data/expiry_z2h.db (write-once per index+expiry+date), so
# the dataset accumulates the whole run-up AND the actual expiry-day window.
set -u
cd "$(dirname "$0")/.." || exit 1
export TZ=Asia/Kolkata
LOG="data/z2h_collect.log"
PY="./venv/bin/python"

# load AngelOne creds
set -a; [ -f .env ] && . ./.env; set +a

TODAY_ISO="$(date +%Y-%m-%d)"
TODAY_DOW="$(date +%u)"          # 1=Mon .. 7=Sun
[ "$TODAY_DOW" -ge 6 ] && { echo "$(date '+%F %T') skip (weekend)" >> "$LOG"; exit 0; }

# resolve the nearest SENSEX weekly expiry (DDMMMYYYY) via the client
EXPIRY="$($PY - <<'PYEOF' 2>>"$LOG"
import sys
sys.path.insert(0, ".")
try:
    from app.connectors.angelone import _market_sdk
    sdk = _market_sdk(require_auth=True)
    from datetime import datetime
    rows = [r for r in sdk.search_instruments(symbol="SENSEX", exchange="BFO")
            if r.get("expiry")]
    today = datetime.now().date()
    def d(e):
        try: return datetime.strptime(e, "%d%b%Y").date()
        except Exception: return None
    fut = sorted({r["expiry"] for r in rows if d(r["expiry"]) and d(r["expiry"]) > today}, key=d)
    print(fut[0] if fut else "")
except Exception as e:
    print("", end=""); sys.stderr.write(f"resolve failed: {e}\n")
PYEOF
)"

if [ -z "${EXPIRY:-}" ]; then
    echo "$(date '+%F %T') ERROR: could not resolve SENSEX weekly expiry" >> "$LOG"
    exit 1
fi

echo "$(date '+%F %T') collect-store SENSEX $EXPIRY $TODAY_ISO" >> "$LOG"
$PY -m app.expiry_zero_to_hero collect-store SENSEX "$EXPIRY" "$TODAY_ISO" >> "$LOG" 2>&1
rc=$?
echo "$(date '+%F %T') exit $rc" >> "$LOG"
exit $rc
