"""Read-only data for the arena Terminal tab (chart + order panel): option-premium candles from the captured quotes of ONE contract, and the underlying's 5/15-minute chart with the Winners Scalper (Pine) indicator.
Nothing here writes anything or touches an order path."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from . import contracts as CT
from .settle import snap_expiry

IST = timezone(timedelta(hours=5, minutes=30)); IST_S = 19800
MCX = ("NATURALGAS", "CRUDEOIL", "GOLDM")


def _iso(ts: float) -> str: return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
def _ep(s: str) -> float: return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def premium(underlying: str, expiry_iso: str, strike: float, ot: str, tf: int = 1, since: float | None = None, now: float | None = None, path: str | None = None) -> dict:
    """OHLC candles (tf minutes) of the contract's last-traded price for today's session, from the exchange-stamped quotes (one per exchange second). `since` (UTC epoch of the last candle the client has) returns only that candle onwards."""
    now = now or time.time(); tfs = tf * 60
    day0 = datetime.fromtimestamp(now, IST).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    lo = day0 if since is None else max(day0, since - tfs); out = {"candles": [], "last": None, "tf": tf, "now": now}
    c = CT._ro(path)
    try:
        rows = c.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE symbol=? AND expiry=? AND strike=? AND option_type=? AND kind='OPTION' AND source='ANGELONE_QUOTE_FULL' AND ltp IS NOT NULL AND exch_ts>=?" + CT.NOT_FUTURE + " ORDER BY exch_ts, id",
                         (underlying, snap_expiry(expiry_iso), float(strike), ot, _iso(day0 - 2 * 86400))).fetchall()                       # the contract's (symbol, expiry, strike, type, exch_ts) index: a few thousand rows, never a table scan (heartbeat rows keep an OLD exch_ts, hence the 2-day lower bound)
    finally: c.close()
    # time axis = when OUR capture stored the price (heartbeat rows included): an illiquid contract whose exchange timestamp does not change still stands at that price, so its chart is continuous instead of 6 candles in 5 hours
    bars, last_b, last_px = {}, None, None
    for ts, ltp in rows:
        t = _ep(ts)
        if t < lo: continue
        b = int(t // tfs)
        if last_b is not None and 1 < b - last_b <= max(2, int(900 // tfs)):                       # carry the last price flat across a gap of up to 15 minutes (never longer: a longer silence stays a visible gap)
            for k in range(last_b + 1, b): bars.setdefault(k, [last_px, last_px, last_px, last_px])
        if b not in bars: bars[b] = [ltp, ltp, ltp, ltp]
        else: x = bars[b]; x[1] = max(x[1], ltp); x[2] = min(x[2], ltp); x[3] = ltp
        last_b, last_px = b, ltp
    out["candles"] = [dict(time=b * tfs + IST_S, open=v[0], high=v[1], low=v[2], close=v[3]) for b, v in sorted(bars.items())]
    out["last"] = CT.latest_quote(underlying, expiry_iso, float(strike), ot, path); return out


def _spot(sym: str):
    try:
        from .. import feed_registry
        from ..autoscalp.runner import _underlying_ref
        feed = feed_registry.get_feed(); tok = str((_underlying_ref(sym) or {}).get("token") or "")
        return float(feed.get_ltp(tok, 10.0)) if feed is not None and tok and feed.get_ltp(tok, 10.0) is not None else None
    except Exception: return None


def _bars_from_capture(sym: str, tf: int, now: float, days: int = 10, path: str | None = None) -> pd.DataFrame:
    kind = "FUTURE" if sym in MCX else "INDEX"; since = (datetime.fromtimestamp(now, IST) - timedelta(days=days)).strftime("%Y-%m-%d")
    c = CT._ro(path)
    try:
        rows = c.execute("SELECT bar_start, expiry, o, h, l, c, v FROM market_candles WHERE symbol=? AND kind=? AND tf='1m' AND session_date_ist>=? ORDER BY bar_start", (sym, kind, since)).fetchall()
    finally: c.close()
    if not rows: return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    d = pd.DataFrame(rows, columns=["timestamp", "expiry", "open", "high", "low", "close", "volume"]); d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True).dt.tz_convert(IST)
    if kind == "FUTURE": d = d[d["expiry"] == d["expiry"].iloc[-1]]                                                                 # the newest (front) contract only
    d = d.drop_duplicates("timestamp").set_index("timestamp")
    g = d.resample(f"{tf}min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}); n = d["close"].resample(f"{tf}min").count()
    g = g[n >= 1].dropna(subset=["close"]).reset_index(); end = g["timestamp"] + pd.Timedelta(minutes=tf)
    return g[end.map(pd.Timestamp.timestamp) <= now - 5].reset_index(drop=True)


_UV: dict = {}


def underlying_view(sym: str, tf: int = 15, n: int = 260, now: float | None = None, path: str | None = None) -> dict:
    hit = _UV.get((sym, tf, path))
    if hit and time.time() - hit[0] < 4.0 and now is None:
        return {**hit[1], "spot": _spot(sym)}                                                                                      # candles/indicator cached 4 s; the spot is always fresh
    out = _underlying_view(sym, tf, n, now, path)
    if now is None: _UV[(sym, tf, path)] = (time.time(), out)
    return out


def _underlying_view(sym: str, tf: int = 15, n: int = 260, now: float | None = None, path: str | None = None) -> dict:
    """Underlying candles + the Winners Scalper (Pine) indicator: trailing stop line, BUY/SELL markers (same code that is verified bar-for-bar against the Pine script) and the live spot from the WS feed."""
    from ..winners_scalper import strategy as WS
    now = now or time.time(); src = "capture (delayed)"; bars = None
    if sym == "NATURALGAS" and tf == 15:
        try:
            from ..winners_scalper import live as WL
            c = WL._conn()
            try: bars = WL.merged_bars(sym, now, c)
            finally: c.close()
            src = "live ticks + capture history"
        except Exception: bars = None
    if bars is None or len(bars) < 60: bars = _bars_from_capture(sym, tf, now, path=path); src = "capture (delayed)"
    out = {"underlying": sym, "tf": tf, "source": src, "spot": _spot(sym), "candles": [], "stop": [], "ma": [], "markers": [], "now": now}
    if len(bars) < 40: return out
    s = WS.signals(bars).tail(n).reset_index(drop=True); t = [int(ts.timestamp()) + IST_S for ts in s["timestamp"]]
    out["candles"] = [dict(time=t[i], open=float(s.open.iat[i]), high=float(s.high.iat[i]), low=float(s.low.iat[i]), close=float(s.close.iat[i])) for i in range(len(s))]
    for i in range(len(s)):
        up = int(s["dir"].iat[i]) == 1; v = s["long_stop"].iat[i] if up else s["short_stop"].iat[i]
        if v == v: out["stop"].append(dict(time=t[i], value=float(v), up=up))
        if s["ma"].iat[i] == s["ma"].iat[i]: out["ma"].append(dict(time=t[i], value=float(s["ma"].iat[i])))
        if s["buy"].iat[i] or s["sell"].iat[i]: out["markers"].append(dict(time=t[i], side="BUY" if s["buy"].iat[i] else "SELL", strong=bool(s["strong"].iat[i])))
    return out
