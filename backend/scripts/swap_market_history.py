#!/usr/bin/env python3
"""Owner-approved swap (2026-11-05) of data/market_history.db -> the verified VACUUM INTO copy data/market_history.compact.db.
Safe order: stop the ONLY writer (oi-dashboard) -> confirm nothing holds the DB -> checkpoint -> copy the rows the original gained since the snapshot (id > M, M from compact_into_result.json) and restore sqlite_sequence ->
exact per-table comparison with the original (count, min id, max id, sum id) + quick_check -> journal_mode WAL -> rename original to market_history.db.old (NEVER deleted here) and compact into place -> start the service and check health + that new rows arrive.
Any failure BEFORE the rename: nothing switched, service restarted. Failure AFTER the rename: automatic rollback (rename back) and restart. Log: data/ops/swap_market_history.log"""
import json, os, shutil, sqlite3, subprocess, sys, time
from datetime import datetime
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); D = os.path.join(B, "data"); DB = os.path.join(D, "market_history.db"); NEW = os.path.join(D, "market_history.compact.db"); OLD = DB + ".old"; LOG = os.path.join(D, "ops", "swap_market_history.log")
def log(m):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"; print(line, flush=True); open(LOG, "a").write(line + "\n")
def sh(c): return subprocess.run(c, shell=True, capture_output=True, text=True)
def holders():
    out = []
    for p in (x for x in os.listdir("/proc") if x.isdigit()):
        try:
            for f in os.listdir(f"/proc/{p}/fd"):
                t = os.readlink(f"/proc/{p}/fd/{f}")
                if t.startswith(DB) or t.startswith(NEW): out.append((p, t)); break
        except OSError: pass
    return out
def start_service(): sh("systemctl start oi-dashboard.service")
def main():
    if datetime.now().hour >= 9 and "--force" not in sys.argv: log("after 09:00: refusing (market about to open)"); return 2
    if os.path.exists(OLD): log(f"{OLD} already exists: refusing (never overwrite)"); return 3
    res = json.load(open(os.path.join(D, "ops", "compact_into_result.json"))); M = res["bounds_M"]
    if not os.path.exists(NEW) or res.get("integrity_check") != ["ok"] or not res.get("schema_same") or not res.get("counts_same"): log("compact result is not verified: refusing"); return 4
    free = shutil.disk_usage(D).free; log(f"start; free {free/2**30:.2f} GiB; new {os.path.getsize(NEW)} B; original {os.path.getsize(DB)} B")
    stopped = False; switched = False
    t0 = time.time(); pre = sh(f'sqlite3 -readonly "{NEW}" "pragma quick_check"').stdout.strip(); log(f"PRE-DOWNTIME quick_check of the new file (service still running): {pre[:80]!r} in {int(time.time()-t0)}s")
    if pre != "ok": log("quick_check failed: nothing touched"); return 5
    try:
        log("stopping oi-dashboard.service (the writer)"); sh("systemctl stop oi-dashboard.service"); stopped = True; time.sleep(5)
        h = holders()
        if h: raise RuntimeError(f"processes still hold the DB: {h}")
        sh(f'sqlite3 "{DB}" "PRAGMA busy_timeout=60000; PRAGMA wal_checkpoint(TRUNCATE);"'); log(f"checkpointed; wal {os.path.getsize(DB + '-wal') if os.path.exists(DB + '-wal') else 0} B")
        n = sqlite3.connect(NEW, timeout=120, isolation_level=None); n.execute("ATTACH DATABASE ? AS src", (f"file:{DB}?mode=ro",)) if False else None
        n.close(); n = sqlite3.connect(f"file:{NEW}?mode=rwc", uri=True, timeout=120, isolation_level=None); n.execute(f"ATTACH DATABASE 'file:{DB}?mode=ro' AS src")
        tabs = [r[0] for r in n.execute("select name from main.sqlite_master where type='table' and name not like 'sqlite_%' order by 1")]
        n.execute("BEGIN IMMEDIATE"); added = {}
        for t in tabs:
            before = n.execute(f'select count(*) from main."{t}"').fetchone()[0]; n.execute(f'INSERT OR IGNORE INTO main."{t}" SELECT * FROM src."{t}" WHERE id > ?', (M.get(t, 0),)); added[t] = n.execute(f'select count(*) from main."{t}"').fetchone()[0] - before
        removed = {}                                                          # rows the original DELETED since the snapshot (retention): mirror them, with a sanity limit of 1 % of the table
        for t in tabs:
            k = n.execute(f'select count(*) from main."{t}" where id <= ? and id not in (select id from src."{t}")', (M.get(t, 0),)).fetchone()[0]; tot = max(1, n.execute(f'select count(*) from main."{t}"').fetchone()[0])
            if k > 0.01 * tot: raise RuntimeError(f"{t}: {k} rows exist only in the copy ({100*k/tot:.1f} %): refusing to mirror deletions of that size")
            if k: n.execute(f'DELETE FROM main."{t}" WHERE id <= ? AND id NOT IN (SELECT id FROM src."{t}")', (M.get(t, 0),))
            removed[t] = k
        for name, seq in n.execute("select name, seq from src.sqlite_sequence").fetchall(): n.execute("UPDATE main.sqlite_sequence SET seq=? WHERE name=?", (seq, name))
        n.execute("COMMIT"); log(f"delta rows copied: {added}; rows removed because the original deleted them since the snapshot: {removed}")
        bad = []
        for t in tabs:
            a = n.execute(f'select count(*), min(id), max(id), sum(id) from main."{t}"').fetchone(); b = n.execute(f'select count(*), min(id), max(id), sum(id) from src."{t}"').fetchone()
            if a != b: bad.append((t, a, b))
        so, sn = dict(n.execute("select name, seq from src.sqlite_sequence")), dict(n.execute("select name, seq from main.sqlite_sequence"))
        if bad or so != sn: raise RuntimeError(f"COMPARISON FAILED: {bad} sequence_same={so == sn}")
        log("exact comparison with the original: count/min/max/sum(id) identical for all tables; sqlite_sequence identical")
        n.execute("DETACH DATABASE src"); n.close()                                              # a FRESH connection for the journal-mode change: single-row cursors above keep statements open ("database table is locked")
        w = sqlite3.connect(NEW, timeout=120, isolation_level=None); jm = w.execute("pragma journal_mode=WAL").fetchone()[0]; w.execute("pragma wal_checkpoint(TRUNCATE)"); log(f"journal_mode {jm}; page_count {w.execute('pragma page_count').fetchone()[0]}"); w.close()
        if jm != "wal": raise RuntimeError("could not set WAL")
        st = os.stat(DB)
        for ext in ("", "-wal", "-shm"):
            if os.path.exists(DB + ext): os.rename(DB + ext, OLD + ext)
        os.rename(NEW, DB)
        for ext in ("-wal", "-shm"):
            if os.path.exists(NEW + ext): os.rename(NEW + ext, DB + ext)
        os.chmod(DB, st.st_mode & 0o777); os.chown(DB, st.st_uid, st.st_gid); switched = True; log("SWITCHED: original kept as market_history.db.old")
        start_service(); stopped = False; time.sleep(45)
        health = sh("curl -s -m 20 -o /dev/null -w '%{http_code}' http://127.0.0.1:7060/api/health").stdout.strip(); act = sh("systemctl is-active oi-dashboard.service arena-tg-reader.service oi-recorder.service").stdout.split()
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60); mx1 = c.execute("select max(id) from quote_snapshots").fetchone()[0]; c.close(); time.sleep(60)
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=60); mx2 = c.execute("select max(id) from quote_snapshots").fetchone()[0]; c.close()
        log(f"services {act} health {health}; quote_snapshots max id {mx1} -> {mx2}; db {os.path.getsize(DB)} B; free {shutil.disk_usage(D).free/2**30:.2f} GiB")
        if health != "200" or any(x != "active" for x in act): raise RuntimeError("post-switch health check failed")
        log("DONE (market-closed window: growth of max id may be 0 until captures run; health OK)"); return 0
    except Exception as e:
        log(f"ABORT: {type(e).__name__}: {e}")
        if switched:
            sh("systemctl stop oi-dashboard.service"); time.sleep(5)
            try:
                for ext in ("", "-wal", "-shm"):
                    if os.path.exists(DB + ext): os.rename(DB + ext, NEW + ext)
                for ext in ("", "-wal", "-shm"):
                    if os.path.exists(OLD + ext): os.rename(OLD + ext, DB + ext)
                log("ROLLED BACK: original restored in place")
            except Exception as e2: log(f"ROLLBACK ERROR {e2}")
            stopped = True
        return 1
    finally:
        if stopped: start_service(); log("oi-dashboard.service started again")
if __name__ == "__main__": sys.exit(main())
