"""
MarketHub — the single read path for live index market data (architecture
audit, phases 2-4).

Everything on the mathematical-confluence / smart-index-scalper surface reads
market data from here. The broker's REST API is touched only as a THROTTLED
last-resort fallback; the primary sources are already running:

  - spot / LTP  ← the shared WS feed              (0 REST, always fresh)
  - option OI   ← histcap  market_history.db.quote_snapshots (captured ~20s)
  - 5m bars     ← histcap  market_history.db.market_candles
  - prev-day    ← per-IST-day cache primed from either source

histcap is already the single leader-elected broker capturer for the configured
universe, so this turns the mathematics/ranking pages into free riders on that
one stream instead of N independent pollers each hammering the rate-limited
REST endpoints. Live REST fallback is rate-capped (per-symbol per-slice
min-interval + a global min-gap) so a burst of cold symbols can never exceed
~2.5 req/s.

`market_context()` (used by /api/mathematics/* and the ranking scanner) is a
thin wrapper over `snapshot()`.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
_MKT = {"SENSEX": "BSE", "BANKEX": "BSE", "NATURALGAS": "MCX", "CRUDEOIL": "MCX"}

try:
    from .histcap.store import DB_PATH as _HDB
except Exception:                                            # pragma: no cover
    _HDB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "market_history.db")

# ---- fallback rate control -------------------------------------------------
_CHAIN_TTL = float(os.environ.get("HUB_CHAIN_FALLBACK_SEC", "25"))
_BARS_TTL = float(os.environ.get("HUB_BARS_FALLBACK_SEC", "240"))
_GLOBAL_MIN_GAP = float(os.environ.get("HUB_REST_MIN_GAP_SEC", "0.4"))
_HIST_CHAIN_MAX_AGE = float(os.environ.get("HUB_CHAIN_MAX_AGE_SEC", "300"))
_HIST_BARS_MAX_AGE = float(os.environ.get("HUB_BARS_MAX_AGE_SEC", "600"))

_last_call: dict = {}          # (sym, slice) -> ts of last live REST fallback
_last_any = [0.0]             # ts of the last hub REST call, any symbol/slice
_lock = threading.Lock()


def _throttle(key) -> bool:
    """True (and records now) if a live REST fallback for `key` is allowed."""
    now = time.time()
    ttl = _CHAIN_TTL if key[1] == "chain" else _BARS_TTL
    with _lock:
        if now - _last_call.get(key, 0.0) < ttl:
            return False
        if now - _last_any[0] < _GLOBAL_MIN_GAP:
            return False
        _last_call[key] = now
        _last_any[0] = now
        return True


def _ro(path=None):
    c = sqlite3.connect(f"file:{path or _HDB}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _now_ist_date() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


# ---- histcap-backed slices ------------------------------------------------
def _hist_chain(sym: str, session_date: str, *, provider=None, expiry=None,
                as_of=None, expiry_dates=None) -> dict | None:
    """One captured provider/expiry only. OI deltas use that same contract.

    An omitted expiry requires provider calendar evidence; captured subsets
    are not expiry calendars. Live/as-of freshness is measured backwards.
    """
    from .histcap import read_contract as rc
    cutoff = as_of or datetime.now(timezone.utc).isoformat()
    result = session_option_quotes_result(sym, session_date, provider=provider,
        expiry=expiry, as_of=cutoff, expiry_dates=expiry_dates, max_age_sec=_HIST_CHAIN_MAX_AGE)
    rows = result["rows"]
    if not rows:
        return None
    newest = rc.instant(rows[-1]["timestamp"])
    latest = {}
    for r in rows:
        key = (float(r["strike"]), r["option_type"])
        latest[key] = r
    by_strike = {}
    for (strike, side), r in latest.items():
        cut = rc.instant(r["timestamp"]) - timedelta(minutes=5)
        # Baseline must be from this exact provider/expiry/leg, never a future quote.
        prior_rows = [x for x in rows if x["strike"] == strike and x["option_type"] == side
                      and rc.instant(x["timestamp"]) <= cut and x["oi"] is not None]
        base = prior_rows[-1]["oi"] if prior_rows else None
        d = by_strike.setdefault(strike, {"strike": strike, "expiry": result["expiry"],
            "provider": result["provider"], "source": result["source"],
            "fallback_used": result["fallback_used"], "fallback_reason": result["fallback_reason"],
            "as_of": cutoff, "calendar": result.get("calendar")})
        pfx = "ce" if side == "CE" else "pe"
        for field in ("ltp", "oi", "volume", "token", "exchange", "instrument_key"):
            d[f"{pfx}_{field}"] = r[field]
        d[f"{pfx}_oi_change"] = r["oi"] - base if r["oi"] is not None and base is not None else None
        d[f"{pfx}_oi_status"] = "AVAILABLE" if r["oi"] is not None else "MISSING"
        d[f"{pfx}_oi_source"] = r["source"]
        d[f"{pfx}_oi_timestamp"] = r["timestamp"]
        d[f"{pfx}_quote_source"] = r["source"]
        d[f"{pfx}_timestamp"] = r["timestamp"]
        d[f"{pfx}_quote_id"] = r["id"]
        d[f"{pfx}_raw_id"] = r["raw_id"]
        for g in ("delta", "gamma", "theta", "vega", "iv"):
            d[f"{pfx}_{g}"] = None
        d[f"{pfx}_greeks_source"] = "UNAVAILABLE"
    chain = [by_strike[k] for k in sorted(by_strike)]
    have = sum(r.get(f"{side}_oi") is not None for r in chain for side in ("ce", "pe"))
    return {**{k: v for k, v in result.items() if k != "rows"}, "chain": chain,
            "age_sec": round((rc.instant(cutoff) - newest).total_seconds()),
            "oi_coverage": {"legs": len(chain) * 2, "with_oi": have,
                            "ratio": round(have / (2 * len(chain)), 3)}}


def session_bars_result(sym: str, session_date: str, *, tf="5m", provider=None,
                        expiry=None, as_of=None) -> dict:
    """Metadata-bearing historical session read; no REST or database writes.

    Default precedence: DHAN_HISTORICAL -> ANGELONE_CANDLES. Explicit
    provider requests never fall through. FUTURE -> INDEX preference and
    earliest-capture rollover dedup operate WITHIN the selected provider.
    With expiry, this is a FUTURE contract read, not a continuous series.
    as_of caps market bar CLOSE time, not the later backfill ingestion time.
    Empty results have an explicit reason; no missing bars are fabricated.
    """
    from .histcap import read_contract as rc
    cutoff = as_of or datetime.now(timezone.utc).isoformat()
    try:
        with _ro() as c:
            return rc.bars(c, sym, session_date, tf=tf, provider=provider, expiry=expiry, as_of=cutoff)
    except sqlite3.Error:
        return {**rc.result(sym, provider, cutoff), "reason": "HISTORY_UNAVAILABLE"}


def session_bars(sym: str, session_date: str, *, tf="5m", provider=None,
                 expiry=None, as_of=None) -> list:
    """Compatibility list view of session_bars_result; every bar retains provenance.

    UNDERLYING_SESSION preserves the original futures/index research view.
    It is not an adjusted continuous-futures contract or an option series.
    Use expiry for CONTRACT reads and session_bars_result for no-data reasons.
    """
    return session_bars_result(sym, session_date, tf=tf, provider=provider,
                               expiry=expiry, as_of=as_of)["rows"]


def session_dates(sym: str, *, tf: str = "5m", limit: int = 30,
                  provider=None, expiry=None, as_of=None) -> list:
    """Dates with a valid session_bars result, newest first. Read-only.

    Each date independently follows the same explicit/default provider policy.
    A date index contains no merged market values; its bars retain provenance.
    """
    from .histcap import read_contract as rc
    sym = sym.upper()
    cutoff = as_of or datetime.now(timezone.utc).isoformat()
    try:
        rc.instant(cutoff)
        sources = [rc.SOURCES[p]["candles"] for p in rc.providers(provider)]
        with _ro() as c:
            rows = c.execute(
                "SELECT DISTINCT session_date_ist d FROM market_candles "
                "WHERE symbol=? AND tf=? AND kind IN ('FUTURE','INDEX') "
                f"AND source IN ({','.join('?' for _ in sources)}) "
                "AND julianday(bar_start)<=julianday(?) ORDER BY d DESC",
                (sym, tf, *sources, cutoff)).fetchall()
            dates = []
            for r in rows:
                if len(dates) >= max(0, limit):
                    break
                if rc.bars(c, sym, r["d"], tf=tf, provider=provider, expiry=expiry, as_of=cutoff)["rows"]:
                    dates.append(r["d"])
        return dates
    except (sqlite3.Error, ValueError, TypeError) as e:
        _log.debug("session_dates(%s) read failed: %r", sym, e)
        return []


def session_option_quotes_result(sym: str, session_date: str, *, provider=None,
                                 expiry=None, as_of=None, expiry_dates=None,
                                 max_age_sec=None) -> dict:
    """One provider and one expiry, with quote/OI/raw-id provenance.

    Explicit dates win. The legacy expiry_dates keyword now accepts verified
    ProviderCalendars only. Bare lists cannot prove historical availability.
    """
    from .histcap import read_contract as rc
    cutoff = as_of or datetime.now(timezone.utc).isoformat()
    try:
        with _ro() as c:
            return rc.options(c, sym, session_date, provider=provider, expiry=expiry,
                              as_of=cutoff, expiry_dates=expiry_dates, max_age_sec=max_age_sec)
    except sqlite3.Error:
        return {**rc.result(sym, provider, cutoff), "reason": "HISTORY_UNAVAILABLE"}


def session_option_quotes(sym: str, session_date: str, *, provider=None,
                          expiry=None, as_of=None, expiry_dates=None) -> dict:
    """(strike, side) -> ordered quote RECORDS including provider/expiry/time.

    Replaces the lossy two-value premium tuples. Consumers must preserve the
    records' provenance; pure premium-walk fixtures may still use tuple input.
    No expiry evidence means no data, rather than mixing captured contracts.
    """
    result = session_option_quotes_result(sym, session_date, provider=provider,
                    expiry=expiry, as_of=as_of, expiry_dates=expiry_dates)
    out = {}
    for r in result["rows"]:
        out.setdefault((float(r["strike"]), r["option_type"]), []).append(r)
    return out


def _hist_bars(sym: str, session_date: str, *, provider=None) -> list | None:
    from .histcap import read_contract as rc
    cutoff = datetime.now(timezone.utc).isoformat()
    try:
        with _ro() as c:
            result = rc.bars(c, sym, session_date, tf="5m", provider=provider, expiry=None,
                             as_of=cutoff, kinds=("INDEX", "FUTURE"))
        rows = result["rows"]
        if not rows or (rc.instant(cutoff) - rc.instant(rows[-1]["bar_start"])).total_seconds() > _HIST_BARS_MAX_AGE + 300:
            return None
        return [{**r, "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"]} for r in rows]
    except (sqlite3.Error, ValueError, TypeError):
        return None


# ---- assembly -----------------------------------------------------------------
def snapshot(sym: str, *, window: int = 6, allow_rest_fallback: bool = True) -> dict:
    """market_context-shaped dict assembled from the WS feed + histcap.

    `allow_rest_fallback=False` (used by the bulk market-map endpoint) serves
    purely from cache/histcap/feed — no live selection_snapshot / get_candles —
    so a bulk 3-5 symbol read is always ~50ms regardless of market hours or how
    fresh histcap's capture is. The focused single-symbol endpoints
    (/signal, /oi, /levels) keep the throttled fallback for accuracy."""
    from .mathematical_confluence import context as _ctx  # reuse the primitives

    sym = sym.upper()
    sess = _now_ist_date()
    mkt = _MKT.get(sym, "NSE")
    ctx: dict = {"instrument": sym, "market": mkt, "prev_day": {}, "bars": [],
                 "chain": [], "data_quality": {}, "source": "MARKET_HUB"}

    sdk = None
    try:
        from .connectors.angelone import _market_sdk
        sdk = _market_sdk(require_auth=True)
    except Exception as e:
        _log.debug("snapshot(%s): broker SDK unavailable, feed/histcap-only: %r", sym, e)

    # 1. SPOT — WS feed -> histcap quote -> throttled REST
    spot = _ctx._feed_spot(sym, sdk) if sdk else None
    if spot is not None:
        ctx["spot"] = spot
        ctx["data_quality"]["spot"] = "ACTUAL (WS feed)"
    else:
        hq = _ctx._spot_from_histcap(sym, provider="ANGELONE")
        if hq and hq.get("ltp") is not None:
            ctx["spot"] = hq["ltp"]
            ctx["spot_source"] = hq["source"]
            ctx["spot_timestamp"] = hq["received_ts"]
            ctx["data_quality"]["spot"] = "ACTUAL (histcap)"
            if hq.get("open") is not None:
                ctx["today_open"] = hq["open"]
            if hq.get("high") is not None and hq.get("low") is not None:
                ctx["day_high"], ctx["day_low"] = hq["high"], hq["low"]

    # 2. PREV-DAY — day-cache -> histcap candles -> (REST handled inside _ctx on the rare fallthrough)
    pdc = _ctx._PREVDAY.get(sym)
    if pdc and pdc[0] == sess:
        ctx["prev_day"] = dict(pdc[1])
        ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL (day-cached)"
    else:
        hp = _ctx._prevday_from_histcap(sym, sess, provider="ANGELONE")
        if hp:
            ctx["prev_day"] = hp
            ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL (histcap)"
            _ctx._PREVDAY[sym] = (sess, dict(hp))

    # 3. 5m BARS — histcap only; the REST fallback is SKIPPED when we already
    # have day_high/day_low from the spot row (bars are optional for the engine:
    # pdh/pdl/pdc + spot are the only hard requirements). This is what kept
    # market-map latency spiky — a slow FIVE_MINUTE fetch for symbols histcap
    # hasn't back-filled yet.
    hb = _hist_bars(sym, sess, provider="ANGELONE")
    if hb:
        ctx["bars"] = hb
        ctx["data_quality"]["intraday_bars"] = f"ACTUAL (histcap x{len(hb)})"
    elif allow_rest_fallback and sdk and ctx.get("day_high") is None and _throttle((sym, "bars")):
        try:
            tk = _ctx._resolve_idx_token(sdk, sym)
            if tk:
                exch = _MKT.get(sym, "NSE")
                now = datetime.now(IST)
                d = sdk.get_candles(exch, tk[0], "FIVE_MINUTE",
                                    now.strftime("%Y-%m-%d 09:00"), now.strftime("%Y-%m-%d %H:%M"))
                ctx["bars"] = [{"high": c["high"], "low": c["low"], "close": c["close"],
                                "volume": c.get("volume")} for c in (d.get("candles") or [])]
                ctx["data_quality"]["intraday_bars"] = f"ACTUAL (REST x{len(ctx['bars'])})"
        except Exception as e:
            _log.debug("snapshot(%s): REST bars fallback failed: %r", sym, e)
    if ctx["bars"]:
        b = ctx["bars"]
        ctx.setdefault("day_high", max(x["high"] for x in b))
        ctx.setdefault("day_low", min(x["low"] for x in b))
        closes = [x["close"] for x in b]
        if len(closes) >= 4:
            ctx["mom_3m"] = round(closes[-1] - closes[-4], 4)
        vols = [x["volume"] for x in b if x.get("volume")]
        if len(vols) >= 6:
            ctx["current_volume"] = vols[-1]
            ctx["avg_volume"] = sum(vols[-20:-1]) / max(1, len(vols[-20:-1]))

    # 4. OPTION CHAIN / OI — histcap -> throttled REST (selection_snapshot)
    gc = get_chain(sym, window=window, allow_rest_fallback=allow_rest_fallback, sdk=sdk, provider="ANGELONE")
    if gc["chain"]:
        ctx["chain"] = gc["chain"]
        ctx["expiry"] = gc.get("expiry")
        ctx["oi_coverage"] = gc.get("oi_coverage")
        ctx["data_quality"]["option_chain"] = gc["data_quality_note"]
        if gc.get("atm") is not None:
            ctx["atm"] = gc["atm"]
        elif ctx.get("spot") and gc["chain"]:
            ctx["atm"] = min((r["strike"] for r in gc["chain"]), key=lambda k: abs(k - ctx["spot"]))
        if gc.get("spot") is not None:
            ctx["spot"] = ctx.get("spot") or gc["spot"]
    elif gc.get("error"):
        ctx["data_quality"]["chain_error"] = gc["error"]
    if not ctx["chain"]:
        ctx["data_quality"].setdefault("option_chain", "MISSING")
    ctx["data_quality"].setdefault("greeks", "UNAVAILABLE (not on this surface)")
    return ctx


def get_chain(sym: str, *, window: int = 6, allow_rest_fallback: bool = True,
             expiry_mode: str = "AUTO", sdk=None, provider=None, as_of=None,
             expiry_dates=None) -> dict:
    """One provider per chain: default DHAN -> ANGELONE; explicit means only.

    Historical/as-of calls are captured-only, even if REST was requested.
    AUTO/NEXT is resolved independently by each provider. Captured reads need
    expiry_dates evidence; a live provider resolves its own aliases. AUTO_ROLL
    remains the AngelOne SDK contract and never uses a captured shortcut.
    """
    from .histcap import read_contract as rc
    sym = sym.upper()
    mkt = _MKT.get(sym, "NSE")
    cutoff = as_of or datetime.now(timezone.utc).isoformat()
    out = {**rc.result(sym, provider, cutoff), "chain": [], "expiry": None, "atm": None,
           "spot": None, "oi_coverage": None, "data_quality_note": "MISSING", "error": None}
    try:
        order = rc.providers(provider)
        sess = rc.instant(cutoff).astimezone(IST).date().isoformat()
        alias = str(expiry_mode or "AUTO").upper()
        explicit = None if alias in ("AUTO", "CURRENT", "NEXT", "LATEST", "AUTO_ROLL") else rc.expiry_date(expiry_mode)
    except (ValueError, TypeError):
        out["reason"] = "INVALID_REQUEST"
        return out
    rest_allowed = None
    for p in order:
        fallback = provider is None and p == "ANGELONE"
        if (as_of is not None or not allow_rest_fallback) and alias in ("AUTO", "CURRENT", "NEXT", "LATEST"):
            try:
                rc.resolve_expiry(expiry_mode, p, rc.instant(cutoff), expiry_dates, underlying=sym)
            except (ValueError, TypeError) as exc:
                rc.unavailable(out, p, str(exc))
                continue
        hc = None if alias == "AUTO_ROLL" else _hist_chain(sym, sess, provider=p,
                expiry=expiry_mode, as_of=cutoff, expiry_dates=expiry_dates)
        if hc:
            attempts = out["attempts"]
            out.update(hc, data_quality_note=f"ACTUAL (histcap, {hc['age_sec']}s)")
            out.update(attempts=attempts, requested_provider=provider)
        elif allow_rest_fallback and as_of is None:
            if rest_allowed is None:
                rest_allowed = _throttle((sym, "chain"))
            if rest_allowed:
                try:
                    if p == "DHAN":
                        if alias == "AUTO_ROLL":
                            rc.unavailable(out, p, "UNSUPPORTED_EXPIRY_MODE")
                            continue
                        from .optionchain.sources import dhan_chain
                        chain = dhan_chain.fetch(sym, expiry_mode)
                        if chain and chain.rows:
                            out.update(_dhan_chain_result(chain, mkt))
                    else:
                        if sdk is None:
                            from .connectors.angelone import _market_sdk
                            sdk = _market_sdk(require_auth=True)
                        if sdk:
                            from . import market_data
                            snap = market_data.selection_snapshot(sdk, mkt, sym, expiry=expiry_mode,
                                option_type="BOTH", window=window,
                                instrument="OPTION" if mkt in ("MCX", "BSE") else None)
                            out.update(expiry=snap.get("expiry"), atm=snap.get("atm"),
                                       spot=snap.get("spot"), chain=_rows_from_snap(snap),
                                       oi_coverage=snap.get("oi_coverage"), source="ANGELONE_QUOTE_FULL")
                    if out["chain"]:
                        if explicit and rc.expiry_date(out["expiry"]) != explicit:
                            out["chain"] = []
                            rc.unavailable(out, p, "EXPIRY_MISMATCH")
                            continue
                        out["data_quality_note"] = "ACTUAL (REST)"
                except Exception as e:
                    # Never include broker exception payloads/credentials in the result.
                    out["error"] = type(e).__name__
        if out["chain"]:
            reason = out["attempts"][0]["reason"] if fallback and out["attempts"] else None
            out.update(status="OK", reason=None, provider=p, fallback_used=fallback, fallback_reason=reason)
            for row in out["chain"]:
                row.update(provider=p, source=out["source"], fallback_used=fallback,
                           fallback_reason=reason, as_of=as_of)
            return out
        rc.unavailable(out, p, "NO_VALID_CAPTURE_OR_PROVIDER_RESPONSE")
    return out


def _dhan_chain_result(chain, market):
    """Keep Dhan identifiers and field attribution; no AngelOne Greek merge."""
    from .histcap.read_contract import finite
    rows = []
    exchange = {"NSE": "NFO", "BSE": "BFO", "MCX": "MCX"}[market]
    for strike in chain.rows:
        row = {"strike": strike.strike, "expiry": chain.expiry}
        for side in ("ce", "pe"):
            leg = getattr(strike, side)
            if leg is None:
                continue
            if not finite(leg.ltp, positive=True):
                return {"chain": []}
            for field in ("ltp", "oi", "volume", "iv", "delta", "gamma", "theta", "vega", "oi_change"):
                row[f"{side}_{field}"] = getattr(leg, field)
            row.update({f"{side}_token": leg.security_id, f"{side}_exchange": exchange,
                        f"{side}_oi_source": "DHAN_CHAIN", f"{side}_quote_source": "DHAN_CHAIN",
                        f"{side}_oi_timestamp": chain.ts, f"{side}_timestamp": chain.ts,
                        f"{side}_oi_status": "AVAILABLE" if leg.oi is not None else "MISSING",
                        f"{side}_greeks_source": "DHAN_OPTION_GREEK" if leg.delta is not None else "UNAVAILABLE"})
        rows.append(row)
    return {"chain": rows, "expiry": chain.expiry, "spot": chain.spot,
            "spot_source": "DHAN_CHAIN", "atm": chain.atm_strike, "source": "DHAN_CHAIN"}


def _rows_from_snap(snap: dict) -> list:
    out = []
    for r in (snap.get("chain") or []):
        row = {"strike": r.get("strike"), "expiry": snap.get("expiry")}
        for pfx in ("ce", "pe"):
            row[f"{pfx}_ltp"] = r.get(f"{pfx}_ltp")
            row[f"{pfx}_oi"] = r.get(f"{pfx}_oi")
            row[f"{pfx}_oi_change"] = r.get(f"{pfx}_oi_change")
            row[f"{pfx}_volume"] = r.get(f"{pfx}_volume")
            row[f"{pfx}_oi_status"] = r.get(f"{pfx}_oi_status")
            row[f"{pfx}_token"] = r.get(f"{pfx}_token")
            row[f"{pfx}_oi_source"] = r.get(f"{pfx}_oi_source")
            row[f"{pfx}_oi_timestamp"] = r.get(f"{pfx}_oi_timestamp")
            for g in ("delta", "gamma", "theta", "vega", "iv"):
                row[f"{pfx}_{g}"] = r.get(f"{pfx}_{g}")
            row[f"{pfx}_greeks_source"] = r.get(f"{pfx}_greeks_source")
        out.append(row)
    return out


def stats() -> dict:
    with _lock:
        return {"last_fallback_calls": {f"{k[0]}:{k[1]}": round(time.time() - v, 1)
                                        for k, v in _last_call.items()},
                "chain_ttl": _CHAIN_TTL, "bars_ttl": _BARS_TTL, "min_gap": _GLOBAL_MIN_GAP,
                "db": _HDB}
