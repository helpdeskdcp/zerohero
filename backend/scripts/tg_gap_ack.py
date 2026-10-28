#!/usr/bin/env python3
"""Inspect / handle the Telegram capture journal (data/tg/capture.db). Never prints message text.
  python scripts/tg_gap_ack.py status                 summary: rows by status, pending, dead letters, gaps
  python scripts/tg_gap_ack.py gaps [STATUS]          list gaps (OPEN | RECOVERED | UNRECOVERABLE | ACKED)
  python scripts/tg_gap_ack.py ack GAP_ID "note"      accept that an UNRECOVERABLE range is lost (clears RECOVERY_REQUIRED; the gap stays in the history)
  python scripts/tg_gap_ack.py failed                 dead-letter posts (chat / message ids and the error type only)
  python scripts/tg_gap_ack.py requeue ROW_ID         put a dead-letter post back in the queue for the worker"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
from app.arena import tg_journal as TJ

j = TJ.Journal(); cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
if cmd == "status": print(json.dumps(j.summary(), indent=1, default=str))
elif cmd == "gaps":
    for g in j.gaps(sys.argv[2] if len(sys.argv) > 2 else None): print({k: g[k] for k in ("id", "chat_id", "from_id", "to_id", "kind", "status", "attempts", "recovered_count", "note")})
elif cmd == "ack": print("acknowledged" if j.ack_gap(int(sys.argv[2]), " ".join(sys.argv[3:]) or "acknowledged") else "not found, or not UNRECOVERABLE")
elif cmd == "failed":
    for r in j._c().execute("SELECT id, chat_id, message_id, edit_version, attempts, last_error FROM tg_capture WHERE ingestion_status='FAILED'"): print(dict(r))
elif cmd == "requeue": j.requeue_failed(int(sys.argv[2])); print("requeued")
else: sys.exit(__doc__)
