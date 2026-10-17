"""Maintenance for data/dhan_orderflow_ticks.db (the Dhan classified-tick capture). DRY-RUN by default; --apply does a verified backup FIRST.
  1. deletes junk rows whose ltt is impossible (< 2017: test pollution with 1970/1980 timestamps)
  2. backfills NULL symbol/kind from the CURRENT Dhan master for security ids that still resolve (expired contracts are not in the master: left NULL, never guessed)
Usage: python scripts/orderflow_ticks_maintain.py [--apply]"""
import os
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, ".")
DB = "data/dhan_orderflow_ticks.db"
apply = "--apply" in sys.argv
db = sqlite3.connect(DB, timeout=30)
junk = db.execute("SELECT COUNT(*) FROM classified_ticks WHERE ltt < 1483228800").fetchone()[0]
nulls = db.execute("SELECT security_id, COUNT(*) FROM classified_ticks WHERE symbol IS NULL AND ltt >= 1483228800 GROUP BY 1").fetchall()
print(f"junk rows (ltt < 2017): {junk}; NULL-symbol security ids: {len(nulls)} ({sum(n for _, n in nulls)} rows)")
from app.connectors import dhan_instruments as di  # noqa: E402
want = [{"exchange_segment": seg, "security_id": str(sid)} for sid, _ in nulls for seg in ("MCX", "NSE", "BSE")]
meta = {}
rows = di._load_master()
byid = {}
for r in rows:
    byid.setdefault(r.get("SEM_SMST_SECURITY_ID"), []).append(r)
for sid, _ in nulls:
    cands = byid.get(str(sid)) or []
    cands = [c for c in cands if c.get("SEM_EXM_EXCH_ID") in ("MCX", "NSE", "BSE") and c.get("SEM_INSTRUMENT_NAME") in ("FUTCOM", "FUTIDX", "OPTIDX", "OPTFUT")]
    if len(cands) == 1:                                    # ambiguous ids (same id on two exchanges) are skipped, not guessed
        m = di.describe_static_instruments([{"exchange_segment": cands[0]["SEM_EXM_EXCH_ID"] + "_X", "security_id": sid}], rows=cands)
        if str(sid) in m:
            meta[sid] = m[str(sid)]
print(f"resolvable from current master: {len(meta)} of {len(nulls)} ids")
if not apply:
    print("DRY RUN: nothing changed. Re-run with --apply (backup is taken first).")
    sys.exit(0)
os.makedirs("data/backups", exist_ok=True)
bk = f"data/backups/dhan_orderflow_ticks_{time.strftime('%Y%m%d_%H%M%S')}.db"
src = sqlite3.connect(DB)
dst = sqlite3.connect(bk)
src.backup(dst)
dst.close(); src.close()
chk = sqlite3.connect(f"file:{bk}?mode=ro", uri=True).execute("SELECT COUNT(*) FROM classified_ticks").fetchone()[0]
total = db.execute("SELECT COUNT(*) FROM classified_ticks").fetchone()[0]
assert chk == total, f"backup row count {chk} != live {total}: aborting"
print(f"backup OK: {bk} ({chk} rows)")
with db:
    db.execute("DELETE FROM classified_ticks WHERE ltt < 1483228800")
    for sid, m in meta.items():
        db.execute("UPDATE classified_ticks SET symbol=?, kind=? WHERE security_id=? AND symbol IS NULL", (m["symbol"], m["kind"], sid))
print("applied. rows now:", db.execute("SELECT COUNT(*), SUM(symbol IS NULL) FROM classified_ticks").fetchone())
