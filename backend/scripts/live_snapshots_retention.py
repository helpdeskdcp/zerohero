"""Retention for chanakya.db `live_market_snapshots.chain_json`: the option-chain JSON (up to 20 kB a row) is 91 % of the 2.7 GB database (2.46 GB for 707 k rows); the signal / decision columns are tiny.
For snapshots older than N days the chain JSON is first exported per session day to data/archive/live_snapshots/<day>.jsonl.gz (count verified), then set to NULL in small committed batches. The row itself
(signal, decision, score, probability, ...) stays. Readers already handle a missing chain (hcs.evidence: "no chain_json on this snapshot"). Dry-run by default.
   python scripts/live_snapshots_retention.py [--keep-days 7] [--apply] [--vacuum]      (--vacuum locks the database for minutes: run it with the app stopped or off-hours)"""
import argparse, gzip, json, os, sqlite3, time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IST = timezone(timedelta(hours=5, minutes=30))
BATCH, PAUSE = 500, 0.1


def run(db: str, keep_days: int, apply: bool, vacuum: bool, archive_dir: str, now: datetime | None = None, log=print) -> dict:
    now = now or datetime.now(IST); limit = (now - timedelta(days=keep_days)).strftime("%Y-%m-%d")
    c = sqlite3.connect(db, timeout=60); c.execute("PRAGMA busy_timeout=60000")
    days = [r[0] for r in c.execute("SELECT DISTINCT session_date FROM live_market_snapshots WHERE session_date<? AND chain_json IS NOT NULL AND chain_json<>'' ORDER BY 1", (limit,))]
    n = c.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(chain_json)),0) FROM live_market_snapshots WHERE session_date<? AND chain_json IS NOT NULL AND chain_json<>''", (limit,)).fetchone()
    out = {"older_than": limit, "days": len(days), "rows": n[0], "chain_bytes": n[1], "archived": 0, "nulled": 0, "vacuumed": False}
    log(f"snapshots older than {limit} with a chain: {n[0]} rows, {n[1] / 2 ** 20:.0f} MiB of chain JSON over {len(days)} days")
    if not apply or not days: return out
    os.makedirs(archive_dir, exist_ok=True)
    for day in days:
        q = "FROM live_market_snapshots WHERE session_date=? AND chain_json IS NOT NULL AND chain_json<>''"
        cnt = c.execute(f"SELECT COUNT(*) {q}", (day,)).fetchone()[0]; fn = os.path.join(archive_dir, f"{day}.jsonl.gz"); tmp = fn + ".tmp"; written = 0
        with gzip.open(tmp, "wt") as f:
            for rid, ts, sym, cj in c.execute(f"SELECT id, ts, symbol, chain_json {q} ORDER BY id", (day,)):
                f.write(json.dumps({"id": rid, "ts": ts, "symbol": sym, "chain_json": cj}) + "\n"); written += 1
        if written != cnt: os.remove(tmp); raise RuntimeError(f"export count mismatch {written} != {cnt} for {day}: nothing changed")
        os.replace(tmp, fn); out["archived"] += written
        while True:
            cur = c.execute("UPDATE live_market_snapshots SET chain_json=NULL WHERE id IN (SELECT id FROM live_market_snapshots WHERE session_date=? AND chain_json IS NOT NULL AND chain_json<>'' LIMIT ?)", (day, BATCH)); c.commit()
            if cur.rowcount <= 0: break
            out["nulled"] += cur.rowcount; time.sleep(PAUSE)
        log(f"{day}: {written} chains archived and cleared")
    if vacuum: c.execute("VACUUM"); out["vacuumed"] = True
    c.close(); return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--keep-days", type=int, default=7); ap.add_argument("--apply", action="store_true"); ap.add_argument("--vacuum", action="store_true")
    ap.add_argument("--db", default=os.path.join(HERE, "data", "chanakya.db")); ap.add_argument("--archive", default=os.path.join(HERE, "data", "archive", "live_snapshots"))
    a = ap.parse_args(); print(run(a.db, a.keep_days, a.apply, a.vacuum, a.archive))
