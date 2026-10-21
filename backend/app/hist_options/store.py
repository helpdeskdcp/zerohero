"""Append-only store for normalized historical option rows. Own SQLite file
(data/hist_options.db) so it never contends with market_history.db's writers."""
from __future__ import annotations

import json
import os
import sqlite3

_DEFAULT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        "data", "hist_options.db")
COLS = ("timestamp", "timeframe", "exchange", "underlying", "expiry", "strike", "option_type",
        "security_id", "spot", "option_ltp", "bid", "ask", "bid_qty", "ask_qty", "volume", "oi",
        "previous_oi", "oi_change", "oi_change_source", "iv", "iv_source", "delta", "gamma", "theta",
        "vega", "greek_source", "greek_model", "greek_inputs", "greek_calc_ts", "vwap", "open", "high",
        "low", "close", "source")
_TABLE = f"""CREATE TABLE IF NOT EXISTS hist_option_bars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  {', '.join(c + ' TEXT' if c in ('timestamp','timeframe','exchange','underlying','expiry','option_type','security_id','oi_change_source','iv_source','greek_source','greek_model','greek_inputs','greek_calc_ts','source') else c + ' REAL' for c in COLS)}
)"""
_LEGACY_KEY = ("underlying", "option_type", "strike", "timestamp", "timeframe", "source")


def _init_schema(c):
    # SQLite cannot drop a table-level UNIQUE constraint. Rebuild only v1,
    # under one write transaction; errors roll back the entire migration.
    c.execute("BEGIN IMMEDIATE")
    c.execute(_TABLE)
    legacy = any(tuple(r[2] for r in c.execute(f'PRAGMA index_info("{idx[1]}")')) == _LEGACY_KEY
                 for idx in c.execute("PRAGMA index_list(hist_option_bars)") if idx[2])
    if legacy:
        if {r[1] for r in c.execute("PRAGMA table_info(hist_option_bars)")} != {"id", *COLS}:
            raise RuntimeError("Unrecognized historical option schema; migration requires review")
        extras = [r[0] for r in c.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='hist_option_bars' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL")]
        seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name='hist_option_bars'").fetchone()
        c.execute(_TABLE.replace("hist_option_bars", "hist_option_bars_expiry_migration", 1))
        cols = "id," + ",".join(COLS)
        c.execute(f"INSERT INTO hist_option_bars_expiry_migration ({cols}) SELECT {cols} FROM hist_option_bars")
        c.execute("DROP TABLE hist_option_bars")
        c.execute("ALTER TABLE hist_option_bars_expiry_migration RENAME TO hist_option_bars")
        if seq:
            c.execute("UPDATE sqlite_sequence SET seq=MAX(seq, ?) WHERE name='hist_option_bars'", (seq[0],))
        for sql in extras:
            c.execute(sql)
    # NULL expiry means unresolved, not an unlimited number of duplicate rows.
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_hob_contract ON hist_option_bars "
              "(underlying, COALESCE(expiry,''), option_type, strike, timestamp, timeframe, source)")
    c.execute("CREATE INDEX IF NOT EXISTS ix_hob_lookup ON hist_option_bars(underlying, option_type, timestamp)")


class HistOptionStore:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("CHANAKYA_HIST_OPTIONS_DB", _DEFAULT)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._c() as c:
            _init_schema(c)

    def _c(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        return c

    def write(self, rows: list[dict]) -> int:
        n = 0
        with self._c() as c:
            for r in rows:
                vals = [json.dumps(r[k]) if k == "greek_inputs" else r.get(k) for k in COLS]
                cur = c.execute(f"INSERT OR IGNORE INTO hist_option_bars ({','.join(COLS)}) "
                                f"VALUES ({','.join('?' * len(COLS))})", vals)
                n += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return n

    def read(self, underlying: str, option_type: str | None = None, since: str | None = None,
             until: str | None = None, limit: int = 100000) -> list[dict]:
        q, p = "SELECT * FROM hist_option_bars WHERE underlying=?", [underlying.upper()]
        if option_type:
            q += " AND option_type=?"; p.append(option_type)
        if since:
            q += " AND timestamp>=?"; p.append(since)
        if until:
            q += " AND timestamp<?"; p.append(until)
        q += " ORDER BY timestamp LIMIT ?"; p.append(limit)
        with self._c() as c:
            out = [dict(r) for r in c.execute(q, p)]
        for r in out:
            if r.get("greek_inputs"):
                r["greek_inputs"] = json.loads(r["greek_inputs"])
        return out
