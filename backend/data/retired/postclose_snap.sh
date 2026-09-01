#!/bin/bash
set -u
DB=/root/zerohero/backend/data/chanakya.db
T=$(date -d "$(date +%F) 15:41:00" +%s)
while [ "$(date +%s)" -lt "$T" ]; do sleep 15; done
q(){ sqlite3 -noheader "$DB" "$1" 2>/dev/null; }
echo "POSTCLOSE $(date -Iseconds)"
echo "AUTOSCALP trades today: $(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND date(opened_ts)>=date('now','-1 day')")  net_pts=$(q "SELECT round(sum(pnl),2) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status!='OPEN'")  open=$(q "SELECT count(*) FROM ai_paper_trades WHERE strategy='AUTOSCALP' AND status='OPEN'")"
echo "snapshots total: $(q 'SELECT count(*) FROM live_market_snapshots')   last decision: $(q "SELECT decision||' @ '||substr(ts,12,8)||'Z ('||regime||')' FROM live_market_snapshots ORDER BY id DESC LIMIT 1")"
curl -s -m8 -u admin:admin@1234 http://127.0.0.1:7060/api/autoscalp/status | python3 -c 'import sys,json;s=json.load(sys.stdin);f=s["feed"];sg=s["safeguards"];print("armed",s["armed"],"leader",s["is_leader"],"| feed",f["connected"],round(f["last_msg_age_sec"],1),"s | open",s["open_positions"],"| realised",sg["realised_pnl_today"],"| halt",sg["halt_reason"],"| err",s["last_error"])'
