"""Index-friendly 'newest row at or before a time' lookups for the 15 GB append-only capture tables.

Why: `... WHERE symbol=? AND kind='OPTION' AND julianday(received_ts)<=julianday(?) ORDER BY julianday(received_ts) DESC LIMIT 1` cannot use any index on the time column, so SQLite reads EVERY row of the symbol and
sorts them (260 s on 2026-10-27, run every 5 minutes by cron scans). Rows are only ever appended, so the integer primary key grows with time: a binary search on the PK finds the id where the cutoff falls
(25 point lookups), and the query then scans only a bounded id window below it, widening it only if nothing qualifies. The qualifying predicate is unchanged, so the answer is the same row (ids may be a few
microseconds out of time order between writer threads; that cannot move the chosen instant by more than that)."""
from __future__ import annotations

from datetime import datetime, timezone

WINDOWS = (50_000, 400_000, 3_000_000)               # ids below the cutoff position, then everything below it


def _julian_str(ts: str) -> str:
    """Normalise any ISO timestamp the callers pass ('...Z', '+00:00', with/without microseconds) to the stored 'YYYY-MM-DDTHH:MM:SS.ffffffZ' shape so string order == time order."""
    d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def id_at_or_before(conn, cutoff_iso: str, table: str = "quote_snapshots", column: str = "received_ts") -> int:
    """Largest id whose `column` is <= cutoff (0 if none), by binary search over the primary key. Assumes the stored timestamps are the uniform 27-character 'Z' shape (checked for received_ts)."""
    key = _julian_str(cutoff_iso)
    hi = conn.execute(f"SELECT MAX(id) FROM {table}").fetchone()[0] or 0; lo = 0; best = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        r = conn.execute(f"SELECT id, {column} FROM {table} WHERE id>=? ORDER BY id LIMIT 1", (mid,)).fetchone()
        if r is None: hi = mid - 1; continue
        rid, ts = r
        if ts is not None and str(ts) <= key: best = max(best, rid); lo = rid + 1
        else: hi = mid - 1
    return best


def newest_matching(conn, select: str, where: str, params: tuple, cutoff_iso: str, *, table: str = "quote_snapshots", order: str, exists: tuple | None = None):
    """First row of `SELECT {select} FROM {table} NOT INDEXED WHERE id>? AND id<=? AND {where} ORDER BY {order} LIMIT 1` over widening id windows ending at the cutoff position; None if nothing qualifies anywhere.
    `where` must already contain the exact time predicate (julianday ...), so the result is what the unbounded query would return. `exists` = (sql, params) of a cheap indexed 'is there any such row at all' probe:
    when it finds nothing the answer is None at once (otherwise a symbol with no rows would be scanned across the whole table)."""
    if exists is not None:
        try: found = conn.execute(exists[0], exists[1]).fetchone()
        except Exception: found = True                                         # probe unusable on this database: skip it
        if not found: return None
    top = id_at_or_before(conn, cutoff_iso, table) + 200                    # headroom: rows stamped a hair after their neighbours' ids are still filtered exactly by `where`
    for w in (*WINDOWS, None):
        lo = 0 if w is None else max(0, top - w)
        r = conn.execute(f"SELECT {select} FROM {table} NOT INDEXED WHERE id>? AND id<=? AND {where} ORDER BY {order} LIMIT 1", (lo, top, *params)).fetchone()
        if r is not None or lo == 0: return r
    return None
