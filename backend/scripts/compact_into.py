"""Non-disruptive compaction of data/market_history.db into a NEW file with VACUUM INTO (owner conditions 2026-11-04).
 * does NOT stop any service (VACUUM INTO only needs a read snapshot; live writers keep going), does NOT rename / swap / delete anything: the original is untouched, the result is data/market_history.compact.db for the owner to inspect.
 * gates (all must pass before anything is written): free disk >= 1.15 x live bytes + 1 GiB; Drive logical-dump backup present (size + md5); market window (00:00-06:00 IST) unless --force-hours; no retention job; free inodes; fs rw; target does not exist.
 * during the run a guard aborts (kills sqlite3) if free disk drops below 1.5 GiB. A partial target is LEFT in place (never deleted by this script).
 * validation: integrity_check + foreign_key_check on the new file; schema identical; for every table the rows with id <= M (M = max id of the ORIGINAL taken just before the run; every table has an INTEGER PRIMARY KEY and tables are append-only) must have the same count, min and max id in both files.
 --dry-run evaluates the gates only. Log: data/ops/compact_into.log, result JSON: data/ops/compact_into_result.json"""
import json, os, re, shutil, sqlite3, subprocess, sys, threading, time
from datetime import datetime
B = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); DB = os.path.join(B, "data", "market_history.db"); NEW = os.path.join(B, "data", "market_history.compact.db")
LOG = os.path.join(B, "data", "ops", "compact_into.log"); RES = os.path.join(B, "data", "ops", "compact_into_result.json")
BACKUP = "gdrive200:ZeroHero-archive/backups/market_history_20261103_logical_dump.sql.gz"; BACKUP_MD5 = "bbc825915f11d0ae5905e423e4f06ee9"; BACKUP_MIN_BYTES = 1_500_000_000
import fcntl
DRY = "--dry-run" in sys.argv; FORCE_HOURS = "--force-hours" in sys.argv; GiB = 2 ** 30
def log(m):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"; print(line, flush=True)
    with open(LOG, "a") as f: f.write(line + "\n")
def sh(cmd): return subprocess.run(cmd, shell=True, capture_output=True, text=True)
def ro(path): return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=120)
def live_bytes():
    c = ro(DB); pc, fl, ps = (c.execute(f"pragma {p}").fetchone()[0] for p in ("page_count", "freelist_count", "page_size")); c.close(); return (pc - fl) * ps
def gates():
    g = {}; live = live_bytes(); free = shutil.disk_usage(B).free; need = 1.15 * live + GiB
    RESERVE = 1.5 * GiB                                                  # safety reserve is added AFTER the estimate and is never reduced: available >= estimate + reserve
    g["disk"] = (free >= need + RESERVE, f"free {free/GiB:.2f} GiB, need {(need + RESERVE)/GiB:.2f} GiB = estimate {need/GiB:.2f} GiB (live {live/GiB:.2f} GiB x 1.15 + 1 GiB) + reserve {RESERVE/GiB:.2f} GiB")
    wal = os.path.getsize(DB + "-wal") if os.path.exists(DB + "-wal") else 0
    g["wal_small"] = (wal < GiB, f"WAL {wal/2**20:.1f} MiB (must be < 1 GiB so the snapshot starts from a checkpointed state)")
    c2 = ro(DB); ps = c2.execute("pragma page_size").fetchone()[0]; pc = c2.execute("pragma page_count").fetchone()[0]; fl = c2.execute("pragma freelist_count").fetchone()[0]; c2.close(); fsz = os.path.getsize(DB)
    g["pages_consistent"] = (ps * pc == fsz or abs(ps * pc - fsz) <= ps * 64, f"page_size {ps} x page_count {pc} = {ps*pc} vs file {fsz} B; freelist {fl} pages = {fl*ps/GiB:.2f} GiB, live {(pc-fl)*ps/GiB:.2f} GiB")
    holders = []
    for pid in (x for x in os.listdir("/proc") if x.isdigit()):
        try:
            if any(os.readlink(f"/proc/{pid}/fd/{f}").endswith("market_history.db") for f in os.listdir(f"/proc/{pid}/fd")): holders.append(pid)
        except OSError: pass
    g["writers_info"] = (True, f"processes holding market_history.db open right now: {holders or 'none'} (live writers are expected; VACUUM INTO only needs a read snapshot and never stops them)")
    r = sh(f"rclone lsl {BACKUP}").stdout.split(); m = sh(f"rclone md5sum {BACKUP}").stdout.split()
    g["backup"] = (bool(r) and int(r[0]) >= BACKUP_MIN_BYTES and bool(m) and m[0] == BACKUP_MD5, f"remote size {r[0] if r else None}, md5 {m[0] if m else None}")
    h = datetime.now().hour; g["window"] = (FORCE_HOURS or h < 6, f"hour {h} (00:00-06:00 unless --force-hours)")
    g["no_retention"] = (sh("pgrep -f '[d]ata_retention.py|[p]latform_candles_retention|[l]ive_snapshots_retention|[m]ohit_filter_forward|[m]cx_uturn_forward'").stdout.strip() == "", "no retention / nightly job running")
    st = os.statvfs(B); g["inodes"] = (st.f_favail > 200000, f"free inodes {st.f_favail}")
    g["target_free"] = (not os.path.exists(NEW), f"{NEW} must not exist (never overwritten)")
    return g, live
def table_facts(path, bounds):
    c = ro(path); out = {}
    for t, M in bounds.items(): out[t] = c.execute(f'select count(*), min(id), max(id) from "{t}" where id <= ?', (M,)).fetchone()
    schema = sorted((r[0], r[1], re.sub(r"\s+", " ", r[2] or "")) for r in c.execute("select type,name,sql from sqlite_master where name not like 'sqlite_%'")); c.close(); return out, schema

APP_QUERIES = {                                                        # the access patterns the app uses (latest quotes of a contract, per-session aggregates, candle ranges); every query is bounded by id <= M
    "latest_quotes_per_symbol": "select symbol, count(*), max(id), round(sum(coalesce(ltp,0)),4), round(sum(coalesce(bid,0)),4), round(sum(coalesce(ask,0)),4) from quote_snapshots where id <= {q} group by symbol order by symbol",
    "last_5_option_ticks": "select id, symbol, strike, option_type, bid, ask, ltp from quote_snapshots where id <= {q} and kind='OPTION' order by id desc limit 50",
    "session_day_aggregate": "select session_date_ist, count(*), round(sum(coalesce(oi,0)),2), round(sum(coalesce(volume,0)),2) from quote_snapshots where id <= {q} group by session_date_ist order by session_date_ist desc limit 10",
    "candles_per_symbol": "select count(*), min(id), max(id) from market_candles where id <= {c}",
    "greeks_sample": "select count(*), round(sum(coalesce(delta,0)),6), round(sum(coalesce(iv,0)),6) from option_greeks where id <= {g}",
}
def app_results(path, bounds):
    c = ro(path); out = {}
    for k, qq in APP_QUERIES.items():
        try: out[k] = c.execute(qq.format(q=bounds.get("quote_snapshots", 0), c=bounds.get("market_candles", 0), g=bounds.get("option_greeks", 0))).fetchall()
        except sqlite3.Error as e: out[k] = f"ERR {e}"
    c.close(); return out
def main():
    lk = open(os.path.join(B, "data", "ops", "compact_into.lock"), "w")
    try: fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError: log("another compaction is running: skipped"); return 4
    log("=== compact_into start" + (" (dry run)" if DRY else "")); g, live = gates()
    for k, (ok, d) in g.items(): log(f"gate {k}: {'PASS' if ok else 'FAIL'}  {d}")
    if not all(ok for ok, _ in g.values()): log("GATES FAILED: nothing was written, no service touched"); return 2
    if DRY: log("dry run: all gates pass"); return 0
    c = ro(DB); tabs = [r[0] for r in c.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by 1")]; bounds = {t: (c.execute(f'select coalesce(max(id),0) from "{t}"').fetchone()[0]) for t in tabs}; c.close()
    size_before = os.path.getsize(DB); stop = threading.Event(); aborted = []
    def guard():
        while not stop.wait(5):
            if shutil.disk_usage(B).free < 1.5 * GiB: aborted.append(shutil.disk_usage(B).free); sh("pkill -f 'VACUUM INTO'"); return
    threading.Thread(target=guard, daemon=True).start(); s = time.time()
    r = sh(f"nice -n 15 ionice -c3 sqlite3 \"{DB}\" \"PRAGMA busy_timeout=120000; VACUUM INTO '{NEW}'\""); stop.set()
    res = {"started": datetime.now().isoformat(timespec="seconds"), "original_bytes_before": size_before, "vacuum_rc": r.returncode, "stderr": r.stderr.strip()[:300], "seconds": int(time.time() - s), "guard_abort_free_bytes": aborted or None, "bounds_M": bounds}
    if r.returncode != 0 or aborted or not os.path.exists(NEW):
        res["verdict"] = "FAILED: original untouched; partial target (if any) left in place for the owner"; log(json.dumps(res)); json.dump(res, open(RES, "w"), indent=1); return 1
    res["new_bytes"] = os.path.getsize(NEW); n = ro(NEW); ic = n.execute("pragma integrity_check").fetchall(); fk = n.execute("pragma foreign_key_check").fetchall(); n.close()
    fo, so = table_facts(DB, bounds); fn, sn = table_facts(NEW, bounds); ao, an = app_results(DB, bounds), app_results(NEW, bounds); res["app_queries_same"] = {k: (ao[k] == an[k]) for k in ao}; res["app_queries_errors"] = [k for k in ao if isinstance(ao[k], str)]
    res.update(integrity_check=[x[0] for x in ic][:3], fk_violations=len(fk), schema_same=(so == sn), counts_same=(fo == fn), facts_original=fo, facts_new=fn)
    res["verdict"] = "VERIFIED (not swapped)" if (ic == [("ok",)] and not fk and so == sn and fo == fn and all(res["app_queries_same"].values()) and not res["app_queries_errors"]) else "VALIDATION FAILED (not swapped)"
    res["free_after_bytes"] = shutil.disk_usage(B).free; log(json.dumps({k: v for k, v in res.items() if k not in ("facts_original", "facts_new", "bounds_M")})); json.dump(res, open(RES, "w"), indent=1, default=str)
    return 0 if res["verdict"].startswith("VERIFIED") else 1
if __name__ == "__main__": sys.exit(main())
