"""
Read-only runtime diagnostics for the merged dashboard process.

Exposed at GET /api/diag. No side effects, never raises to the caller — every
probe is wrapped and reports its own error string. Added for PHASE 0 of the
data-integrity work: after the --workers 1 rollback, prove there is exactly one
feed/leader process, the feed is live, and each pipeline stage is still writing.
"""
from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timezone

from . import db

try:
    from .histcap.store import DB_PATH as _HIST_DB
except Exception:                                   # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "data", "market_history.db")

_LEASE_KEYS = ("runner_lease", "autoscalp_lease", "histcap_lease")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _age_sec(iso_or_epoch) -> float | None:
    if iso_or_epoch in (None, ""):
        return None
    try:
        if isinstance(iso_or_epoch, (int, float)):
            return round(time.time() - float(iso_or_epoch), 1)
        s = str(iso_or_epoch).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - dt).total_seconds(), 1)
    except Exception:
        return None


def _worker_count() -> dict:
    """Best-effort: read --workers from the uvicorn parent cmdline, and count
    live processes bound to the same port for a cross-check."""
    out = {"configured": 1, "source": "default", "uvicorn_procs": None}

    def _parse(pid):
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            args = [p.decode("utf-8", "replace") for p in fh.read().split(b"\x00") if p]
        if "uvicorn" not in " ".join(args):
            return None
        if "--workers" in args:
            return int(args[args.index("--workers") + 1])
        for a in args:
            if a.startswith("--workers="):
                return int(a.split("=")[1])
        return 1                                       # uvicorn default

    # self first (with --workers 1 uvicorn does not fork), then walk up the ppid chain
    try:
        pid = os.getpid()
        for _ in range(4):
            v = _parse(pid)
            if v is not None:
                out["configured"], out["source"] = v, "cmdline"
                break
            with open(f"/proc/{pid}/stat") as fh:
                pid = int(fh.read().split()[3])
            if pid <= 1:
                break
    except Exception as e:
        out["source"] = f"unreadable:{type(e).__name__}"

    # cross-check: how many live processes actually have uvicorn app.main in argv
    try:
        n = 0
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/cmdline", "rb") as fh:
                    cl = fh.read().decode("utf-8", "replace")
                if "uvicorn" in cl and "app.main" in cl:
                    n += 1
            except Exception:
                pass
        out["uvicorn_procs"] = n or None
    except Exception:
        pass
    return out


def _leader_state(my_owner: str | None) -> dict:
    rows = {}
    try:
        conn = db.get_conn()
        for k in _LEASE_KEYS:
            r = conn.execute("SELECT value FROM app_settings WHERE key=?", (k,)).fetchone()
            if not r:
                rows[k] = {"owner": None, "held_by_me": False, "hb_age_sec": None}
                continue
            import json as _j
            d = _j.loads(r["value"]) if r["value"] else {}
            owner = d.get("owner")
            rows[k] = {"owner": owner,
                       "held_by_me": bool(my_owner and owner == my_owner),
                       "hb_age_sec": _age_sec(d.get("hb"))}
    except Exception as e:
        rows["_error"] = f"{type(e).__name__}: {e}"
    owners = {v.get("owner") for v in rows.values() if isinstance(v, dict) and v.get("owner")}
    rows["_single_owner"] = (len(owners) == 1)
    rows["_distinct_owners"] = sorted(o for o in owners if o)
    return rows


def _feed_state(scalp_runner) -> dict:
    try:
        fs = scalp_runner.feed.status() if scalp_runner and scalp_runner.feed else {}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}
    marks = fs.get("marks") or {}
    fresh = sum(1 for m in marks.values() if isinstance(m, dict) and m.get("fresh"))
    return {
        "connected": fs.get("connected"),
        "last_error": fs.get("last_error"),
        "last_msg_age_sec": fs.get("last_msg_age_sec"),
        "n_desired": len(fs.get("desired_tokens") or []),
        "n_active": len(fs.get("active_tokens") or []),
        "marks_total": len(marks),
        "marks_fresh": fresh,
        "marks_stale": len(marks) - fresh,
    }


def _last_ts(dbpath: str, sql: str) -> dict:
    try:
        conn = sqlite3.connect(f"file:{dbpath}?mode=ro", uri=True, timeout=5)
        try:
            r = conn.execute(sql).fetchone()
        finally:
            conn.close()
        val = r[0] if r else None
        return {"ts": val, "age_sec": _age_sec(val)}
    except Exception as e:
        return {"ts": None, "age_sec": None, "error": f"{type(e).__name__}: {e}"}


def runtime_diag(scalp_runner=None, autoscalp=None, histcap_worker=None) -> dict:
    my_owner = None
    for obj, attr in ((autoscalp, "_owner"), (autoscalp, "owner"),
                      (scalp_runner, "_owner"), (histcap_worker, "_owner")):
        try:
            v = getattr(obj, attr, None)
            if v and ":" in str(v):
                my_owner = str(v)
                break
        except Exception:
            pass
    if not my_owner:
        my_owner = f"{os.uname().nodename}:{os.getpid()}"

    trade_db = getattr(getattr(db, "DB_PATH", None), "__str__", lambda: None)() or \
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "chanakya.db")

    leader = _leader_state(my_owner)
    feed = _feed_state(scalp_runner)

    diag = {
        "generated_at": _now(),
        "pid": os.getpid(),
        "owner": my_owner,
        "worker_count": _worker_count(),
        "leader_state": leader,
        "feed_state": feed,
        "last_tick_time": {
            "ts": (scalp_runner.feed.last_msg_ts if scalp_runner and scalp_runner.feed
                   and getattr(scalp_runner.feed, "last_msg_ts", None) else None),
        },
        "last_snapshot_time": _last_ts(trade_db, "SELECT MAX(ts) FROM live_market_snapshots"),
        "last_candle_time": _last_ts(_HIST_DB, "SELECT MAX(received_ts) FROM market_candles"),
        "last_persist_time": _last_ts(_HIST_DB, "SELECT MAX(received_ts) FROM quote_snapshots"),
        "histcap": {},
    }
    # last_tick age
    lt = diag["last_tick_time"].get("ts")
    diag["last_tick_time"]["age_sec"] = _age_sec(lt)

    try:
        if histcap_worker is not None:
            hs = histcap_worker.status()
            diag["histcap"] = {
                "running": hs.get("running"), "is_leader": hs.get("is_leader"),
                "lease_owner": hs.get("lease_owner"), "last_error": hs.get("last_error"),
                "last_run": (hs.get("last_run") or {}).get("run_id"),
            }
    except Exception as e:
        diag["histcap"] = {"error": f"{type(e).__name__}: {e}"}

    # PHASE 1 — broker greeks capability per underlying (AVAILABLE/UNAVAILABLE/UNKNOWN)
    try:
        from .connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=False)
        diag["greeks_capability"] = sdk.greek_capabilities() if sdk else {}
    except Exception as e:
        diag["greeks_capability"] = {"error": f"{type(e).__name__}: {e}"}

    # top-level health rollup
    diag["ok"] = bool(
        diag["worker_count"]["configured"] == 1
        and leader.get("_single_owner")
        and feed.get("connected") is True
        and (diag["last_snapshot_time"].get("age_sec") or 1e9) < 900
    )
    diag["warnings"] = []
    if diag["worker_count"]["configured"] != 1:
        diag["warnings"].append("worker_count != 1 (feed/leader split risk — see PHASE 0)")
    if not leader.get("_single_owner"):
        diag["warnings"].append(f"leases split across processes: {leader.get('_distinct_owners')}")
    if feed.get("connected") is not True:
        diag["warnings"].append("feed not connected")
    for k in ("last_snapshot_time", "last_candle_time", "last_persist_time"):
        a = diag[k].get("age_sec")
        if a is not None and a > 900:
            diag["warnings"].append(f"{k} stale ({a}s)")
    return diag
