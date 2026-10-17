"""Archive EXACT duplicate MANUAL paper rows in data/chanakya.db (the broker sync used to re-insert the same broker-realised P&L; fixed 2026-10-15).

A row is archived only if ALL hold: strategy='MANUAL', closed, reason LIKE 'broker realised%' (the re-inserted family), and an EARLIER row exists on the same
IST day with the same (symboltoken, entry, exit_price, quantity, pnl). The earlier row (the original) is never touched. Rows that merely look similar but
differ in entry/qty/P&L are NOT touched (they may be genuine separate fills; the broker contract note is the truth).
Reversible: rows are COPIED into ai_paper_trades_dupe_archive (+ their paper_trade_events into paper_trade_events_dupe_archive) before deletion, after a
verified full backup. DRY-RUN by default.   Usage: python scripts/manual_rows_dedupe.py [--apply]"""
import datetime as dt
import sqlite3
import sys
import time

DB = "data/chanakya.db"
apply = "--apply" in sys.argv
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
day = lambda ts: dt.datetime.fromisoformat(ts).astimezone(IST).date().isoformat()

c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
c.row_factory = sqlite3.Row
rows = c.execute("SELECT id,trade_id,symboltoken tok,entry,exit_price ex,quantity q,pnl,closed_ts,reason FROM ai_paper_trades "
                 "WHERE strategy='MANUAL' AND closed_ts IS NOT NULL ORDER BY id").fetchall()
seen, dup = {}, []
for r in rows:
    k = (day(r["closed_ts"]), r["tok"], r["entry"], r["ex"], r["q"], r["pnl"])
    if k in seen and (r["reason"] or "").startswith("broker realised"):
        dup.append(r)
    else:
        seen.setdefault(k, r)
ids = [r["id"] for r in dup]
tids = [r["trade_id"] for r in dup]
pn = round(sum(r["pnl"] or 0 for r in dup), 2)
ev = c.execute(f"SELECT COUNT(*) FROM paper_trade_events WHERE trade_id IN ({','.join('?' * len(tids))})", tids).fetchone()[0] if tids else 0
tot = c.execute("SELECT COUNT(*), ROUND(SUM(pnl),2) FROM ai_paper_trades WHERE strategy='MANUAL'").fetchone()
print(f"MANUAL rows now: {tot[0]}, recorded P&L {tot[1]}")
print(f"exact duplicates to archive: {len(dup)} rows, their P&L {pn}, events {ev}; recorded P&L after = {round(tot[1] - pn, 2)}")
for r in dup[:5]:
    print("  e.g. id", r["id"], r["reason"][:28], r["pnl"])
c.close()
if not apply or not dup:
    print("DRY RUN (or nothing to do): no change. Re-run with --apply.")
    sys.exit(0)

bk = f"data/backups/chanakya_pre_dedupe_{time.strftime('%Y%m%d_%H%M%S')}.db"
src = sqlite3.connect(DB, timeout=60)
dst = sqlite3.connect(bk)
src.backup(dst)
dst.close()
n_live = src.execute("SELECT COUNT(*) FROM ai_paper_trades").fetchone()[0]
bkc = sqlite3.connect(f"file:{bk}?mode=ro", uri=True)
n_bk = bkc.execute("SELECT COUNT(*) FROM ai_paper_trades").fetchone()[0]
ok = bkc.execute("PRAGMA quick_check").fetchone()[0]
bkc.close()
assert n_bk >= n_live - 5 and ok == "ok", f"backup verification failed (backup {n_bk} vs live {n_live}, quick_check={ok})"
print(f"backup OK: {bk} (ai_paper_trades {n_bk} rows, quick_check={ok})")

src.execute("PRAGMA busy_timeout=60000")
src.execute("BEGIN IMMEDIATE")
try:
    src.execute("CREATE TABLE IF NOT EXISTS ai_paper_trades_dupe_archive AS SELECT * FROM ai_paper_trades WHERE 0")
    if "archived_at" not in [r[1] for r in src.execute("PRAGMA table_info(ai_paper_trades_dupe_archive)")]:
        src.execute("ALTER TABLE ai_paper_trades_dupe_archive ADD COLUMN archived_at TEXT")
    src.execute("CREATE TABLE IF NOT EXISTS paper_trade_events_dupe_archive AS SELECT * FROM paper_trade_events WHERE 0")
    q = ",".join("?" * len(ids))
    qt = ",".join("?" * len(tids))
    cols = [r[1] for r in src.execute("PRAGMA table_info(ai_paper_trades)")]
    src.execute(f"INSERT INTO ai_paper_trades_dupe_archive ({','.join(cols)}, archived_at) SELECT {','.join(cols)}, datetime('now') FROM ai_paper_trades WHERE id IN ({q})", ids)
    src.execute(f"INSERT INTO paper_trade_events_dupe_archive SELECT * FROM paper_trade_events WHERE trade_id IN ({qt})", tids)
    d1 = src.execute(f"DELETE FROM ai_paper_trades WHERE id IN ({q}) AND strategy='MANUAL' AND reason LIKE 'broker realised%'", ids).rowcount
    d2 = src.execute(f"DELETE FROM paper_trade_events WHERE trade_id IN ({qt})", tids).rowcount
    assert d1 == len(ids), f"expected {len(ids)} deletes, got {d1}"
    src.execute("COMMIT")
except Exception:
    src.execute("ROLLBACK")
    raise
after = src.execute("SELECT COUNT(*), ROUND(SUM(pnl),2) FROM ai_paper_trades WHERE strategy='MANUAL'").fetchone()
print(f"applied: archived {d1} trades + {d2} events. MANUAL rows {after[0]}, recorded P&L {after[1]}")
