"""Index-friendly 'newest row at or before a time' lookups for the 15 GB append-only capture tables.

Why: `... WHERE symbol=? AND kind='OPTION' AND julianday(received_ts)<=julianday(?) ORDER BY julianday(received_ts) DESC LIMIT 1` cannot use any index on the time column, so SQLite reads EVERY row of the symbol and
sorts them (260 s on 2026-10-27, run every 5 minutes by cron scans). Rows are only ever appended, so the integer primary key grows with time: a binary search on the PK finds the id where the cutoff falls
(25 point lookups), and the query then scans only a bounded id window below it, widening it only if nothing qualifies. The qualifying predicate is unchanged.

Ids are WRITE order and received_ts is when a capture cycle STARTED, so the two disagree by up to a cycle length: measured 2026-10-28 on 371k recent rows, received_ts trails id order by p99 525 s and max 6,891 s
(slow afternoon cycles). Two helpers are therefore provably safe supersets (floor_id / ceil_id, margin MAX_LAG_SEC) and are what time-WINDOW queries use. newest_matching (newest row <= cutoff) searches id windows
that end at the cutoff position: for a live cutoff that is the table end and the answer is exact; for a historical cutoff a row stamped just before the cutoff but written much later is not visible, which also
matches what could have been known at that instant."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

WINDOWS = (50_000, 400_000, 3_000_000)               # ids below the cutoff position, widening; nothing within ~3 M ids (about six days of capture) -> None (a chain that old is not a live chain)
MAX_LAG_SEC = float(os.environ.get("HISTCAP_MAX_LAG_SEC", "10800"))   # a batch is stamped when its cycle STARTED and written when it ended: measured 2026-10-28 on 371k recent rows, received_ts trails id order by p99 525 s, max 6,891 s
NEG_STATE = None                                       # path of the 'nothing found' watermark file (set lazily); tests override it



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


_neg_lock = threading.Lock()


def _neg_path() -> str:
    return NEG_STATE or os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", ".id_window_none.json")


def _neg_key(table, where, params, order) -> str:
    return hashlib.sha1(json.dumps([table, where, [str(x) for x in params[:-1]], order]).encode()).hexdigest()[:16]     # the cutoff (last param) is not part of the key: 'no such row' holds for every cutoff


def _neg_get(key):
    try: return json.load(open(_neg_path())).get(key)
    except Exception: return None


def _neg_put(key, frm, to):
    try:
        with _neg_lock:
            try: d = json.load(open(_neg_path()))
            except Exception: d = {}
            d[key] = {"from": int(frm), "to": int(to)}; tmp = _neg_path() + ".tmp"; json.dump(d, open(tmp, "w")); os.replace(tmp, _neg_path())
    except Exception:
        pass                                                                       # the watermark is an optimisation only


def newest_matching(conn, select: str, where: str, params: tuple, cutoff_iso: str, *, table: str = "quote_snapshots", order: str):
    """First row of `SELECT {select} FROM {table} NOT INDEXED WHERE id>? AND id<=? AND {where} ORDER BY {order} LIMIT 1` over widening id windows ending at the cutoff position; None if nothing qualifies within
    the widest window (WINDOWS[-1] ids below the cutoff). `where` must already contain the exact time predicate (julianday ..., LAST parameter = the cutoff), so inside that range the result is what the unbounded
    query would return. No 'does any such row exist' probe (285 s on the source index for a symbol without rows, measured 2026-10-27). Instead a miss is remembered as a watermark (ids [from, to] hold no matching row;
    rows are only appended, so that stays true): a repeat call scans only the ids added since, which keeps the 'symbol without data' case cheap."""
    base = id_at_or_before(conn, cutoff_iso, table); top = base + 200       # headroom: rows stamped a hair after their neighbours' ids are still filtered exactly by `where`
    key = _neg_key(table, where, params, order); neg = _neg_get(key); cap = WINDOWS[-1]
    floor = neg["to"] if (neg and base >= neg["to"] and neg["from"] <= max(0, top - cap)) else 0   # ids up to `to` (rows that EXISTED when the miss was recorded) are known to hold no match
    if floor >= base and floor > 0: return None
    for w in WINDOWS:
        lo = max(floor, top - w)
        r = conn.execute(f"SELECT {select} FROM {table} NOT INDEXED WHERE id>? AND id<=? AND {where} ORDER BY {order} LIMIT 1", (lo, top, *params)).fetchone()
        if r is not None: return r
        if lo <= floor: break                                                # the window already reaches the known-empty part (or the first row)
    _neg_put(key, max(0, top - cap), base)                                   # nothing in (top - cap, base]: remember it (base = the highest id that existed, not the headroom)
    return None


def floor_id(conn, start_epoch: float, *, cache: dict | None = None, key=None, table: str = "quote_snapshots") -> int:
    """An id F such that EVERY row with received_ts >= start has an id greater than F (0 = no information), so `AND id > F` can be added to a time-window query without dropping a wanted row.
    Why it holds: a row's id follows its WRITE order and received_ts <= write time <= received_ts + MAX_LAG_SEC. A row with received_ts <= start - MAX_LAG_SEC was therefore written before `start`, and every
    wanted row (received_ts >= start) was written after `start`, so it has a larger id. Any such row will do, so the binary search on a not-quite-monotone column is safe (it only needs to return one).
    The answer is cached per 5-minute bucket of `start` (conservative: the bucket start) so a polling screen pays for the search once per bucket. Relies on MAX_LAG_SEC bounding the real lag."""
    bucket = int(start_epoch // 300) * 300; ck = (key, table, bucket)
    if cache is not None and ck in cache: return cache[ck]
    f = id_at_or_before(conn, datetime.fromtimestamp(bucket - MAX_LAG_SEC, tz=timezone.utc).isoformat(), table)
    if cache is not None:
        if len(cache) > 256: cache.clear()
        cache[ck] = f
    return f


def ceil_id(conn, end_epoch: float, *, cache: dict | None = None, key=None, table: str = "quote_snapshots", column: str = "received_ts"):
    """An id C such that EVERY row with received_ts <= end has an id <= C, or None when no row is stamped later than end + MAX_LAG_SEC (then no upper bound is needed: live queries).
    Why it holds: a wanted row (received_ts <= end) was written no later than end + MAX_LAG_SEC; a row stamped AFTER end + MAX_LAG_SEC was written after that, so its id is larger. Any such row will do;
    the binary search keeps the smallest one it probed. Cached per 5-minute bucket (rounded UP, conservative)."""
    bucket = (int(end_epoch // 300) + 1) * 300; ck = (key, table, "ceil", bucket)
    if cache is not None and ck in cache: return cache[ck]
    keyts = _julian_str(datetime.fromtimestamp(bucket + MAX_LAG_SEC, tz=timezone.utc).isoformat())
    mx = conn.execute(f"SELECT MAX(id) FROM {table}").fetchone()[0] or 0; lo, hi, best = 0, mx, None
    while lo <= hi:
        mid = (lo + hi) // 2
        r = conn.execute(f"SELECT id, {column} FROM {table} WHERE id>=? ORDER BY id LIMIT 1", (mid,)).fetchone()
        if r is None: hi = mid - 1; continue
        rid, ts = r
        if ts is not None and str(ts) > keyts: best = rid if best is None else min(best, rid); hi = mid - 1
        else: lo = rid + 1
    if cache is not None:
        if len(cache) > 256: cache.clear()
        cache[ck] = best
    return best


def db_key(conn) -> str:
    """Cache key for a connection: the database file (connections are short-lived, the file is not)."""
    try: return next((r[2] for r in conn.execute("PRAGMA database_list") if r[1] == "main"), "") or ""
    except Exception: return ""
