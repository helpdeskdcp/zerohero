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
_SCHEMA = f"""CREATE TABLE IF NOT EXISTS hist_option_bars (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  {', '.join(c + ' TEXT' if c in ('timestamp','timeframe','exchange','underlying','expiry','option_type','security_id','oi_change_source','iv_source','greek_source','greek_model','greek_inputs','greek_calc_ts','source') else c + ' REAL' for c in COLS)},
  UNIQUE(underlying, option_type, strike, timestamp, timeframe, source)
);
CREATE INDEX IF NOT EXISTS ix_hob_lookup ON hist_option_bars(underlying, option_type, timestamp);"""


class HistOptionStore:
    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("CHANAKYA_HIST_OPTIONS_DB", _DEFAULT)
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with self._c() as c:
            c.executescript(_SCHEMA)

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
