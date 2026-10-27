"""Smart Chart persistence: its own SQLite file (data/smart_chart.db). Never touches chanakya.db.

Ownership is enforced in SQL: every read/write of a user's object includes `owner = ?`, so a missing row and
someone else's row are indistinguishable to the caller (both -> None / False).
Schema evolves only by appending to MIGRATIONS (PRAGMA user_version tracks the applied count).
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time

from . import config

MIGRATIONS = [
    # 1: scripts, versions, diagnostics
    """
    CREATE TABLE scripts (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
      kind TEXT NOT NULL CHECK (kind IN ('indicator','strategy')), source TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1,
      status TEXT NOT NULL DEFAULT 'UNCOMPILED', inputs_json TEXT NOT NULL DEFAULT '{}', favorite INTEGER NOT NULL DEFAULT 0,
      deleted INTEGER NOT NULL DEFAULT 0, created_ts REAL NOT NULL, updated_ts REAL NOT NULL);
    CREATE INDEX idx_scripts_owner ON scripts(owner, deleted, updated_ts DESC);
    CREATE TABLE script_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, script_id TEXT NOT NULL, version INTEGER NOT NULL, source TEXT NOT NULL,
      created_ts REAL NOT NULL, UNIQUE(script_id, version));
    CREATE TABLE compile_diagnostics (
      id INTEGER PRIMARY KEY AUTOINCREMENT, script_id TEXT NOT NULL, version INTEGER NOT NULL, ok INTEGER NOT NULL,
      diagnostics_json TEXT NOT NULL, inputs_schema_json TEXT NOT NULL DEFAULT '[]', ts REAL NOT NULL);
    CREATE INDEX idx_diag_script ON compile_diagnostics(script_id, ts DESC);
    """,
    # 2: execution jobs (+ bounded indicator output summary), backtests
    """
    CREATE TABLE exec_jobs (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, script_id TEXT, kind TEXT NOT NULL, symbol TEXT, tf TEXT,
      status TEXT NOT NULL, started_ts REAL NOT NULL, finished_ts REAL, duration_ms INTEGER, error TEXT, result_json TEXT);
    CREATE INDEX idx_jobs_owner ON exec_jobs(owner, started_ts DESC);
    CREATE TABLE backtests (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, script_id TEXT NOT NULL, script_version INTEGER NOT NULL, symbol TEXT NOT NULL,
      tf TEXT NOT NULL, bars INTEGER NOT NULL, params_json TEXT NOT NULL, stats_json TEXT NOT NULL, trades_json TEXT NOT NULL,
      equity_json TEXT NOT NULL, data_meta_json TEXT NOT NULL, assumptions_json TEXT NOT NULL, created_ts REAL NOT NULL);
    CREATE INDEX idx_bt_owner ON backtests(owner, created_ts DESC);
    """,
    # 3: paper trading, preferences, audit
    """
    CREATE TABLE paper_portfolios (
      id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, script_id TEXT NOT NULL, script_version INTEGER NOT NULL,
      symbol TEXT NOT NULL, tf TEXT NOT NULL, capital REAL NOT NULL, commission_pct REAL NOT NULL, slippage_ticks REAL NOT NULL,
      daily_loss_limit REAL NOT NULL, status TEXT NOT NULL, start_ms INTEGER NOT NULL, last_bar_ms INTEGER,
      cash REAL NOT NULL, state_json TEXT NOT NULL DEFAULT '{}', halted_reason TEXT, created_ts REAL NOT NULL, updated_ts REAL NOT NULL);
    CREATE INDEX idx_paper_owner ON paper_portfolios(owner, status);
    CREATE TABLE paper_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT, portfolio_id TEXT NOT NULL, trade_key TEXT NOT NULL, direction TEXT NOT NULL,
      qty REAL NOT NULL, entry_ms INTEGER NOT NULL, entry_price REAL NOT NULL, exit_ms INTEGER, exit_price REAL,
      commission REAL NOT NULL DEFAULT 0, pnl REAL, status TEXT NOT NULL, created_ts REAL NOT NULL,
      UNIQUE(portfolio_id, trade_key));
    CREATE INDEX idx_ptrades_pf ON paper_trades(portfolio_id, entry_ms);
    CREATE TABLE user_prefs (owner TEXT NOT NULL, key TEXT NOT NULL, value_json TEXT NOT NULL, updated_ts REAL NOT NULL, PRIMARY KEY (owner, key));
    CREATE TABLE audit_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, owner TEXT NOT NULL, action TEXT NOT NULL,
      object_type TEXT, object_id TEXT, detail_json TEXT NOT NULL DEFAULT '{}');
    CREATE INDEX idx_audit_owner ON audit_events(owner, ts DESC);
    """,
]

_lock = threading.RLock()
_conn_state: dict = {"path": None, "conn": None}


def _connect() -> sqlite3.Connection:
    path = config.db_path()
    with _lock:
        if _conn_state["conn"] is not None and _conn_state["path"] == path:
            return _conn_state["conn"]
        if _conn_state["conn"] is not None:
            _conn_state["conn"].close()
        import os
        os.makedirs(os.path.dirname(path), exist_ok=True)
        c = sqlite3.connect(path, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        v = c.execute("PRAGMA user_version").fetchone()[0]
        for i in range(v, len(MIGRATIONS)):
            c.executescript(MIGRATIONS[i])
            c.execute(f"PRAGMA user_version={i + 1}")
            c.commit()
        _conn_state.update(path=path, conn=c)
        return c


def close() -> None:
    with _lock:
        if _conn_state["conn"] is not None:
            _conn_state["conn"].close()
        _conn_state.update(path=None, conn=None)


def schema_version() -> int:
    with _lock:
        return _connect().execute("PRAGMA user_version").fetchone()[0]


def _id() -> str:
    return secrets.token_hex(8)


def _q(sql: str, args: tuple = ()) -> list[sqlite3.Row]:
    with _lock:
        return _connect().execute(sql, args).fetchall()


def _x(sql: str, args: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        c = _connect()
        cur = c.execute(sql, args)
        c.commit()
        return cur


# ------------------------------------------------------------------------------------ audit
def audit(owner: str, action: str, object_type: str | None = None, object_id: str | None = None, detail: dict | None = None) -> None:
    _x("INSERT INTO audit_events(ts, owner, action, object_type, object_id, detail_json) VALUES (?,?,?,?,?,?)",
       (time.time(), owner, action, object_type, object_id, json.dumps(detail or {}, default=str)[:4000]))


def list_audit(owner: str, limit: int = 100, offset: int = 0) -> list[dict]:
    rows = _q("SELECT * FROM audit_events WHERE owner=? ORDER BY id DESC LIMIT ? OFFSET ?", (owner, limit, offset))
    return [{**dict(r), "detail": json.loads(r["detail_json"])} for r in rows]


# ------------------------------------------------------------------------------------ scripts
def _script(r: sqlite3.Row, with_source: bool = True) -> dict:
    d = dict(r)
    d["favorite"] = bool(d["favorite"])
    d["inputs"] = json.loads(d.pop("inputs_json") or "{}")
    d.pop("deleted", None)
    if not with_source:
        d.pop("source", None)
    return d


def count_scripts(owner: str) -> int:
    return _q("SELECT COUNT(*) c FROM scripts WHERE owner=? AND deleted=0", (owner,))[0]["c"]


def create_script(owner: str, name: str, description: str, kind: str, source: str, inputs: dict | None = None) -> dict:
    sid, now = _id(), time.time()
    with _lock:
        c = _connect()
        c.execute("INSERT INTO scripts(id,owner,name,description,kind,source,version,status,inputs_json,created_ts,updated_ts) VALUES (?,?,?,?,?,?,1,'UNCOMPILED',?,?,?)",
                  (sid, owner, name, description, kind, source, json.dumps(inputs or {}), now, now))
        c.execute("INSERT INTO script_versions(script_id,version,source,created_ts) VALUES (?,?,?,?)", (sid, 1, source, now))
        c.commit()
    audit(owner, "script.create", "script", sid, {"name": name, "kind": kind})
    return get_script(owner, sid)


def get_script(owner: str, sid: str) -> dict | None:
    r = _q("SELECT * FROM scripts WHERE id=? AND owner=? AND deleted=0", (sid, owner))
    return _script(r[0]) if r else None


def list_scripts(owner: str, kind: str | None = None, q: str | None = None, limit: int = 50, offset: int = 0) -> dict:
    where, args = "owner=? AND deleted=0", [owner]
    if kind:
        where += " AND kind=?"
        args.append(kind)
    if q:
        where += " AND (name LIKE ? OR description LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    total = _q(f"SELECT COUNT(*) c FROM scripts WHERE {where}", tuple(args))[0]["c"]
    rows = _q(f"SELECT * FROM scripts WHERE {where} ORDER BY favorite DESC, updated_ts DESC LIMIT ? OFFSET ?", (*args, limit, offset))
    return {"total": total, "limit": limit, "offset": offset, "items": [_script(r, with_source=False) for r in rows]}


def update_script(owner: str, sid: str, *, name=None, description=None, kind=None, source=None, inputs=None, favorite=None) -> dict | None:
    cur = get_script(owner, sid)
    if not cur:
        return None
    sets, args = [], []
    bump = source is not None and source != cur["source"]
    for col, val in (("name", name), ("description", description), ("kind", kind), ("source", source)):
        if val is not None:
            sets.append(f"{col}=?")
            args.append(val)
    if inputs is not None:
        sets.append("inputs_json=?")
        args.append(json.dumps(inputs))
    if favorite is not None:
        sets.append("favorite=?")
        args.append(1 if favorite else 0)
    now = time.time()
    if bump:
        sets += ["version=version+1", "status='UNCOMPILED'"]
    if not sets:
        return cur
    sets.append("updated_ts=?")
    args.append(now)
    with _lock:
        c = _connect()
        c.execute(f"UPDATE scripts SET {', '.join(sets)} WHERE id=? AND owner=? AND deleted=0", (*args, sid, owner))
        if bump:
            c.execute("INSERT INTO script_versions(script_id,version,source,created_ts) VALUES (?,?,?,?)", (sid, cur["version"] + 1, source, now))
            c.execute("DELETE FROM script_versions WHERE script_id=? AND version<=?", (sid, cur["version"] + 1 - config.MAX_VERSIONS_PER_SCRIPT))
        c.commit()
    audit(owner, "script.update", "script", sid, {"version_bump": bump})
    return get_script(owner, sid)


def delete_script(owner: str, sid: str) -> bool:
    cur = _x("UPDATE scripts SET deleted=1, updated_ts=? WHERE id=? AND owner=? AND deleted=0", (time.time(), sid, owner))
    if cur.rowcount:
        audit(owner, "script.delete", "script", sid)
    return bool(cur.rowcount)


def list_versions(owner: str, sid: str) -> list[dict] | None:
    if not get_script(owner, sid):
        return None
    return [dict(r) for r in _q("SELECT version, created_ts, length(source) AS bytes FROM script_versions WHERE script_id=? ORDER BY version DESC", (sid,))]


def get_version(owner: str, sid: str, version: int) -> dict | None:
    if not get_script(owner, sid):
        return None
    r = _q("SELECT version, source, created_ts FROM script_versions WHERE script_id=? AND version=?", (sid, version))
    return dict(r[0]) if r else None


def record_compile(owner: str, sid: str, version: int, ok: bool, diagnostics: list, inputs_schema: list) -> None:
    _x("INSERT INTO compile_diagnostics(script_id,version,ok,diagnostics_json,inputs_schema_json,ts) VALUES (?,?,?,?,?,?)",
       (sid, version, 1 if ok else 0, json.dumps(diagnostics), json.dumps(inputs_schema), time.time()))
    _x("DELETE FROM compile_diagnostics WHERE script_id=? AND id NOT IN (SELECT id FROM compile_diagnostics WHERE script_id=? ORDER BY id DESC LIMIT 20)", (sid, sid))
    _x("UPDATE scripts SET status=? WHERE id=? AND owner=? AND version=?", ("OK" if ok else "ERROR", sid, owner, version))


def last_compile(owner: str, sid: str) -> dict | None:
    if not get_script(owner, sid):
        return None
    r = _q("SELECT * FROM compile_diagnostics WHERE script_id=? ORDER BY id DESC LIMIT 1", (sid,))
    if not r:
        return None
    return {"version": r[0]["version"], "ok": bool(r[0]["ok"]), "diagnostics": json.loads(r[0]["diagnostics_json"]),
            "inputs": json.loads(r[0]["inputs_schema_json"]), "ts": r[0]["ts"]}


# ------------------------------------------------------------------------------------ jobs
def start_job(owner: str, script_id: str | None, kind: str, symbol: str | None, tf: str | None) -> str:
    jid = _id()
    _x("INSERT INTO exec_jobs(id,owner,script_id,kind,symbol,tf,status,started_ts) VALUES (?,?,?,?,?,?,'RUNNING',?)",
       (jid, owner, script_id, kind, symbol, tf, time.time()))
    _x("DELETE FROM exec_jobs WHERE owner=? AND id NOT IN (SELECT id FROM exec_jobs WHERE owner=? ORDER BY started_ts DESC LIMIT ?)",
       (owner, owner, config.EXEC_JOB_RETENTION))
    return jid


def finish_job(jid: str, status: str, duration_ms: int, error: str | None = None, result: dict | None = None) -> None:
    blob = json.dumps(result)[:200_000] if result is not None else None
    _x("UPDATE exec_jobs SET status=?, finished_ts=?, duration_ms=?, error=?, result_json=? WHERE id=?",
       (status, time.time(), duration_ms, (error or "")[:500] or None, blob, jid))


def list_jobs(owner: str, limit: int = 50, offset: int = 0) -> list[dict]:
    return [dict(r) for r in _q("SELECT id,script_id,kind,symbol,tf,status,started_ts,duration_ms,error FROM exec_jobs WHERE owner=? ORDER BY started_ts DESC LIMIT ? OFFSET ?", (owner, limit, offset))]


# ------------------------------------------------------------------------------------ backtests
def save_backtest(owner: str, script_id: str, script_version: int, symbol: str, tf: str, bars: int, params: dict,
                  stats: dict, trades: list, equity: list, data_meta: dict, assumptions: list) -> str:
    bid = _id()
    _x("INSERT INTO backtests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
       (bid, owner, script_id, script_version, symbol, tf, bars, json.dumps(params), json.dumps(stats), json.dumps(trades),
        json.dumps(equity), json.dumps(data_meta), json.dumps(assumptions), time.time()))
    _x("DELETE FROM backtests WHERE owner=? AND id NOT IN (SELECT id FROM backtests WHERE owner=? ORDER BY created_ts DESC LIMIT ?)",
       (owner, owner, config.BACKTEST_RETENTION))
    audit(owner, "backtest.run", "backtest", bid, {"script_id": script_id, "symbol": symbol, "tf": tf})
    return bid


def _bt(r: sqlite3.Row, full: bool) -> dict:
    d = {"id": r["id"], "script_id": r["script_id"], "script_version": r["script_version"], "symbol": r["symbol"], "tf": r["tf"],
         "bars": r["bars"], "params": json.loads(r["params_json"]), "stats": json.loads(r["stats_json"]), "created_ts": r["created_ts"]}
    if full:
        d.update(trades=json.loads(r["trades_json"]), equity=json.loads(r["equity_json"]), data_meta=json.loads(r["data_meta_json"]),
                 assumptions=json.loads(r["assumptions_json"]))
    return d


def get_backtest(owner: str, bid: str) -> dict | None:
    r = _q("SELECT * FROM backtests WHERE id=? AND owner=?", (bid, owner))
    return _bt(r[0], True) if r else None


def list_backtests(owner: str, script_id: str | None = None, limit: int = 30, offset: int = 0) -> list[dict]:
    if script_id:
        rows = _q("SELECT * FROM backtests WHERE owner=? AND script_id=? ORDER BY created_ts DESC LIMIT ? OFFSET ?", (owner, script_id, limit, offset))
    else:
        rows = _q("SELECT * FROM backtests WHERE owner=? ORDER BY created_ts DESC LIMIT ? OFFSET ?", (owner, limit, offset))
    return [_bt(r, False) for r in rows]


# ------------------------------------------------------------------------------------ paper trading
def _pf(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["state"] = json.loads(d.pop("state_json") or "{}")
    return d


def count_active_portfolios(owner: str) -> int:
    return _q("SELECT COUNT(*) c FROM paper_portfolios WHERE owner=? AND status!='DELETED'", (owner,))[0]["c"]


def create_portfolio(owner: str, name: str, script_id: str, script_version: int, symbol: str, tf: str, capital: float,
                     commission_pct: float, slippage_ticks: float, daily_loss_limit: float, start_ms: int) -> dict:
    pid, now = _id(), time.time()
    _x("INSERT INTO paper_portfolios(id,owner,name,script_id,script_version,symbol,tf,capital,commission_pct,slippage_ticks,daily_loss_limit,status,start_ms,cash,created_ts,updated_ts) "
       "VALUES (?,?,?,?,?,?,?,?,?,?,?,'STOPPED',?,?,?,?)",
       (pid, owner, name, script_id, script_version, symbol, tf, capital, commission_pct, slippage_ticks, daily_loss_limit, start_ms, capital, now, now))
    audit(owner, "paper.create", "paper_portfolio", pid, {"script_id": script_id, "symbol": symbol, "tf": tf, "capital": capital})
    return get_portfolio(owner, pid)


def get_portfolio(owner: str, pid: str) -> dict | None:
    r = _q("SELECT * FROM paper_portfolios WHERE id=? AND owner=? AND status!='DELETED'", (pid, owner))
    return _pf(r[0]) if r else None


def list_portfolios(owner: str) -> list[dict]:
    return [_pf(r) for r in _q("SELECT * FROM paper_portfolios WHERE owner=? AND status!='DELETED' ORDER BY created_ts DESC", (owner,))]


def update_portfolio(owner: str, pid: str, **fields) -> None:
    allowed = {"status", "last_bar_ms", "cash", "state", "halted_reason"}
    sets, args = [], []
    for k, v in fields.items():
        if k not in allowed:
            raise ValueError(k)
        if k == "state":
            sets.append("state_json=?")
            args.append(json.dumps(v))
        else:
            sets.append(f"{k}=?")
            args.append(v)
    sets.append("updated_ts=?")
    args.append(time.time())
    _x(f"UPDATE paper_portfolios SET {', '.join(sets)} WHERE id=? AND owner=?", (*args, pid, owner))


def delete_portfolio(owner: str, pid: str) -> bool:
    cur = _x("UPDATE paper_portfolios SET status='DELETED', updated_ts=? WHERE id=? AND owner=? AND status!='DELETED'", (time.time(), pid, owner))
    if cur.rowcount:
        audit(owner, "paper.delete", "paper_portfolio", pid)
    return bool(cur.rowcount)


def upsert_paper_trade(pid: str, trade_key: str, direction: str, qty: float, entry_ms: int, entry_price: float,
                       exit_ms: int | None, exit_price: float | None, commission: float, pnl: float | None, status: str) -> bool:
    """Idempotent on (portfolio_id, trade_key): the same simulated trade can never be recorded twice. Returns True if newly inserted."""
    with _lock:
        c = _connect()
        cur = c.execute("INSERT OR IGNORE INTO paper_trades(portfolio_id,trade_key,direction,qty,entry_ms,entry_price,exit_ms,exit_price,commission,pnl,status,created_ts) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (pid, trade_key, direction, qty, entry_ms, entry_price, exit_ms, exit_price, commission, pnl, status, time.time()))
        inserted = bool(cur.rowcount)
        if not inserted:
            c.execute("UPDATE paper_trades SET exit_ms=?, exit_price=?, commission=?, pnl=?, status=? WHERE portfolio_id=? AND trade_key=?",
                      (exit_ms, exit_price, commission, pnl, status, pid, trade_key))
        c.commit()
        return inserted


def list_paper_trades(pid: str, limit: int = 200, offset: int = 0) -> list[dict]:
    return [dict(r) for r in _q("SELECT * FROM paper_trades WHERE portfolio_id=? ORDER BY entry_ms DESC, id DESC LIMIT ? OFFSET ?", (pid, limit, offset))]


# ------------------------------------------------------------------------------------ preferences
def get_prefs(owner: str) -> dict:
    return {r["key"]: json.loads(r["value_json"]) for r in _q("SELECT key,value_json FROM user_prefs WHERE owner=?", (owner,))}


def set_pref(owner: str, key: str, value) -> None:
    _x("INSERT INTO user_prefs(owner,key,value_json,updated_ts) VALUES (?,?,?,?) ON CONFLICT(owner,key) DO UPDATE SET value_json=excluded.value_json, updated_ts=excluded.updated_ts",
       (owner, key, json.dumps(value)[:20_000], time.time()))
