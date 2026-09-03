"""
Strict-causal historical context builder for the Smart Index Scalper replay
(slice 5/6, spec sections 26 & 27).

Reconstructs a `market_context()`-shaped dict for one instrument AS OF a past
UTC timestamp, using ONLY rows already visible at that moment:

  - `market_candles`  — index / future OHLCV, a bar counts only once it has
                        CLOSED (bar_start + timeframe <= as_of)
  - `quote_snapshots` — per-strike option LTP / OI / volume; the latest snapshot
                        with received_ts <= as_of wins. ΔOI is DERIVED by
                        differencing against the snapshot ~5 min earlier
                        (AngelOne never stored oi_change).

Nothing is fabricated. Greeks were never captured historically, so every
greeks_* field is left None with `*_greeks_source = "UNAVAILABLE"` — the replay
does NOT compute a Black-Scholes derivation on the fly (that would be a model,
not data, and the engine treats greeks as optional).

`SessionData` loads a whole (symbol, session, expiry) once; `.context_at(T)`
then slices it in memory with bisect, so a full-session walk is O(steps · log n)
rather than a fresh O(n) DB scan per step.

This module never touches the broker and never imports the live
`mathematical_confluence.context` — it is a pure read over the capture DB.
"""
from __future__ import annotations

import bisect
import sqlite3
from datetime import datetime, timedelta, timezone

try:
    from ..histcap.store import DB_PATH as _HIST_DB
except Exception:                                            # pragma: no cover
    import os
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "market_history.db")

_MKT = {"SENSEX": "BSE", "BANKEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX"}
_TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}
_SPOT_KIND_PREF = ("INDEX", "FUTURE")
_DOI_LOOKBACK_SEC = 300
SOURCE = "HISTORICAL_REPLAY:market_history.db"


def _ro(path=None):
    c = sqlite3.connect(f"file:{path or _HIST_DB}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _dt(s) -> datetime:
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def _epoch(s) -> float:
    return _dt(s).timestamp()


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _ist_date(t: datetime) -> str:
    return _dt(t).astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")


# --------------------------------------------------------------------- sessions
def available_sessions(symbols=None) -> list[dict]:
    """Every (symbol, session_date_ist) that has BOTH intraday candles and an
    option chain with real OI — i.e. a session the replay can actually run."""
    want = None
    if symbols:
        want = {s.strip().upper() for s in (symbols.split(",") if isinstance(symbols, str) else symbols)}
    out = []
    with _ro() as c:
        cand = {(r["symbol"], r["day"]): dict(r) for r in c.execute(
            "SELECT symbol, session_date_ist AS day, MIN(bar_start) t0, MAX(bar_start) t1, "
            "COUNT(*) bars FROM market_candles WHERE kind IN ('INDEX','FUTURE') "
            "GROUP BY symbol, session_date_ist")}
        chains = {(r["symbol"], r["day"], r["expiry"]): dict(r) for r in c.execute(
            "SELECT symbol, session_date_ist AS day, expiry, COUNT(*) snaps, "
            "COUNT(DISTINCT strike) strikes, MIN(received_ts) q0, MAX(received_ts) q1 "
            "FROM quote_snapshots WHERE kind='OPTION' AND oi IS NOT NULL "
            "GROUP BY symbol, session_date_ist, expiry")}
    for (sym, day, expiry), ch in chains.items():
        if want and sym not in want:
            continue
        cd = cand.get((sym, day))
        if not cd or ch["strikes"] < 5:
            continue
        out.append({
            "symbol": sym, "session_date": day, "expiry": expiry,
            "candle_start": cd["t0"], "candle_end": cd["t1"], "candle_bars": cd["bars"],
            "chain_snaps": ch["snaps"], "chain_strikes": ch["strikes"],
            "chain_first": ch["q0"], "chain_last": ch["q1"],
        })
    return sorted(out, key=lambda r: (r["symbol"], r["session_date"], r["expiry"]))


# --------------------------------------------------------------------- loader
class SessionData:
    """Whole-session capture, loaded once, sliced causally in memory."""

    def __init__(self, symbol: str, session_date: str, expiry: str | None):
        self.symbol = symbol.upper()
        self.session_date = session_date
        self.market = _MKT.get(self.symbol, "NSE")
        self.prev_day: dict = {}
        self.spot_kind: str | None = None
        self.today_open = None
        self._b: dict[str, tuple[list[float], list[dict]]] = {}   # tf -> (close_epochs, rows)
        self._q: dict[tuple, tuple[list[float], list[tuple]]] = {}  # (strike,side) -> (epochs, (ltp,oi,vol))
        self.strikes: list[float] = []
        self.expiry = expiry
        self._load()

    def _load(self):
        with _ro() as c:
            if self.expiry is None:
                er = c.execute(
                    "SELECT expiry FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
                    "AND session_date_ist=? AND oi IS NOT NULL GROUP BY expiry "
                    "ORDER BY expiry LIMIT 1", (self.symbol, self.session_date)).fetchone()
                self.expiry = er["expiry"] if er else None

            for kind in _SPOT_KIND_PREF:
                if c.execute("SELECT 1 FROM market_candles WHERE symbol=? AND kind=? "
                             "AND session_date_ist=? LIMIT 1",
                             (self.symbol, kind, self.session_date)).fetchone():
                    self.spot_kind = kind
                    break

            if self.spot_kind:
                # previous captured session (aggregate OHLC)
                pr = c.execute(
                    "SELECT session_date_ist d, MAX(h) hi, MIN(l) lo FROM market_candles "
                    "WHERE symbol=? AND kind=? AND session_date_ist < ? GROUP BY session_date_ist "
                    "ORDER BY session_date_ist DESC LIMIT 1",
                    (self.symbol, self.spot_kind, self.session_date)).fetchone()
                if pr:
                    lastc = c.execute(
                        "SELECT c FROM market_candles WHERE symbol=? AND kind=? AND session_date_ist=? "
                        "ORDER BY bar_start DESC LIMIT 1",
                        (self.symbol, self.spot_kind, pr["d"])).fetchone()
                    self.prev_day = {"high": pr["hi"], "low": pr["lo"],
                                     "close": lastc["c"] if lastc else None,
                                     "source_kind": self.spot_kind, "date": pr["d"]}

                fo = c.execute(
                    "SELECT o FROM market_candles WHERE symbol=? AND kind=? AND session_date_ist=? "
                    "ORDER BY bar_start LIMIT 1", (self.symbol, self.spot_kind, self.session_date)).fetchone()
                self.today_open = fo["o"] if fo else None

                for tf in ("1m", "5m"):
                    rows = c.execute(
                        "SELECT bar_start, o, h, l, c, v FROM market_candles WHERE symbol=? "
                        "AND kind=? AND tf=? AND session_date_ist=? ORDER BY bar_start",
                        (self.symbol, self.spot_kind, tf, self.session_date)).fetchall()
                    recs = [{"bar_start": r["bar_start"], "open": r["o"], "high": r["h"],
                             "low": r["l"], "close": r["c"], "volume": r["v"]} for r in rows]
                    close_ep = [_epoch(r["bar_start"]) + _TF_SEC[tf] for r in recs]
                    self._b[tf] = (close_ep, recs)

            if self.expiry:
                qs = c.execute(
                    "SELECT received_ts, strike, option_type, ltp, oi, volume FROM quote_snapshots "
                    "WHERE symbol=? AND kind='OPTION' AND session_date_ist=? AND expiry=? "
                    "AND strike IS NOT NULL ORDER BY received_ts",
                    (self.symbol, self.session_date, self.expiry)).fetchall()
                grp: dict[tuple, list[tuple]] = {}
                for r in qs:
                    key = (float(r["strike"]), str(r["option_type"]).upper())
                    grp.setdefault(key, []).append((
                        _epoch(r["received_ts"]),
                        float(r["ltp"]) if r["ltp"] is not None else None,
                        float(r["oi"]) if r["oi"] is not None else None,
                        float(r["volume"]) if r["volume"] is not None else None))
                for key, lst in grp.items():
                    lst.sort(key=lambda x: x[0])
                    self._q[key] = ([x[0] for x in lst], [(x[1], x[2], x[3]) for x in lst])
                self.strikes = sorted({k[0] for k in self._q})

    # ------------------------------------------------------------- causal slice
    def span(self) -> tuple[datetime, datetime] | None:
        recs = (self._b.get("1m") or self._b.get("5m") or (None, None))[1]
        if not recs:
            return None
        return _dt(recs[0]["bar_start"]), _dt(recs[-1]["bar_start"])

    def _bars_upto(self, tf: str, as_of_ep: float) -> list[dict]:
        close_ep, recs = self._b.get(tf, ([], []))
        i = bisect.bisect_right(close_ep, as_of_ep)      # bars fully closed by as_of
        return recs[:i]

    def _leg_at(self, strike: float, side: str, as_of_ep: float) -> dict:
        eps, vals = self._q.get((strike, side), ([], []))
        i = bisect.bisect_right(eps, as_of_ep) - 1
        if i < 0:
            return {"ltp": None, "oi": None, "volume": None, "oi_change": None, "status": "MISSING"}
        ltp, oi, vol = vals[i]
        j = bisect.bisect_right(eps, as_of_ep - _DOI_LOOKBACK_SEC) - 1
        base = vals[j][1] if j >= 0 else None
        return {"ltp": ltp, "oi": oi, "volume": vol,
                "oi_change": (oi - base) if (oi is not None and base is not None) else None,
                "status": "AVAILABLE" if oi is not None else "MISSING"}

    def context_at(self, as_of) -> dict:
        t = _dt(as_of)
        ep = t.timestamp()
        ctx: dict = {"instrument": self.symbol, "market": self.market, "as_of": _iso(t),
                     "session_date": self.session_date, "expiry": self.expiry,
                     "source": SOURCE, "data_quality": {}}
        ctx["prev_day"] = {k: self.prev_day[k] for k in ("high", "low", "close")
                           if k in self.prev_day} if self.prev_day else {}
        ctx["data_quality"]["prev_day_ohlc"] = (
            f"ACTUAL (aggregated {self.prev_day.get('source_kind')} {self.prev_day.get('date')})"
            if self.prev_day else "MISSING (no prior captured session)")

        b5 = self._bars_upto("5m", ep)
        b1 = self._bars_upto("1m", ep)
        ctx["bars"] = b5
        ctx["today_open"] = self.today_open
        ctx["data_quality"]["intraday_bars"] = (
            f"ACTUAL ({self.spot_kind} 5m x{len(b5)})" if b5 else "MISSING")
        fine = b1 or b5
        if fine:
            ctx["spot"] = fine[-1]["close"]
            ctx["day_high"] = max(x["high"] for x in fine)
            ctx["day_low"] = min(x["low"] for x in fine)
            closes = [x["close"] for x in b5] or [x["close"] for x in fine]
            if len(closes) >= 4:
                ctx["mom_3m"] = round(closes[-1] - closes[-4], 4)
            vols = [x["volume"] for x in b5 if x.get("volume")]
            if len(vols) >= 6:
                ctx["current_volume"] = vols[-1]
                ctx["avg_volume"] = sum(vols[-20:-1]) / max(1, len(vols[-20:-1]))

        chain = []
        for k in self.strikes:
            ce = self._leg_at(k, "CE", ep)
            pe = self._leg_at(k, "PE", ep)
            if ce["ltp"] is None and pe["ltp"] is None and ce["oi"] is None and pe["oi"] is None:
                continue
            row = {"strike": k, "expiry": self.expiry}
            for pfx, leg in (("ce", ce), ("pe", pe)):
                row[f"{pfx}_ltp"] = leg["ltp"]
                row[f"{pfx}_oi"] = leg["oi"]
                row[f"{pfx}_oi_change"] = leg["oi_change"]
                row[f"{pfx}_volume"] = leg["volume"]
                row[f"{pfx}_oi_status"] = leg["status"]
                for g in ("delta", "gamma", "theta", "vega", "iv"):
                    row[f"{pfx}_{g}"] = None
                row[f"{pfx}_greeks_source"] = "UNAVAILABLE"
            chain.append(row)
        ctx["chain"] = chain
        ctx["data_quality"]["option_chain"] = f"ACTUAL (x{len(chain)} strikes)" if chain else "MISSING"
        ctx["data_quality"]["greeks"] = "UNAVAILABLE (never captured historically; not derived in replay)"
        if chain and ctx.get("spot"):
            ctx["atm"] = min((r["strike"] for r in chain), key=lambda s: abs(s - ctx["spot"]))
        return ctx


# --------------------------------------------------------------- standalone
def build_context(symbol: str, as_of, *, expiry: str | None = None,
                  session_date: str | None = None) -> dict:
    """One-shot: load the session then slice at `as_of`. For a full-session walk
    use `SessionData(...).context_at(T)` instead so the load happens once."""
    sess = session_date or _ist_date(as_of)
    return SessionData(symbol, sess, expiry).context_at(as_of)
