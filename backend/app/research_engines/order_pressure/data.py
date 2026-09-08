"""
READ-ONLY data adapters for the order-pressure research engine.

Sources actually present in this repo (surveyed 2026-09-08):
  * Kaggle CSVs under data/historical/kaggle/  -- long SPOT/INDEX 1-minute history
    (NIFTY 2015-2025 & 2008-2020, BANK-NIFTY 1m to 2024). Cash index -> volume 0.
  * market_history.db market_candles (kind INDEX|FUTURE, 1m/3m/5m, ~2026-09-01..07)
  * market_history.db quote_snapshots (kind OPTION) -- poll snapshots with
    open/high/low/close, volume, oi, oi_change, bid/ask. Irregular cadence,
    ~5-10 clustered sessions per symbol. NOT clean candles.

NOT usable (reported by coverage_report, never invented):
  * Upstox expired-instruments historical candles -- payloads are empty.
  * per-1m option OHLC/OI -- does not exist; option data is poll-snapshot only.
"""
from __future__ import annotations

import csv
import os
import pickle
import sqlite3
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_MH_DB = os.path.join(_ROOT, "data", "market_history.db")
_KAGGLE = os.path.join(_ROOT, "data", "historical", "kaggle")
_CACHE_DIR = os.path.join(_ROOT, "data", "research", "order_pressure", "_cache")

_TF_SEC = {"1m": 60, "3m": 180, "5m": 300, "15m": 900}

# Kaggle SPOT files: symbol -> (path, datetime_col, has_header)
_KAGGLE_SPOT = {
    "NIFTY": [
        os.path.join(_KAGGLE, "mavrick136__10-years-of-nifty-50-1-minute-data-20152025",
                     "NIFTY_1MIN_2015-2025.csv"),
        os.path.join(_KAGGLE, "nishanthsalian__indian-stock-index-1minute-data-2008-2020",
                     "NIFTY_2008_2020.csv"),
    ],
    "BANKNIFTY": [
        os.path.join(_KAGGLE, "sandeepkapri__banknifty-data-upto-2024", "bank-nifty-1m-data.csv"),
    ],
}


def _kaggle_ticks_cached(path: str) -> list[dict]:
    """Parse a Kaggle OHLC csv into 1m ticks once, then pickle-cache keyed on the
    file's mtime+size. Read-only w.r.t. the source csv."""
    try:
        st = os.stat(path)
        os.makedirs(_CACHE_DIR, exist_ok=True)
        ck = os.path.join(_CACHE_DIR, f"{os.path.basename(path)}.{int(st.st_mtime)}.{st.st_size}.pkl")
        if os.path.exists(ck):
            with open(ck, "rb") as fh:
                return pickle.load(fh)
    except OSError:
        ck = None
    ticks: list[dict] = []
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        cols = {c.lower().strip(): c for c in (rd.fieldnames or [])}
        dtc = cols.get("datetime") or cols.get("date") or cols.get("timestamp") or cols.get("time")
        for row in rd:
            t = _epoch(row.get(dtc))
            if t is None:
                continue
            try:
                ticks.append({"t": t, "o": float(row[cols["open"]]), "h": float(row[cols["high"]]),
                              "l": float(row[cols["low"]]), "c": float(row[cols["close"]]),
                              "v": float(row.get(cols.get("volume", ""), 0) or 0)})
            except (ValueError, KeyError):
                continue
    if ck:
        try:
            with open(ck, "wb") as fh:
                pickle.dump(ticks, fh, protocol=4)
        except OSError:
            pass
    return ticks


def _ro_conn(path):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        return con
    except sqlite3.OperationalError:
        return None


def _epoch(ts) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts if ts < 1e12 else ts / 1000.0)
    s = str(ts).strip().replace("Z", "+00:00")
    for fmt in (None,):  # try fromisoformat first
        try:
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%m/%d/%Y %H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


def _resample(ticks: list[dict], tf: str) -> list[dict]:
    """ticks: [{t(epoch), o,h,l,c, v, oi, oi_change}] in time order (o may be None
    for pure snapshots -> use c). Returns closed bars for `tf`, oldest first."""
    step = _TF_SEC[tf]
    out: list[dict] = []
    cur_bucket = None
    b = None
    for tk in ticks:
        t = tk.get("t")
        if t is None:
            continue
        bk = int(t // step) * step
        px_o = tk.get("o") if tk.get("o") not in (None, 0) else tk.get("c")
        h = tk.get("h") if tk.get("h") not in (None, 0) else tk.get("c")
        l = tk.get("l") if tk.get("l") not in (None, 0) else tk.get("c")
        c = tk.get("c")
        if c is None:
            continue
        if bk != cur_bucket:
            if b is not None:
                out.append(b)
            cur_bucket = bk
            b = {"t": bk, "o": px_o if px_o is not None else c, "h": h if h is not None else c,
                 "l": l if l is not None else c, "c": c, "v": 0.0,
                 "oi": tk.get("oi"), "oi_first": tk.get("oi"), "n": 0}
        b["h"] = max(b["h"], h if h is not None else c)
        b["l"] = min(b["l"], l if l is not None else c)
        b["c"] = c
        b["oi"] = tk.get("oi") if tk.get("oi") is not None else b["oi"]
        b["n"] += 1
        vv = tk.get("v")
        if vv is not None:
            b["v"] = vv  # snapshot volume is cumulative-for-day; keep last, delta computed below
    if b is not None:
        out.append(b)
    # oi_change per bar = last oi - first oi in bar; volume delta = this.v - prev.v
    prev_v = None
    for i, bar in enumerate(out):
        oi0, oi1 = bar.pop("oi_first", None), bar.get("oi")
        bar["oi_change"] = (oi1 - oi0) if (oi0 is not None and oi1 is not None) else None
        if prev_v is not None and bar["v"] and bar["v"] >= prev_v:
            bar["v_delta"] = bar["v"] - prev_v
        else:
            bar["v_delta"] = bar["v"] or 0.0
        prev_v = bar["v"] if bar["v"] else prev_v
    return out


# --------------------------------------------------------------------------- SPOT
def load_spot_bars(symbol: str, tf: str, *, start: str | None = None, end: str | None = None,
                   max_bars: int | None = None, source: str = "auto") -> list[dict]:
    """Chronological SPOT/INDEX bars for `symbol` at `tf`. Prefers dense Kaggle
    1m history, resampled; falls back to market_candles (recent). No look-ahead:
    just an ordered read."""
    sym = symbol.upper()
    ticks: list[dict] = []
    used = None
    if source in ("auto", "kaggle") and sym in _KAGGLE_SPOT:
        for path in _KAGGLE_SPOT[sym]:
            if not os.path.exists(path):
                continue
            ticks += _kaggle_ticks_cached(path)
        used = "kaggle" if ticks else used
    if not ticks and source in ("auto", "market_candles"):
        con = _ro_conn(_MH_DB)
        if con:
            try:
                q = con.execute(
                    "SELECT bar_start, o, h, l, c, v FROM market_candles "
                    "WHERE symbol=? AND kind='INDEX' AND tf=? ORDER BY bar_start",
                    (sym, tf if tf in ("1m", "3m", "5m", "15m") else "1m"))
                for r in q:
                    t = _epoch(r["bar_start"])
                    if t is not None and r["c"] is not None:
                        ticks.append({"t": t, "o": r["o"], "h": r["h"], "l": r["l"],
                                      "c": r["c"], "v": r["v"] or 0.0})
                used = "market_candles"
            finally:
                con.close()
    ticks.sort(key=lambda x: x["t"])
    if start:
        s = _epoch(start) or 0
        ticks = [t for t in ticks if t["t"] >= s]
    if end:
        e = _epoch(end) or 9e18
        ticks = [t for t in ticks if t["t"] <= e]
    bars = _resample(ticks, tf) if used == "kaggle" or tf not in ("1m", "3m", "5m") else _resample(ticks, tf)
    for b in bars:
        b["source"] = used
        b["session_date"] = datetime.fromtimestamp(b["t"], tz=timezone.utc).astimezone(
            timezone(__import__("datetime").timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
    if max_bars:
        bars = bars[-max_bars:]
    return bars


# ------------------------------------------------------------------------- OPTION
def _atm_strike(con, symbol, session_date, side) -> float | None:
    row = con.execute(
        "SELECT strike, COUNT(*) n FROM quote_snapshots "
        "WHERE kind='OPTION' AND symbol=? AND session_date_ist=? AND option_type=? "
        "GROUP BY strike ORDER BY n DESC LIMIT 1", (symbol, session_date, side)).fetchone()
    return row["strike"] if row else None


def load_option_bars(symbol: str, side: str, tf: str, *, session_date: str | None = None,
                     strike: float | None = None) -> list[dict]:
    """Resample poll snapshots for one option leg (the most-polled strike of the
    session unless `strike` is given) into `tf` bars. Poll cadence is irregular:
    each bar carries `n` (snapshot count) so a caller can drop thin bars."""
    con = _ro_conn(_MH_DB)
    if not con:
        return []
    try:
        sessions = ([session_date] if session_date else
                    [r["session_date_ist"] for r in con.execute(
                        "SELECT DISTINCT session_date_ist FROM quote_snapshots "
                        "WHERE kind='OPTION' AND symbol=? AND option_type=? ORDER BY session_date_ist",
                        (symbol, side.upper()))])
        allbars: list[dict] = []
        for sd in sessions:
            k = strike if strike is not None else _atm_strike(con, symbol, sd, side.upper())
            if k is None:
                continue
            ticks = []
            for r in con.execute(
                "SELECT exch_ts, received_ts, open, high, low, close, ltp, volume, oi, oi_change "
                "FROM quote_snapshots WHERE kind='OPTION' AND symbol=? AND session_date_ist=? "
                "AND option_type=? AND strike=? ORDER BY COALESCE(exch_ts, received_ts)",
                    (symbol, sd, side.upper(), k)):
                t = _epoch(r["exch_ts"] or r["received_ts"])
                c = r["close"] if r["close"] not in (None, 0) else r["ltp"]
                if t is None or c is None:
                    continue
                ticks.append({"t": t, "o": r["open"], "h": r["high"], "l": r["low"], "c": c,
                              "v": r["volume"], "oi": r["oi"]})
            bars = _resample(ticks, tf)
            for b in bars:
                b["session_date"] = sd
                b["strike"] = k
                b["side"] = side.upper()
            allbars += bars
        return allbars
    finally:
        con.close()


# ---------------------------------------------------------------------- coverage
def coverage_report() -> dict:
    """What the engine can actually run on. Explicit about every gap."""
    rep: dict = {"generated_at": datetime.now(timezone.utc).isoformat(),
                 "spot": {}, "option": {}, "unavailable": []}

    for sym in ("NIFTY", "BANKNIFTY"):
        files = [p for p in _KAGGLE_SPOT.get(sym, []) if os.path.exists(p)]
        if files:
            b1 = load_spot_bars(sym, "1m", max_bars=5)
            ball = load_spot_bars(sym, "5m")
            rep["spot"][sym] = {
                "source": "kaggle", "files": [os.path.basename(f) for f in files],
                "tf_available": ["1m", "3m", "5m"],
                "n_5m_bars": len(ball),
                "range": [ball[0]["session_date"], ball[-1]["session_date"]] if ball else None,
                "volume": "0 (cash index -- no traded volume)",
            }
    con = _ro_conn(_MH_DB)
    if con:
        try:
            for r in con.execute(
                "SELECT symbol, kind, COUNT(*) n, COUNT(DISTINCT session_date_ist) days, "
                "MIN(session_date_ist) d0, MAX(session_date_ist) d1, "
                "SUM(oi IS NOT NULL) with_oi, SUM(volume IS NOT NULL) with_vol "
                "FROM quote_snapshots WHERE kind='OPTION' GROUP BY symbol"):
                rep["option"][r["symbol"]] = {
                    "source": "quote_snapshots (poll snapshots, NOT candles)",
                    "rows": r["n"], "sessions": r["days"], "range": [r["d0"], r["d1"]],
                    "rows_with_oi": r["with_oi"], "rows_with_volume": r["with_vol"],
                    "cadence": "irregular poll (~seconds-to-minutes apart); resample >=5m only",
                    "verdict": ("USABLE_DEMO" if r["days"] >= 4 else "INSUFFICIENT"),
                }
        finally:
            con.close()

    rep["unavailable"] = [
        "Per-1m option OHLC/OI candles -- do not exist. Option history is poll-snapshot only.",
        "Upstox expired-instruments historical candles -- stored responses have empty candle arrays.",
        "MCX (CRUDEOIL/NATURALGAS) SPOT 1m history -- not in Kaggle; only option snapshots (~5-7 days).",
        "Cash-index traded volume -- always 0; SPOT pressure uses price geometry only.",
        "Level-2 / aggressor / order-flow tape -- not present anywhere (see orderflow-l2 memory).",
    ]
    rep["summary"] = (
        "SPOT next-candle model: real walk-forward OOS possible (NIFTY 1m 2015-2025, BANKNIFTY 1m). "
        "CE/PE pressure + OI + dynamic trade management: DEMONSTRATOR ONLY -- 5-10 poll sessions per "
        "symbol, no dense option candles. Any option-side OOS claim is INSUFFICIENT_SAMPLE."
    )
    return rep
