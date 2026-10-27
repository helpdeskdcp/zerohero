"""Index-friendly 'newest row at or before a time' lookups for the 15 GB append-only capture tables.

Why: `... WHERE symbol=? AND kind='OPTION' AND julianday(received_ts)<=julianday(?) ORDER BY julianday(received_ts) DESC LIMIT 1` cannot use any index on the time column, so SQLite reads EVERY row of the symbol and
sorts them (260 s on 2026-10-27, run every 5 minutes by cron scans). Rows are only ever appended, so the integer primary key grows with time: a binary search on the PK finds the id where the cutoff falls
(25 point lookups), and the query then scans only a bounded id window below it, widening it only if nothing qualifies. The qualifying predicate is unchanged, so the answer is the same row (ids may be a few
microseconds out of time order between writer threads; that cannot move the chosen instant by more than that)."""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime, timezone

WINDOWS = (50_000, 400_000, 3_000_000)               # ids below the cutoff position, widening; nothing within ~3 M ids (about six days of capture) -> None (a chain that old is not a live chain)
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
