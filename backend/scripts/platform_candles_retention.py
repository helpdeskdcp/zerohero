"""Retention for data/platform_ticks.db `candles`: the 1 / 5 / 15 / 30-second candles are ~90 % of the table (14.5 M of 20 M rows after 17 days) and nothing reads them (every reader uses tf_sec=60).
Older-than-N-days short candles are first exported per day to data/archive/platform_candles/<day>_tf<sec>.csv.gz (row count verified), then deleted in small committed batches (the live writer keeps writing).
Dry-run by default.   python scripts/platform_candles_retention.py [--keep-days 3] [--apply] [--vacuum]
--vacuum rebuilds the file afterwards (locks the DB for a few minutes; needs free space = the new size); run it off-hours."""
import argparse, csv, gzip, os, sqlite3, sys, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))
SHORT = (1, 5, 15, 30)
BATCH, PAUSE = 20000, 0.15
COLS = "instrument, tf_sec, start_ts, provider, o, h, l, c, tick_count, volume, volume_complete, oi, oi_change, first_ts, last_ts"


def run(db: str, keep_days: int, apply: bool, vacuum: bool, archive_dir: str, now: float | None = None, log=print) -> dict:
    now = now or time.time(); cutoff = now - keep_days * 86400
    c = sqlite3.connect(db, timeout=60); c.execute("PRAGMA busy_timeout=60000")
    ph = ",".join("?" * len(SHORT))
    n = c.execute(f"SELECT COUNT(*) FROM candles WHERE tf_sec IN ({ph}) AND start_ts<?", (*SHORT, cutoff)).fetchone()[0]
    out = {"eligible_rows": n, "cutoff": datetime.fromtimestamp(cutoff, IST).isoformat(), "archived": 0, "deleted": 0, "vacuumed": False}
    log(f"eligible short candles older than {keep_days} d: {n}")
    if not apply or n == 0: return out
    os.makedirs(archive_dir, exist_ok=True)
    lo, hi = c.execute(f"SELECT MIN(start_ts), MAX(start_ts) FROM candles WHERE tf_sec IN ({ph}) AND start_ts<?", (*SHORT, cutoff)).fetchone()
    day = int(lo // 86400) * 86400
    while day <= hi:                                                                    # one file per UTC day and timeframe: export, verify the count, then delete exactly those rows
        for tf in SHORT:
            q = "FROM candles WHERE tf_sec=? AND start_ts>=? AND start_ts<? AND start_ts<?"; args = (tf, day, day + 86400, cutoff)
            cnt = c.execute(f"SELECT COUNT(*) {q}", args).fetchone()[0]
            if cnt == 0: continue
            fn = os.path.join(archive_dir, f"{datetime.fromtimestamp(day, timezone.utc).strftime('%Y-%m-%d')}_tf{tf}.csv.gz"); tmp = fn + ".tmp"; written = 0
            with gzip.open(tmp, "wt", newline="") as f:
                w = csv.writer(f); w.writerow(COLS.replace(" ", "").split(","))
                for row in c.execute(f"SELECT {COLS} {q} ORDER BY start_ts, instrument", args): w.writerow(row); written += 1
            if written != cnt: os.remove(tmp); raise RuntimeError(f"export count mismatch {written} != {cnt} for {fn}: nothing deleted")
            os.replace(tmp, fn); out["archived"] += written
            while True:
                cur = c.execute(f"DELETE FROM candles WHERE rowid IN (SELECT rowid {q} LIMIT ?)", (*args, BATCH)); c.commit()
                if cur.rowcount <= 0: break
                out["deleted"] += cur.rowcount; time.sleep(PAUSE)
            log(f"{os.path.basename(fn)}: {written} rows archived and deleted")
        day += 86400
    if vacuum: c.execute("VACUUM"); out["vacuumed"] = True
    c.close(); return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--keep-days", type=int, default=3); ap.add_argument("--apply", action="store_true"); ap.add_argument("--vacuum", action="store_true")
    ap.add_argument("--db", default=os.path.join(HERE, "data", "platform_ticks.db")); ap.add_argument("--archive", default=os.path.join(HERE, "data", "archive", "platform_candles"))
    a = ap.parse_args(); print(run(a.db, a.keep_days, a.apply, a.vacuum, a.archive))
