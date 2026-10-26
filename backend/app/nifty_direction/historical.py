"""HISTORICAL adapter over the captured 1-minute stock/index bars (data/historical/stocks_1m/stocks_1m.db). Time-travel by `as_of`: nothing after as_of is visible,
bars are returned complete-only, so replay cannot look ahead. Labelled HISTORICAL; it has no futures / option-chain capability."""
from __future__ import annotations

import os
import sqlite3

from .adapters import Quote, CAP_QUOTES, CAP_CANDLES, CAP_FUTURES, CAP_CHAIN
from .indicators import IST, session_day

DB = os.environ.get("NIFTY_DIR_HIST_DB") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "historical", "stocks_1m", "stocks_1m.db"))
_SEC = {"1m": 60, "5m": 300, "15m": 900}


class HistoricalAdapter:
    name = "historical"; data_kind = "HISTORICAL"

    def __init__(self, as_of: float, path: str | None = None):
        self.as_of = as_of; self.path = path or DB; self._c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=15)

    # capture cadence measured on 2026-10-23: futures ~160 s, option-greek snapshots ~960 s -> limits ~2.6x / ~1.9x (env-overridable); the live limit stays 60 s
    deriv_max_age = {"futures": float(os.environ.get("NIFTY_DIR_CAPTURED_FUT_MAX_AGE_SEC", "420")), "chain": float(os.environ.get("NIFTY_DIR_CAPTURED_CHAIN_MAX_AGE_SEC", "1800"))}

    def capabilities(self):
        caps = {CAP_QUOTES, CAP_CANDLES}
        try:
            from .captured_derivatives import DB as _MH
            if os.path.exists(_MH): caps |= {CAP_FUTURES, CAP_CHAIN}
        except Exception: pass
        return caps

    def _deriv(self):
        from .captured_derivatives import CapturedDerivatives
        return CapturedDerivatives(self.as_of)

    def futures(self):
        d = self._deriv()
        try: return d.futures()
        finally: d.close()

    def option_chain(self):
        d = self._deriv()
        try: return d.option_chain()
        finally: d.close()

    def _one(self, sym, lo, hi):
        return self._c.execute("SELECT ts,o,h,l,c,v FROM bars WHERE sym=? AND ts>=? AND ts<? ORDER BY ts", (sym, lo, hi)).fetchall()

    def candles(self, symbol, interval, count):
        sec = _SEC[interval]; hi = int(self.as_of // sec * sec)                       # only buckets that have fully closed by as_of
        rows = self._one(symbol, hi - sec * (count + 2) - 86400 * 5 if interval != "1m" else hi - 60 * count - 86400, hi)
        out: dict = {}
        for ts, o, h, l, c, v in rows:
            if ts >= hi: continue
            b = ts // sec * sec; d = out.get(b)
            if d is None: out[b] = {"t": b, "o": o, "h": h, "l": l, "c": c, "v": v or 0}
            else: d["h"] = max(d["h"], h); d["l"] = min(d["l"], l); d["c"] = c; d["v"] += v or 0
        ordered = [out[k] for k in sorted(out)]
        return ordered[-count:]

    def quotes(self, symbols):
        res = {}
        for s in symbols:
            r = self._c.execute("SELECT ts,c,v FROM bars WHERE sym=? AND ts<? ORDER BY ts DESC LIMIT 1", (s, int(self.as_of // 60 * 60))).fetchone()
            if not r: continue
            day = session_day(r[0]); start = int(__import__("datetime").datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=IST).timestamp())
            p = self._c.execute("SELECT c FROM bars WHERE sym=? AND ts<? ORDER BY ts DESC LIMIT 1", (s, start)).fetchone()
            res[s] = Quote(s, r[1], p[0] if p else None, r[0] + 60, r[2], "historical")
        return res

    def close(self): self._c.close()

    def health(self): return {"adapter": "historical", "ok": os.path.exists(self.path), "as_of": self.as_of}
