"""
Layer 2b -- time-series option analytics built on ALREADY-CAPTURED histcap
data (market_history.db :: quote_snapshots / option_greeks / market_candles).

These are the three OpenAlgo-style analytics that genuinely don't exist
anywhere in zerohero yet (checked against app/optionchain/analytics.py,
app/greeks_engine/, and the frontend before writing a line of this):

  - Volatility Surface : strike x expiry grid of real broker-reported IV,
    built from option_greeks' multiple ALREADY-captured expiries (no new
    live/multi-expiry broker fetch -- avoids repeating the exact AngelOne
    rate-limit problem market_data_hub was built to fix).
  - Straddle P&L        : ATM CE+PE premium reconstructed from
    quote_snapshots over a session, with a simulated entry P&L.
  - OI Profile          : per-strike OI over time (quote_snapshots) paired
    with the underlying/futures candle (market_candles) for the same window.

PCR-over-time, Greeks-history, IV-Smile, GEX-per-strike, and the Max-Pain
curve are NOT duplicated here -- they already exist:
  - IV smile / GEX-per-strike / Max-Pain curve: already returned per-strike
    by app.optionchain.analytics.iv_skew()/gex()/max_pain() inside the
    existing GET /api/optionchain/{underlying} response (analytics.iv_skew.
    per_strike, analytics.gex.per_strike, analytics.max_pain.curve) -- these
    only needed a frontend chart, not new backend code.
  - PCR-over-time / Greeks-history: already stored, snapshot by snapshot, in
    greek_exposure (pcr_oi, net_delta_exp, net_gamma_exp, net_theta_exp,
    net_vega_exp columns) and already served read-only by the existing
    GET /api/greeks-engine/exposure endpoint (app/greeks_engine/api.py) --
    likewise only needed a frontend chart.

Every function here is read-only against the shared histcap DB, takes an
injectable `db_path` for tests (same convention as GreeksEngine/HistStore),
and touches no trading/order-execution/broker-credential code at all.
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import asdict, dataclass, field

try:
    from ..histcap.store import DB_PATH as _HIST_DB
except Exception:  # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "market_history.db")


def _conn(db_path: str | None):
    # same override order as app.optionchain.sources.angelone_chain._db_path:
    # explicit arg > env var (read fresh, not import-time-cached -- lets tests
    # monkeypatch CHANAKYA_HIST_DB_PATH) > the shared default.
    path = os.path.abspath(db_path or os.environ.get("CHANAKYA_HIST_DB_PATH") or _HIST_DB)
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    return c


def _n(x):
    try:
        f = float(x)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
#  Volatility Surface -- strike x expiry grid, real captured broker IV        #
# --------------------------------------------------------------------------- #
@dataclass
class VolSurface:
    status: str
    underlying: str = ""
    expiries: list = field(default_factory=list)          # ["08SEP2026", ...]
    points: list = field(default_factory=list)             # [{expiry, strike, ce_iv, pe_iv, as_of}]
    as_of_by_expiry: dict = field(default_factory=dict)     # {expiry: latest snap_key used}
    method: str = ("per (expiry,strike): latest captured broker Greek snapshot "
                    "(option_greeks.iv, broker_status='OK'); no BS solve, no live fetch")

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v not in (None, "")}
        d["points"] = self.points
        d["expiries"] = self.expiries
        return d


def vol_surface(underlying: str, *, db_path: str | None = None,
                 max_expiries: int = 6) -> VolSurface:
    underlying = (underlying or "").upper().strip()
    if not underlying:
        return VolSurface(status="bad_underlying")
    conn = _conn(db_path)
    try:
        expiries = [r["expiry"] for r in conn.execute(
            "SELECT DISTINCT expiry FROM option_greeks WHERE underlying=? AND broker_status='OK' "
            "ORDER BY expiry", (underlying,)).fetchall()]
        if not expiries:
            return VolSurface(status="no_data", underlying=underlying)
        expiries = expiries[:max_expiries]

        points, as_of_by_expiry = [], {}
        for expiry in expiries:
            # latest snap_key actually captured for this expiry
            row = conn.execute(
                "SELECT MAX(snap_key) sk FROM option_greeks WHERE underlying=? AND expiry=? "
                "AND broker_status='OK'", (underlying, expiry)).fetchone()
            snap_key = row["sk"] if row else None
            if not snap_key:
                continue
            as_of_by_expiry[expiry] = snap_key
            for r in conn.execute(
                "SELECT strike, option_type, iv FROM option_greeks "
                "WHERE underlying=? AND expiry=? AND snap_key=? AND broker_status='OK' AND iv IS NOT NULL",
                (underlying, expiry, snap_key)).fetchall():
                points.append({"expiry": expiry, "strike": r["strike"],
                                "option_type": r["option_type"], "iv": round(r["iv"], 5)})
        if not points:
            return VolSurface(status="no_iv", underlying=underlying, expiries=expiries)
        return VolSurface(status="ok", underlying=underlying, expiries=expiries,
                           points=points, as_of_by_expiry=as_of_by_expiry)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Straddle P&L -- ATM CE+PE premium over a session, simulated entry         #
# --------------------------------------------------------------------------- #
@dataclass
class StraddlePnl:
    status: str
    underlying: str = ""
    expiry: str = ""
    strike: float | None = None
    entry_ts: str | None = None
    entry_premium: float | None = None
    series: list = field(default_factory=list)     # [{ts, ce, pe, straddle, pnl}]
    method: str = "ATM CE.ltp + PE.ltp from captured quote_snapshots; pnl = straddle(t) - straddle(entry)"

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["series"] = self.series
        return d


def _resolve_atm_strike(conn, underlying: str, expiry: str, at_ts: str | None) -> float | None:
    """Nearest strike to the underlying's own price around `at_ts` (or the
    earliest available option quote if at_ts is not given), from whichever
    strike's CE+PE both have a quote near that time -- avoids a dependency on
    a separate index/future price row existing at the exact same instant."""
    q = ("SELECT DISTINCT strike FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
         "AND expiry=?")
    strikes = sorted(r["strike"] for r in conn.execute(q, (underlying, expiry)).fetchall())
    if not strikes:
        return None
    # underlying reference price: nearest INDEX/FUTURE quote at/after at_ts, else the first one
    uq = ("SELECT ltp FROM quote_snapshots WHERE symbol=? AND kind IN ('INDEX','FUTURE') "
          + ("AND received_ts>=? " if at_ts else "") + "ORDER BY received_ts ASC LIMIT 1")
    params = (underlying, at_ts) if at_ts else (underlying,)
    urow = conn.execute(uq, params).fetchone()
    ref = _n(urow["ltp"]) if urow else None
    if ref is None:
        return strikes[len(strikes) // 2]
    return min(strikes, key=lambda s: abs(s - ref))


def straddle_pnl(underlying: str, expiry: str, *, db_path: str | None = None,
                  entry_ts: str | None = None, strike: float | None = None,
                  limit: int = 5000) -> StraddlePnl:
    underlying = (underlying or "").upper().strip()
    if not underlying or not expiry:
        return StraddlePnl(status="bad_params")
    conn = _conn(db_path)
    try:
        atm = strike if strike is not None else _resolve_atm_strike(conn, underlying, expiry, entry_ts)
        if atm is None:
            return StraddlePnl(status="no_data", underlying=underlying, expiry=expiry)

        rows = conn.execute(
            "SELECT received_ts, option_type, ltp FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? "
            + ("AND received_ts>=? " if entry_ts else "")
            + "ORDER BY received_ts ASC LIMIT ?",
            (underlying, expiry, atm) + ((entry_ts,) if entry_ts else ()) + (limit,)
        ).fetchall()
        if not rows:
            return StraddlePnl(status="no_data", underlying=underlying, expiry=expiry, strike=atm)

        by_ts: dict[str, dict] = {}
        for r in rows:
            d = by_ts.setdefault(r["received_ts"], {"ts": r["received_ts"]})
            if r["option_type"] == "CE":
                d["ce"] = _n(r["ltp"])
            elif r["option_type"] == "PE":
                d["pe"] = _n(r["ltp"])

        series = []
        entry_straddle = None
        for ts in sorted(by_ts):
            d = by_ts[ts]
            ce, pe = d.get("ce"), d.get("pe")
            if ce is None or pe is None:
                continue
            straddle = round(ce + pe, 2)
            if entry_straddle is None:
                entry_straddle = straddle
            series.append({"ts": ts, "ce": ce, "pe": pe, "straddle": straddle,
                            "pnl": round(straddle - entry_straddle, 2)})
        if not series:
            return StraddlePnl(status="no_overlap", underlying=underlying, expiry=expiry, strike=atm)

        return StraddlePnl(status="ok", underlying=underlying, expiry=expiry, strike=atm,
                            entry_ts=series[0]["ts"], entry_premium=series[0]["straddle"],
                            series=series)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  OI Profile -- per-strike OI over time + the underlying's own candle       #
# --------------------------------------------------------------------------- #
@dataclass
class OiProfile:
    status: str
    underlying: str = ""
    expiry: str = ""
    strikes: list = field(default_factory=list)         # sorted strikes covered
    oi_series: list = field(default_factory=list)        # [{ts, strike, ce_oi, pe_oi}]
    underlying_candles: list = field(default_factory=list)  # [{ts, o,h,l,c}] futures/index
    method: str = ("per-strike OI over time from quote_snapshots (kind=OPTION); "
                   "underlying candle from market_candles (kind=FUTURE, else INDEX)")

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["oi_series"], d["underlying_candles"] = self.oi_series, self.underlying_candles
        return d


def oi_profile(underlying: str, expiry: str, *, db_path: str | None = None,
               top_n_strikes: int = 8, limit_per_strike: int = 1000) -> OiProfile:
    underlying = (underlying or "").upper().strip()
    if not underlying or not expiry:
        return OiProfile(status="bad_params")
    conn = _conn(db_path)
    try:
        latest_oi = conn.execute(
            "SELECT strike, MAX(oi) mx FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND expiry=? AND oi IS NOT NULL "
            "GROUP BY strike ORDER BY mx DESC LIMIT ?",
            (underlying, expiry, top_n_strikes)).fetchall()
        strikes = sorted(r["strike"] for r in latest_oi)
        if not strikes:
            return OiProfile(status="no_data", underlying=underlying, expiry=expiry)

        oi_series = []
        for strike in strikes:
            rows = conn.execute(
                "SELECT received_ts, option_type, oi FROM quote_snapshots "
                "WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? AND oi IS NOT NULL "
                "ORDER BY received_ts ASC LIMIT ?",
                (underlying, expiry, strike, limit_per_strike)).fetchall()
            by_ts: dict[str, dict] = {}
            for r in rows:
                d = by_ts.setdefault(r["received_ts"], {"ts": r["received_ts"], "strike": strike})
                d[f"{r['option_type'].lower()}_oi"] = _n(r["oi"])
            oi_series.extend(by_ts[ts] for ts in sorted(by_ts))

        cand = conn.execute(
            "SELECT bar_start ts, o, h, l, c FROM market_candles "
            "WHERE symbol=? AND kind='FUTURE' AND tf='1m' ORDER BY bar_start ASC LIMIT ?",
            (underlying, limit_per_strike)).fetchall()
        if not cand:
            cand = conn.execute(
                "SELECT bar_start ts, o, h, l, c FROM market_candles "
                "WHERE symbol=? AND kind='INDEX' AND tf='1m' ORDER BY bar_start ASC LIMIT ?",
                (underlying, limit_per_strike)).fetchall()
        underlying_candles = [{"ts": r["ts"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"]}
                               for r in cand]

        return OiProfile(status="ok", underlying=underlying, expiry=expiry, strikes=strikes,
                          oi_series=oi_series, underlying_candles=underlying_candles)
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
#  Volume Spike Ratio -- today's real captured option volume vs the mean of   #
#  N prior REAL captured sessions (checked against Quantech-innovation/       #
#  options-flow-ml-workstation's README, which computes this on SYNTHETIC     #
#  simulated volume over a 10-day rolling window; this version only ever      #
#  uses real captured quote_snapshots rows -- `n_days_in_average` is always   #
#  returned explicitly so a caller can see exactly how thin the real history  #
#  actually is, never silently padded to look like a full N-day average.)     #
# --------------------------------------------------------------------------- #
@dataclass
class VolumeSpike:
    status: str
    underlying: str = ""
    today_session: str | None = None
    today_volume: float | None = None
    avg_volume: float | None = None
    ratio: float | None = None
    n_days_in_average: int = 0
    session_dates_used: list = field(default_factory=list)
    method: str = ("today's total captured option-chain volume (max cumulative reading per "
                   "strike/option_type per session, summed across the chain) vs the plain mean of "
                   "the prior REAL captured sessions -- never padded or fabricated to reach a target "
                   "lookback length")

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["session_dates_used"] = self.session_dates_used
        return d


def volume_spike_ratio(underlying: str, *, db_path: str | None = None, lookback_days: int = 10) -> VolumeSpike:
    underlying = (underlying or "").upper().strip()
    if not underlying:
        return VolumeSpike(status="bad_underlying")
    conn = _conn(db_path)
    try:
        # volume is cumulative WITHIN a session (same convention documented in
        # app.backtest.oi_history_adapter's own vol_delta logic) -- the latest
        # (max) reading per strike/option_type IS that session's real total.
        rows = conn.execute(
            "SELECT session_date_ist, strike, option_type, MAX(volume) v FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND volume IS NOT NULL AND session_date_ist IS NOT NULL "
            "GROUP BY session_date_ist, strike, option_type", (underlying,)).fetchall()
        if not rows:
            return VolumeSpike(status="no_data", underlying=underlying)

        by_session: dict[str, float] = {}
        for r in rows:
            by_session[r["session_date_ist"]] = by_session.get(r["session_date_ist"], 0.0) + (r["v"] or 0.0)
        dates = sorted(by_session.keys())
        if len(dates) < 2:
            return VolumeSpike(status="insufficient_history", underlying=underlying,
                               today_session=dates[-1] if dates else None,
                               today_volume=by_session.get(dates[-1]) if dates else None)

        today = dates[-1]
        prior = dates[max(0, len(dates) - 1 - lookback_days):-1]
        today_vol = by_session[today]
        avg_vol = sum(by_session[d] for d in prior) / len(prior) if prior else None
        ratio = round(today_vol / avg_vol, 3) if avg_vol else None
        return VolumeSpike(status="ok", underlying=underlying, today_session=today, today_volume=today_vol,
                           avg_volume=round(avg_vol, 1) if avg_vol is not None else None, ratio=ratio,
                           n_days_in_average=len(prior), session_dates_used=prior + [today])
    finally:
        conn.close()
