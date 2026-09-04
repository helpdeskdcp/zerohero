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
def _hist_chain(sym: str, session_date: str) -> dict | None:
    """Latest captured option snapshot per (strike, side) -> chain rows in the
    shape market_context produces. ΔOI is derived by differencing vs the
    snapshot ~5 min earlier (histcap does not store oi_change)."""
    try:
        with _ro() as c:
            rows = c.execute(
                "SELECT received_ts, strike, option_type, ltp, oi, volume, expiry "
                "FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
                "AND session_date_ist=? AND strike IS NOT NULL "
                "ORDER BY received_ts", (sym, session_date)).fetchall()
    except Exception as e:
        _log.debug("_hist_chain(%s) read failed: %r", sym, e)
        return None
    if not rows:
        return None
    newest_ts = datetime.fromisoformat(rows[-1]["received_ts"].replace("Z", "+00:00"))
    if (datetime.now(timezone.utc) - newest_ts).total_seconds() > _HIST_CHAIN_MAX_AGE:
        return None
    cut_prev = (newest_ts - timedelta(minutes=5)).isoformat()
    latest: dict = {}
    prior: dict = {}
    for r in rows:
        k = (float(r["strike"]), str(r["option_type"]).upper())
        latest[k] = r
        if r["received_ts"] <= cut_prev and r["oi"] is not None:
            prior[k] = float(r["oi"])
    by_strike: dict = {}
    for (strike, side), r in latest.items():
        d = by_strike.setdefault(strike, {"strike": strike})
        pfx = "ce" if side == "CE" else "pe"
        oi = float(r["oi"]) if r["oi"] is not None else None
        base = prior.get((strike, side))
        d[f"{pfx}_ltp"] = float(r["ltp"]) if r["ltp"] is not None else None
        d[f"{pfx}_oi"] = oi
        d[f"{pfx}_oi_change"] = (oi - base) if (oi is not None and base is not None) else None
        d[f"{pfx}_volume"] = float(r["volume"]) if r["volume"] is not None else None
        d[f"{pfx}_oi_status"] = "AVAILABLE" if oi is not None else "MISSING"
        for g in ("delta", "gamma", "theta", "vega", "iv"):
            d[f"{pfx}_{g}"] = None
        d[f"{pfx}_greeks_source"] = "UNAVAILABLE"
    expiry = next((r["expiry"] for r in rows if r["expiry"]), None)
    chain = [by_strike[k] for k in sorted(by_strike)]
    for r in chain:
        r["expiry"] = expiry
    have = sum(1 for r in chain if r.get("ce_oi") is not None and r.get("pe_oi") is not None)
    return {"chain": chain, "source": "HISTCAP", "expiry": expiry,
            "age_sec": round((datetime.now(timezone.utc) - newest_ts).total_seconds()),
            "oi_coverage": {"legs": len(chain) * 2, "with_oi": have,
                            "ratio": round(have / len(chain), 3) if chain else 0.0}}


def _hist_bars(sym: str, session_date: str) -> list | None:
    try:
        with _ro() as c:
            for kind in ("INDEX", "FUTURE"):
                rows = c.execute(
                    "SELECT bar_start, o, h, l, c, v FROM market_candles WHERE symbol=? "
                    "AND kind=? AND tf='5m' AND session_date_ist=? ORDER BY bar_start",
                    (sym, kind, session_date)).fetchall()
                if not rows:
                    continue
                last = datetime.fromisoformat(rows[-1]["bar_start"].replace("Z", "+00:00"))
                if (datetime.now(timezone.utc) - last).total_seconds() > _HIST_BARS_MAX_AGE + 300:
                    return None
                return [{"high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"]} for r in rows]
    except Exception as e:
        _log.debug("_hist_bars(%s) read failed: %r", sym, e)
        return None
    return None


# ---- assembly -----------------------------------------------------------------
def snapshot(sym: str, *, window: int = 6, allow_rest_fallback: bool = True) -> dict:
    """market_context-shaped dict assembled from the WS feed + histcap.

    `allow_rest_fallback=False` (used by the bulk market-map endpoint) serves
    purely from cache/histcap/feed — no live selection_snapshot / get_candles —
    so a bulk 3-5 symbol read is always ~50ms regardless of market hours or how
    fresh histcap's capture is. The focused single-symbol endpoints
    (/signal, /oi, /levels) keep the throttled fallback for accuracy."""
    from .mathematical_confluence import context as _ctx     # reuse the primitives

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
        hq = _ctx._spot_from_histcap(sym)
        if hq and hq.get("ltp") is not None:
            ctx["spot"] = hq["ltp"]
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
        hp = _ctx._prevday_from_histcap(sym, sess)
        if hp:
            ctx["prev_day"] = hp
            ctx["data_quality"]["prev_day_ohlc"] = "ACTUAL (histcap)"
            _ctx._PREVDAY[sym] = (sess, dict(hp))

    # 3. 5m BARS — histcap only; the REST fallback is SKIPPED when we already
    # have day_high/day_low from the spot row (bars are optional for the engine:
    # pdh/pdl/pdc + spot are the only hard requirements). This is what kept
    # market-map latency spiky — a slow FIVE_MINUTE fetch for symbols histcap
    # hasn't back-filled yet.
    hb = _hist_bars(sym, sess)
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
    hc = _hist_chain(sym, sess)
    if hc:
        ctx["chain"] = hc["chain"]
        ctx["expiry"] = hc.get("expiry")
        ctx["oi_coverage"] = hc["oi_coverage"]
        ctx["data_quality"]["option_chain"] = f"ACTUAL (histcap, {hc['age_sec']}s)"
        if ctx.get("spot") and hc["chain"]:
            ctx["atm"] = min((r["strike"] for r in hc["chain"]), key=lambda k: abs(k - ctx["spot"]))
    elif allow_rest_fallback and sdk and _throttle((sym, "chain")):
        try:
            from . import market_data
            snap = market_data.selection_snapshot(
                sdk, mkt, sym, expiry="AUTO", option_type="BOTH", window=window,
                instrument="OPTION" if mkt in ("MCX", "BSE") else None)
            ctx["expiry"] = snap.get("expiry")
            ctx["atm"] = ctx.get("atm") or snap.get("atm")
            ctx["spot"] = ctx.get("spot") or snap.get("spot") or snap.get("atm")
            ctx["chain"] = _rows_from_snap(snap)
            ctx["oi_coverage"] = snap.get("oi_coverage")
            ctx["data_quality"]["option_chain"] = "ACTUAL (REST)" if ctx["chain"] else "MISSING"
        except Exception as e:
            ctx["data_quality"]["chain_error"] = f"{type(e).__name__}: {e}"
    if not ctx["chain"]:
        ctx["data_quality"].setdefault("option_chain", "MISSING")
    ctx["data_quality"].setdefault("greeks", "UNAVAILABLE (not on this surface)")
    return ctx


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
