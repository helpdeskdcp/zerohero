#!/bin/bash
# One-shot watchdog: exit (re-invoking the agent) on the first *sustained*
# runner error or feed drop. Two consecutive bad polls required (debounce).
set -u
BASE="http://127.0.0.1:7060"; AUTH="admin:admin@1234"
DEADLINE=$(date -d "$(date +%F) 15:40:00" +%s)
BAD=0; LASTREASON=""
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  NOWHM=$(date +%H%M)
  MKT=0; [ "$NOWHM" -ge 0915 ] && [ "$NOWHM" -le 1530 ] && MKT=1
  ST=$(curl -s -m 10 -u "$AUTH" "$BASE/api/autoscalp/status" 2>/dev/null)
  SVC=$(systemctl is-active oi-dashboard.service 2>/dev/null)
  REASON=$(python3 - "$ST" "$SVC" "$MKT" <<'PY'
import json,sys
st_raw,svc,mkt=sys.argv[1],sys.argv[2],sys.argv[3]=="1"
if svc!="active": print(f"service {svc}"); raise SystemExit
try: st=json.loads(st_raw)
except Exception: print("status endpoint unreachable"); raise SystemExit
if not st.get("running"): print("runner not running"); raise SystemExit
if not st.get("is_leader"): print("lease lost (not leader)"); raise SystemExit
le=st.get("last_error")
if le: print(f"last_error: {le}"); raise SystemExit
fb=st.get("feed") or {}
if mkt:
    if not fb.get("connected"): print("feed disconnected"); raise SystemExit
    age=fb.get("last_msg_age_sec")
    if age is not None and age>90: print(f"feed stale {age:.0f}s"); raise SystemExit
    fle=fb.get("last_error")
    if fle: print(f"feed error: {fle}"); raise SystemExit
print("")
PY
)
  if [ -n "$REASON" ]; then
    BAD=$((BAD+1)); LASTREASON="$REASON"
    [ "$BAD" -ge 2 ] && { echo "HEALTH_ALERT $(date -Iseconds) :: $LASTREASON"; exit 0; }
  else
    BAD=0
  fi
  if [ "$MKT" = "1" ]; then sleep 45; else sleep 300; fi
done
echo "HEALTH_OK_TO_CLOSE $(date -Iseconds)"
