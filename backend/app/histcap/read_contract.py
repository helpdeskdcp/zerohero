"""Read-side contracts only: one provider per result, no database mutations.

Bars use market time (bar close <= as_of), allowing historical backfills.
Quotes require capture AND exchange timestamps <= as_of. Missing expiry
evidence is not inferred from whichever contracts happened to be captured.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

SOURCES = {
    "DHAN": {"candles": "DHAN_HISTORICAL", "quotes": "DHAN_CHAIN"},
    "ANGELONE": {"candles": "ANGELONE_CANDLES", "quotes": "ANGELONE_QUOTE_FULL"},
}
IST = timezone(timedelta(hours=5, minutes=30))


def instant(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return dt.astimezone(timezone.utc)


def expiry_date(value):
    for fmt in ("%d%b%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(value).upper(), fmt).date()
        except ValueError:
            continue
    raise ValueError("invalid expiry")


def providers(provider):
    if provider is None:
        return ["DHAN", "ANGELONE"]
    name = str(provider).upper()
    for p, sources in SOURCES.items():
        if name == p or name in sources.values():
            return [p]
    raise ValueError("unsupported provider")


def finite(value, *, positive=False):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def result(symbol, provider, as_of):
    return {"status": "UNAVAILABLE", "reason": "NO_DATA", "rows": [],
            "underlying": symbol.upper(), "requested_provider": provider,
            "provider": None, "source": None, "as_of": as_of,
            "fallback_used": False, "fallback_reason": None, "attempts": []}


def selected(out, rows, provider, source):
    fallback = out["requested_provider"] is None and provider == "ANGELONE"
    reason = out["attempts"][0]["reason"] if fallback and out["attempts"] else None
    metadata = {"provider": provider, "source": source, "fallback_used": fallback,
                "fallback_reason": reason, "as_of": out["as_of"], "underlying": out["underlying"]}
    out.update(metadata, status="OK", reason=None, rows=[{**r, **metadata} for r in rows])
    return out


def unavailable(out, provider, reason):
    out["attempts"].append({"provider": provider, "reason": reason})
    out["reason"] = reason


def resolve_expiry(want, provider, cutoff, expiry_dates, *, underlying=None):
    """Compatibility date projection; aliases require verified ProviderCalendars."""
    from ..hist_options.provider_calendar import resolve
    return resolve(want, provider, underlying, cutoff, expiry_dates)[0]


def bars(conn, symbol, session, *, tf, provider, expiry, as_of, kinds=("FUTURE", "INDEX")):
    out = result(symbol, provider, as_of)
    try:
        cutoff = instant(as_of)
        order = providers(provider)
        step = timedelta(minutes=int(tf[:-1])) if tf.endswith("m") else timedelta(days=1) if tf == "1d" else None
        if step is None or step.total_seconds() <= 0:
            raise ValueError("invalid timeframe")
        exp = expiry_date(expiry) if expiry is not None else None
    except (ValueError, TypeError):
        out["reason"] = "INVALID_REQUEST"
        return out
    out["series_type"] = "CONTRACT" if exp else "UNDERLYING_SESSION"
    out["timeframe"] = tf
    for p in order:
        source = SOURCES[p]["candles"]
        failure = "NO_DATA"
        for kind in (("FUTURE",) if exp else kinds):
            records = conn.execute("SELECT rowid AS capture_id, * FROM market_candles WHERE symbol=? "
                                   "AND session_date_ist=? AND tf=? AND source=? AND kind=? ORDER BY rowid",
                                   (symbol.upper(), session, tf, source, kind)).fetchall()
            by_time = {}
            for record in records:
                r = dict(record)
                try:
                    row_exp = expiry_date(r["expiry"]) if kind == "FUTURE" else None
                    if exp is not None and row_exp != exp:
                        continue
                    ts = instant(r["bar_start"])
                    if ts + step > cutoff or ts.astimezone(IST).date().isoformat() != session:
                        continue
                    if row_exp and ts.astimezone(IST).date() > row_exp:
                        continue
                    if not r["instrument_key"] or not r["exchange"] or not r["token"]:
                        raise ValueError("missing instrument identity")
                    if not all(finite(r[k], positive=True) for k in ("o", "h", "l", "c")):
                        raise ValueError("invalid OHLC")
                    if r["h"] < max(r["o"], r["c"], r["l"]) or r["l"] > min(r["o"], r["c"]):
                        raise ValueError("invalid OHLC range")
                    if r["v"] is not None and not finite(r["v"]):
                        raise ValueError("invalid volume")
                    # Preserve legacy earliest-capture rollover dedup within the selected source.
                    r.update(series_type=out["series_type"], timestamp=r["bar_start"])
                    by_time.setdefault(ts, r)
                except (ValueError, TypeError, KeyError):
                    failure = "INVALID_DATA"
                    break
            if failure == "INVALID_DATA":
                break
            times = sorted(by_time)
            if any(b - a > step for a, b in zip(times, times[1:])):
                failure = "INCOMPLETE_SERIES"
                break
            if times:
                return selected(out, [by_time[t] for t in times], p, source)
        unavailable(out, p, failure)
    return out



_ID_CACHE: dict = {}
_SPOT_SQL = ("SELECT * FROM quote_snapshots WHERE {rng}symbol=? AND source=? AND kind=? "
             "AND julianday(received_ts)<=julianday(?) "
             "AND julianday(received_ts)>=julianday(?)-?/86400.0 "
             "AND (exch_ts IS NULL OR julianday(exch_ts)<=julianday(?)) "
             "ORDER BY julianday(received_ts) DESC, id DESC")


def _spot_rows(conn, symbol, source, kind, as_of, max_age_sec, cutoff):
    """The as-of quote window of one symbol. The exact time predicates are unchanged; an id range (see histcap.id_window: provably contains every wanted row) is added so SQLite filters on the row id
    inside the index and never reads the wide table rows of the symbol's whole history (measured 2026-10-28: 9.6 / 3.1 / 15.1 s for NIFTY / SENSEX / NATURALGAS on a cold cache, ~0.00 s with the range).
    Any problem computing the range (a connection that cannot answer, a view) falls back to the original unbounded query."""
    args = (symbol.upper(), source, kind, as_of, as_of, max_age_sec, as_of)
    try:
        from . import id_window as ID
        key = ID.db_key(conn); end = cutoff.timestamp()
        lo = ID.floor_id(conn, end - float(max_age_sec), cache=_ID_CACHE, key=key); hi = ID.ceil_id(conn, end, cache=_ID_CACHE, key=key)
        rng = "id>%d AND " % lo + ("id<=%d AND " % hi if hi is not None else "")
        return conn.execute(_SPOT_SQL.format(rng=rng), args).fetchall()
    except Exception:
        return conn.execute(_SPOT_SQL.format(rng=""), args).fetchall()


def spot(conn, symbol, *, provider, as_of, max_age_sec):
    out = result(symbol, provider, as_of)
    cutoff = instant(as_of)
    for p in providers(provider):
        source = SOURCES[p]["quotes"]
        for kind in ("INDEX", "FUTURE"):
            rows = _spot_rows(conn, symbol, source, kind, as_of, max_age_sec, cutoff)
            valid = []
            for r in rows:
                if not finite(r["ltp"], positive=True):
                    continue
                if kind == "FUTURE":
                    try:
                        if expiry_date(r["expiry"]) < cutoff.astimezone(IST).date():
                            continue
                    except ValueError:
                        continue
                valid.append(dict(r))
            if len({(r["instrument_key"], r["expiry"]) for r in valid}) == 1:
                return selected(out, [valid[0]], p, source)
        unavailable(out, p, "NO_UNAMBIGUOUS_ASOF_QUOTE")
    return out


def options(conn, symbol, session, *, provider, expiry, as_of, expiry_dates=None, max_age_sec=None):
    out = result(symbol, provider, as_of)
    try:
        cutoff, order = instant(as_of), providers(provider)
    except (ValueError, TypeError):
        out["reason"] = "INVALID_REQUEST"
        return out
    for p in order:
        source = SOURCES[p]["quotes"]
        try:
            from ..hist_options.provider_calendar import ALIASES, resolve
            if str(expiry or "AUTO").upper() in ALIASES and cutoff.astimezone(IST).date().isoformat() != session:
                raise ValueError("CALENDAR_SESSION_ASOF_MISMATCH")
            exp, calendar = resolve(expiry, p, symbol, cutoff, expiry_dates)
        except (ValueError, TypeError) as e:
            unavailable(out, p, str(e))
            continue
        rows = []
        failure = "NO_DATA"
        for record in conn.execute("SELECT * FROM quote_snapshots WHERE symbol=? AND session_date_ist=? "
                                   "AND kind='OPTION' AND source=? ORDER BY id",
                                   (symbol.upper(), session, source)).fetchall():
            r = dict(record)
            try:
                if expiry_date(r["expiry"]) != exp:
                    continue
                ts = instant(r["received_ts"])
                if calendar["verification_status"] == "VERIFIED":
                    if not instant(calendar["listed_at"]) <= ts < instant(calendar["last_trading_at"]):
                        continue
                    venue = {"NFO": "NSE", "NSE_FNO": "NSE", "BFO": "BSE", "BSE_FNO": "BSE",
                             "MCX_COMM": "MCX"}.get(r["exchange"], r["exchange"])
                    if venue != calendar["exchange"]:
                        raise ValueError("calendar exchange mismatch")
                if ts > cutoff or (r["exch_ts"] and instant(r["exch_ts"]) > cutoff):
                    continue
                if ts.astimezone(IST).date().isoformat() != session or ts.astimezone(IST).date() > exp:
                    continue
                if (r["option_type"] not in ("CE", "PE") or not finite(r["strike"], positive=True)
                        or not finite(r["ltp"], positive=True) or not r["exchange"] or not r["token"]):
                    raise ValueError("invalid contract/quote")
                if any(r[k] is not None and not finite(r[k]) for k in ("oi", "volume")):
                    raise ValueError("invalid OI/volume")
                if r["quote_status"] not in (None, "OK"):
                    raise ValueError("invalid quote status")
                r["timestamp"] = ts.isoformat().replace("+00:00", "Z")
                rows.append(r)
            except (ValueError, TypeError, KeyError):
                failure = "INVALID_DATA"
                rows = []
                break
        if rows:
            identities = {}
            for r in rows:
                identities.setdefault((r["strike"], r["option_type"]), set()).add(
                    (r["exchange"], r["instrument_key"], r["token"]))
            if len({r["exchange"] for r in rows}) != 1 or any(len(ids) != 1 for ids in identities.values()):
                unavailable(out, p, "AMBIGUOUS_CONTRACT")
                continue
            if max_age_sec is not None and (cutoff - max(instant(r["timestamp"]) for r in rows)).total_seconds() > max_age_sec:
                unavailable(out, p, "STALE_DATA")
                continue
            out["expiry"] = exp.strftime("%d%b%Y").upper()
            out["calendar"] = calendar
            for row in rows:
                row["calendar"] = calendar
            return selected(out, sorted(rows, key=lambda r: (instant(r["timestamp"]), r["id"])), p, source)
        unavailable(out, p, failure)
    return out
