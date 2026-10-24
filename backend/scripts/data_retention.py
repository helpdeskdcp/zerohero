"""Retention for the two big capture databases. Dry-run by default; --apply deletes, in small committed batches (a long write transaction starves the live-trade price capture).
  l2_capture.db    raw_frames       keep 7 days   (only the l2capture worker reads them; the parsed snapquote_ticks stay)
                   snapquote_ticks  keep 21 days
  market_history.db quote_snapshots, option_greeks  keep 60 days, and NEVER a session on/after the arena contest start (settlement + evidence need it)
                   (raw_responses already has its own 14-day rotation in histcap)
Archive (default ON, --no-archive to skip): rows of snapquote_ticks / quote_snapshots / option_greeks are first exported per session day to
`<db>/<table>/<day>_<minid>-<maxid>.csv.gz` (NULL = \\N, bytes = b64:...) and uploaded to gdrive-guardianx:ZeroHero-archive, the upload is verified by md5, and ONLY archived days are deleted.
A failed upload keeps that day (retried next night). If the Drive account is over 11 GiB used (it is shared with the GuardianX/CSR backups), or the local disk is over 92% full, it falls back to delete-only and sends a private Telegram alert.
Rows are located by the monotonic PK id (cutoff found by binary search), so no full scan. Deleted pages are reused by new inserts (the file stops growing; it does not shrink without VACUUM).
Refuses to run while NSE/MCX is trading unless --force.   python scripts/data_retention.py [--apply] [--force]"""
import base64, csv, gzip, hashlib, json, os, shutil, sqlite3, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))
BATCH, PAUSE = 5000, 0.2
REMOTE = "gdrive-guardianx:ZeroHero-archive"
QUOTA_GUARD = 11 * 2 ** 30
DISK_PANIC_PCT = 92
ARCHIVE = {("l2_capture.db", "snapquote_ticks"), ("market_history.db", "quote_snapshots"), ("market_history.db", "option_greeks")}
# (db, table, time column, kind, keep_days, protected_from_contest_start)
POLICY = [("l2_capture.db", "raw_frames", "received_ts", "ts", 7, False),
          ("l2_capture.db", "snapquote_ticks", "session_date_ist", "day", 21, False),
          ("market_history.db", "quote_snapshots", "session_date_ist", "day", 60, True),
          ("market_history.db", "option_greeks", "session_date_ist", "day", 60, True)]


def cutoff_id(c, table, col, kind, limit):
    """Largest id whose time value is < limit (rows are inserted in time order); None if there is none."""
    lo, hi = c.execute(f"SELECT MIN(id), MAX(id) FROM {table}").fetchone()
    if lo is None: return None
    key = (lambda v: str(v)[:10]) if kind == "day" else (lambda v: str(v))
    lim = limit if kind == "day" else limit + "T00:00:00"
    first = c.execute(f"SELECT {col} FROM {table} WHERE id>=? ORDER BY id LIMIT 1", (lo,)).fetchone()[0]
    if key(first) >= lim: return None
    while lo < hi:                                              # last id with value < lim
        mid = (lo + hi + 1) // 2
        r = c.execute(f"SELECT {col} FROM {table} WHERE id>=? ORDER BY id LIMIT 1", (mid,)).fetchone()
        if r is None: hi = mid - 1; continue
        if key(r[0]) < lim: lo = mid
        else: hi = mid - 1
    return lo


class RcloneArchive:
    def __init__(self, remote=REMOTE): self.remote = remote

    def _run(self, *a, timeout=1800): return subprocess.run(["rclone", *a], capture_output=True, text=True, timeout=timeout)

    def used_bytes(self) -> int:
        r = self._run("about", self.remote.split(":")[0] + ":", "--json", timeout=120); j = json.loads(r.stdout)
        return int(j["total"]) - int(j["free"])                                              # the whole account: shared with the other backups

    def put(self, local: str, rel: str) -> bool:
        dst = f"{self.remote}/{rel}"
        if self._run("copyto", local, dst).returncode != 0: return False
        r = self._run("md5sum", dst, timeout=300)
        if r.returncode != 0 or not r.stdout.split(): return False
        return r.stdout.split()[0] == hashlib.md5(open(local, "rb").read()).hexdigest()


def _cell(v):
    if v is None: return "\\N"
    if isinstance(v, bytes): return "b64:" + base64.b64encode(v).decode()
    return v


def export_day(c, table, lo, hi, path) -> int:
    n = 0
    with gzip.open(path, "wt", newline="", compresslevel=6) as f:
        w = csv.writer(f); cur = c.execute(f"SELECT * FROM {table} WHERE id BETWEEN ? AND ? ORDER BY id", (lo, hi)); w.writerow([d[0] for d in cur.description])
        for row in cur: w.writerow([_cell(v) for v in row]); n += 1
    return n


def archive_upto(c, dbf, table, col, cid, arch, tmp, log, dry) -> tuple[int, dict]:
    """Archive whole session days with ids <= cid. Returns (largest id safely archived or cid when archiving is off, info)."""
    start = c.execute(f"SELECT MIN(id) FROM {table}").fetchone()[0]; info = {"days": 0, "bytes": 0, "stopped": None}
    if start is None: return cid, info
    last_ok = start - 1
    while start <= cid:
        day = str(c.execute(f"SELECT {col} FROM {table} WHERE id>=? ORDER BY id LIMIT 1", (start,)).fetchone()[0])[:10]
        nxt = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        end = min(cutoff_id(c, table, col, "day", nxt) or cid, cid)
        if end < start: break
        info["days"] += 1
        if dry: last_ok = end; start = end + 1; continue
        fn = os.path.join(tmp, f"{day}_{start}-{end}.csv.gz"); export_day(c, table, start, end, fn)
        size = os.path.getsize(fn); rel = f"{dbf.replace('.db', '')}/{table}/{os.path.basename(fn)}"
        if arch.used_bytes() + size > QUOTA_GUARD: info["stopped"] = "QUOTA"; os.remove(fn); break
        ok = arch.put(fn, rel); os.remove(fn)
        if not ok: info["stopped"] = "UPLOAD_FAILED"; break
        info["bytes"] += size; last_ok = end; start = end + 1; log(json.dumps({"archived": rel, "bytes": size}))
    return last_ok, info


def alert(text):
    try:
        sys.path.insert(0, HERE)
        from app.connectors import telegram
        telegram._send("Data retention: " + text, os.environ.get("TELEGRAM_CHAT_ID"))
    except Exception: pass


def market_open() -> bool:
    try:
        sys.path.insert(0, HERE)
        from app import market_calendar as mc
        return any(mc.is_trading(x) for x in ("NSE", "MCX"))
    except Exception:
        return False


def contest_start():
    try: return json.load(open(os.path.join(HERE, "data", "arena", "contest.json")))["start"]
    except Exception: return None


def run(apply: bool, force: bool = False, data_dir: str | None = None, now: datetime | None = None, log=print, archiver=None) -> list[dict]:
    if apply and not force and market_open(): log("market is trading: refusing to delete (use --force)"); return []
    d = data_dir or os.path.join(HERE, "data"); now = now or datetime.now(IST); cs = contest_start(); out = []
    for dbf, table, col, kind, keep, protect in POLICY:
        path = os.path.join(d, dbf)
        if not os.path.exists(path): continue
        limit = (now - timedelta(days=keep)).strftime("%Y-%m-%d")
        if protect and cs and cs < limit: limit = cs
        c = sqlite3.connect(path, timeout=30); c.execute("PRAGMA busy_timeout=30000")
        try:
            cid = cutoff_id(c, table, col, kind, limit); n = 0; arch_info = None
            if cid is not None and archiver is not None and (dbf, table) in ARCHIVE:
                pct = shutil.disk_usage(d).used * 100 / shutil.disk_usage(d).total
                if pct > DISK_PANIC_PCT: alert(f"disk {pct:.0f}% full: {dbf}.{table} deleted WITHOUT archive"); arch_info = {"stopped": "DISK_PANIC"}
                else:
                    tmp = tempfile.mkdtemp(prefix="retention_", dir=d)
                    try: cid, arch_info = archive_upto(c, dbf, table, col, cid, archiver, tmp, log, not apply)
                    except Exception as e: alert(f"archive error {dbf}.{table}: {e}; nothing deleted for it tonight"); arch_info = {"stopped": f"ERROR {e}"}; cid = None
                    finally: shutil.rmtree(tmp, ignore_errors=True)
                    if arch_info.get("stopped") == "QUOTA": alert(f"Drive over 11 GiB: {dbf}.{table} archive stopped, deleting WITHOUT archive from now (free Drive space or raise the guard)")
                    elif arch_info.get("stopped") == "UPLOAD_FAILED": alert(f"upload failed for {dbf}.{table}: unarchived days kept, retry tonight")
                    if arch_info.get("stopped") == "QUOTA": cid = cutoff_id(c, table, col, kind, limit)          # guard hit: delete-only, as agreed
            if cid is not None:
                n = c.execute(f"SELECT COUNT(*) FROM {table} WHERE id<=?", (cid,)).fetchone()[0]
                if apply:
                    done = 0
                    while True:
                        cur = c.execute(f"DELETE FROM {table} WHERE id IN (SELECT id FROM {table} WHERE id<=? LIMIT ?)", (cid, BATCH)); c.commit()
                        if cur.rowcount <= 0: break
                        done += cur.rowcount; time.sleep(PAUSE)
                    n = done
            rec = {"db": dbf, "table": table, "keep_days": keep, "older_than": limit, "rows": n, "applied": apply, **({"archive": arch_info} if arch_info else {})}; out.append(rec); log(json.dumps(rec))
        finally:
            c.close()
    return out


if __name__ == "__main__":
    for l in open(os.path.join(HERE, ".env")):
        if "=" in l and not l.startswith("#"): k, v = l.strip().split("=", 1); os.environ.setdefault(k, v)
    run("--apply" in sys.argv, "--force" in sys.argv, archiver=None if "--no-archive" in sys.argv else RcloneArchive())
