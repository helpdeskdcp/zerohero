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
import math
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from ..histcap import read_contract as rc

try:
    from ..histcap.store import DB_PATH as _HIST_DB
except Exception:  # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "data", "market_history.db")


def _conn(db_path: str | None, provider: str, as_of: str):
    # same override order as app.optionchain.sources.angelone_chain._db_path:
    # explicit arg > env var (read fresh, not import-time-cached -- lets tests
    # monkeypatch CHANAKYA_HIST_DB_PATH) > the shared default.
    path = os.path.abspath(db_path or os.environ.get("CHANAKYA_HIST_DB_PATH") or _HIST_DB)
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    c.row_factory = sqlite3.Row
    def valid_contract_time(kind, expiry, timestamp):
        try:
            day = rc.instant(timestamp).astimezone(rc.IST).date()
            return kind not in ('OPTION', 'FUTURE') or day <= rc.expiry_date(expiry)
        except (ValueError, TypeError, AttributeError):
            return False
    c.create_function('valid_contract_time', 3, valid_contract_time, deterministic=True)
    c.execute("CREATE TEMP TABLE read_policy(quote_source TEXT, greek_source TEXT, candle_source TEXT, cutoff TEXT)")
    c.execute("INSERT INTO read_policy VALUES (?,?,?,?)", (rc.SOURCES[provider]["quotes"],
              provider + "_OPTION_GREEK", rc.SOURCES[provider]["candles"], as_of))
    for table, source_field, timestamp in (("quote_snapshots", "quote_source", "received_ts"),
            ("option_greeks", "greek_source", "received_ts"), ("market_candles", "candle_source", "bar_start")):
        cols = {r[1] for r in c.execute(f"PRAGMA main.table_info({table})")}
        if not cols:
            continue
        clauses = [f"source=(SELECT {source_field} FROM read_policy)" if "source" in cols else "0",
                   f"julianday({timestamp})<=julianday((SELECT cutoff FROM read_policy))"]
        for time in ("exch_ts", "server_ts"):
            if time in cols:
                clauses.append(f"({time} IS NULL OR julianday({time})<=julianday((SELECT cutoff FROM read_policy)))")
                clauses.append(f"({time} IS NULL OR julianday({time})<=julianday(received_ts))")
        if table == "market_candles":
            # This reader's underlying overlay is explicitly one-minute bars.
            clauses.append("julianday(bar_start,'+1 minute')<=julianday((SELECT cutoff FROM read_policy))")
        if 'expiry' in cols:
            kind = 'kind' if 'kind' in cols else "'OPTION'"
            clauses.append(f"valid_contract_time({kind},expiry,{timestamp})")
        c.execute(f"CREATE TEMP VIEW {table} AS SELECT * FROM main.{table} WHERE " + " AND ".join(clauses))
    return c


def _n(x):
    try:
        f = float(x)
        return f if math.isfinite(f) else None
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
    provenance: dict = field(default_factory=dict)
    method: str = ("per (expiry,strike): latest captured broker Greek snapshot "
                    "(option_greeks.iv, broker_status='OK'); no BS solve, no live fetch")

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v not in (None, "")}
        d["points"] = self.points
        d["expiries"] = self.expiries
        return d


def _vol_surface(underlying: str, *, db_path: str | None = None,
                 max_expiries: int = 6, provider, as_of) -> VolSurface:
    underlying = (underlying or "").upper().strip()
    if not underlying:
        return VolSurface(status="bad_underlying")
    conn = _conn(db_path, provider, as_of)
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
                "SELECT snap_key sk FROM option_greeks WHERE underlying=? AND expiry=? "
                "AND broker_status='OK' ORDER BY julianday(received_ts) DESC, id DESC LIMIT 1", (underlying, expiry)).fetchone()
            snap_key = row["sk"] if row else None
            if not snap_key:
                continue
            as_of_by_expiry[expiry] = snap_key
            for r in conn.execute(
                "SELECT id, received_ts, strike, option_type, iv FROM option_greeks "
                "WHERE underlying=? AND expiry=? AND snap_key=? AND broker_status='OK' AND iv IS NOT NULL",
                (underlying, expiry, snap_key)).fetchall():
                iv = _n(r["iv"])
                if iv is None or iv <= 0:
                    continue
                points.append({"expiry": expiry, "strike": r["strike"], "greek_id": r["id"],
                                "timestamp": r["received_ts"], "option_type": r["option_type"], "iv": round(iv, 5)})
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
    provenance: dict = field(default_factory=dict)
    method: str = "ATM CE.ltp + PE.ltp from captured quote_snapshots; pnl = straddle(t) - straddle(entry)"

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["series"] = self.series
        return d


def _resolve_atm_strike(conn, underlying: str, expiry: str, at_ts: str | None) -> tuple | None:
    """ATM from provider-matched underlying and strikes known at entry only."""
    if at_ts is None:
        row = conn.execute("SELECT received_ts FROM quote_snapshots WHERE symbol=? AND kind='OPTION' "
            "AND expiry=? ORDER BY julianday(received_ts), id LIMIT 1", (underlying, expiry)).fetchone()
        at_ts = row[0] if row else None
    if at_ts is None:
        return None
    q = ("SELECT strike FROM quote_snapshots WHERE symbol=? AND kind='OPTION' AND expiry=? "
         "AND julianday(received_ts)<=julianday(?) AND ltp>0 AND option_type IN ('CE','PE') "
         "GROUP BY strike HAVING COUNT(DISTINCT option_type)=2")
    strikes = sorted(r["strike"] for r in conn.execute(q, (underlying, expiry, at_ts)).fetchall())
    if not strikes:
        return None
    for kind in ('INDEX', 'FUTURE'):
        rows = conn.execute("SELECT * FROM quote_snapshots WHERE symbol=? AND kind=? "
            "AND julianday(received_ts)<=julianday(?) AND julianday(received_ts)>=julianday(?)-90/86400.0 "
            "ORDER BY julianday(received_ts) DESC, id DESC", (underlying, kind, at_ts, at_ts)).fetchall()
        valid = [dict(r) for r in rows if _n(r["ltp"]) is not None and r["ltp"] > 0]
        if kind == 'FUTURE':
            valid = [r for r in valid if r.get('expiry') and
                     rc.expiry_date(r['expiry']) >= rc.instant(at_ts).astimezone(rc.IST).date()]
        if len({(r.get('instrument_key'), r.get('exchange'), r.get('expiry')) for r in valid}) == 1:
            return min(strikes, key=lambda s: abs(s - valid[0]['ltp'])), valid[0]
    return None


def _straddle_pnl(underlying: str, expiry: str, *, db_path: str | None = None,
                  entry_ts: str | None = None, strike: float | None = None,
                  limit: int = 5000, provider, as_of) -> StraddlePnl:
    underlying = (underlying or "").upper().strip()
    if not underlying or not expiry:
        return StraddlePnl(status="bad_params")
    conn = _conn(db_path, provider, as_of)
    try:
        selection = (strike, None) if strike is not None else _resolve_atm_strike(conn, underlying, expiry, entry_ts)
        if selection is None:
            return StraddlePnl(status="no_data", underlying=underlying, expiry=expiry)
        atm, underlying_quote = selection
        start = None
        if entry_ts:
            baseline = conn.execute("SELECT MIN(received_ts) FROM quote_snapshots "
                "WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? "
                "AND julianday(received_ts)<=julianday(?) AND ltp>0 AND option_type IN ('CE','PE') "
                "GROUP BY julianday(received_ts) HAVING COUNT(DISTINCT option_type)=2 "
                "ORDER BY julianday(received_ts) DESC LIMIT 1", (underlying, expiry, atm, entry_ts)).fetchone()
            if baseline is None:
                return StraddlePnl(status="no_data", underlying=underlying, expiry=expiry, strike=atm)
            start = baseline[0]
        # Choose the entry before limiting the forward observation series.
        rows = conn.execute(
            "SELECT id, received_ts, option_type, ltp FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND expiry=? AND strike=? "
            + ("AND julianday(received_ts)>=julianday(?) " if start else "")
            + "ORDER BY julianday(received_ts) ASC, id ASC LIMIT ?",
            (underlying, expiry, atm) + ((start,) if start else ()) + (limit,)
        ).fetchall()
        if not rows:
            return StraddlePnl(status="no_data", underlying=underlying, expiry=expiry, strike=atm)

        by_ts: dict[str, dict] = {}
        for r in rows:
            ts = rc.instant(r["received_ts"]).isoformat().replace('+00:00', 'Z')
            d = by_ts.setdefault(ts, {"ts": ts})
            if r["option_type"] == "CE":
                d["ce"] = _n(r["ltp"])
                d['ce_quote_id'] = r['id']
            elif r["option_type"] == "PE":
                d["pe"] = _n(r["ltp"])
                d['pe_quote_id'] = r['id']

        if entry_ts:
            past = [ts for ts, d in by_ts.items() if rc.instant(ts) <= rc.instant(entry_ts)
                    and d.get('ce') is not None and d.get('pe') is not None
                    and d['ce'] > 0 and d['pe'] > 0]
            if not past:
                return StraddlePnl(status="no_data", underlying=underlying, expiry=expiry, strike=atm)
            first = max(past)
            by_ts = {ts: d for ts, d in by_ts.items() if ts >= first}
        series = []
        entry_straddle = None
        for ts in sorted(by_ts):
            d = by_ts[ts]
            ce, pe = d.get("ce"), d.get("pe")
            if ce is None or pe is None or ce <= 0 or pe <= 0:
                continue
            straddle = round(ce + pe, 2)
            if entry_straddle is None:
                entry_straddle = straddle
            series.append({"ts": ts, "ce": ce, "pe": pe, "straddle": straddle,
                            "ce_quote_id": d['ce_quote_id'], "pe_quote_id": d['pe_quote_id'],
                            "pnl": round(straddle - entry_straddle, 2)})
        if not series:
            return StraddlePnl(status="no_overlap", underlying=underlying, expiry=expiry, strike=atm)

        return StraddlePnl(status="ok", underlying=underlying, expiry=expiry, strike=atm,
                            entry_ts=series[0]["ts"], entry_premium=series[0]["straddle"],
                            provenance={'underlying_quote': {k: underlying_quote.get(k) for k in
                                ('id', 'source', 'instrument_key', 'expiry', 'exchange', 'received_ts', 'exch_ts')}
                                if underlying_quote else {'reason': 'EXPLICIT_STRIKE'}},
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
    provenance: dict = field(default_factory=dict)
    method: str = ("per-strike OI over time from quote_snapshots (kind=OPTION); "
                   "underlying candle from market_candles (kind=FUTURE, else INDEX)")

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["oi_series"], d["underlying_candles"] = self.oi_series, self.underlying_candles
        return d


def _oi_profile(underlying: str, expiry: str, *, db_path: str | None = None,
               top_n_strikes: int = 8, limit_per_strike: int = 1000, provider, as_of) -> OiProfile:
    underlying = (underlying or "").upper().strip()
    if not underlying or not expiry:
        return OiProfile(status="bad_params")
    conn = _conn(db_path, provider, as_of)
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
                "ORDER BY julianday(received_ts) ASC, id ASC LIMIT ?",
                (underlying, expiry, strike, limit_per_strike)).fetchall()
            by_ts: dict[str, dict] = {}
            for r in rows:
                if _n(r["oi"]) is None or r["oi"] < 0:
                    continue
                ts = rc.instant(r["received_ts"]).isoformat().replace('+00:00', 'Z')
                d = by_ts.setdefault(ts, {"ts": ts, "strike": strike})
                d[f"{r['option_type'].lower()}_oi"] = _n(r["oi"])
            oi_series.extend(by_ts[ts] for ts in sorted(by_ts))

        if not oi_series:
            return OiProfile(status="no_data", underlying=underlying, expiry=expiry)
        underlying_candles = []
        if oi_series:
            start, end = min(r['ts'] for r in oi_series), max(r['ts'] for r in oi_series)
            for kind in ('FUTURE', 'INDEX'):
                cand = conn.execute("SELECT * FROM market_candles WHERE symbol=? AND kind=? AND tf='1m' "
                    "AND julianday(bar_start)>=julianday(?) AND julianday(bar_start,'+1 minute')<=julianday(?) "
                    "ORDER BY julianday(bar_start), id LIMIT ?", (underlying, kind, start, end, limit_per_strike)).fetchall()
                by_time = {}
                for raw in cand:
                    r = dict(raw)
                    r.update(ts=r['bar_start'], series_type='UNDERLYING_SESSION')
                    by_time.setdefault(rc.instant(r['bar_start']), r)
                if by_time:
                    underlying_candles = list(by_time.values())
                    break

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
    expiries: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)
    method: str = ("today's total captured option-chain volume (max cumulative reading per "
                   "strike/option_type per session, summed across the chain) vs the plain mean of "
                   "the prior REAL captured sessions -- never padded or fabricated to reach a target "
                   "lookback length")

    def to_dict(self):
        d = {k: v for k, v in asdict(self).items() if v is not None}
        d["session_dates_used"] = self.session_dates_used
        return d


def _volume_spike_ratio(underlying: str, *, db_path: str | None = None, lookback_days: int = 10,
                        provider, as_of) -> VolumeSpike:
    underlying = (underlying or "").upper().strip()
    if not underlying:
        return VolumeSpike(status="bad_underlying")
    conn = _conn(db_path, provider, as_of)
    try:
        # volume is cumulative WITHIN a session (same convention documented in
        # app.backtest.oi_history_adapter's own vol_delta logic) -- the latest
        # (max) reading per strike/option_type IS that session's real total.
        rows = conn.execute(
            "SELECT session_date_ist, expiry, strike, option_type, MAX(volume) v FROM quote_snapshots "
            "WHERE symbol=? AND kind='OPTION' AND volume IS NOT NULL AND session_date_ist IS NOT NULL "
            "GROUP BY session_date_ist, expiry, strike, option_type", (underlying,)).fetchall()
        if not rows:
            return VolumeSpike(status="no_data", underlying=underlying)
        if any(_n(r['v']) is None or r['v'] < 0 or not r['expiry'] for r in rows):
            return VolumeSpike(status="invalid_data", underlying=underlying)

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
                           expiries=sorted({r['expiry'] for r in rows}),
                           n_days_in_average=len(prior), session_dates_used=prior + [today])
    finally:
        conn.close()


def _provider_read(reader, model, source_kind, underlying, *args, provider=None, as_of=None, **kwargs):
    """One complete analytical result per provider; never merge fallback fields."""
    try:
        cutoff = rc.instant(as_of or datetime.now(timezone.utc)).isoformat()
        order = rc.providers(provider)
        if kwargs.get('entry_ts') and rc.instant(kwargs['entry_ts']) > rc.instant(cutoff):
            raise ValueError('entry after as_of')
    except (ValueError, TypeError):
        return model(status='bad_params', underlying=underlying,
                     provenance={'status': 'UNAVAILABLE', 'reason': 'INVALID_REQUEST'})
    attempts = []
    for p in order:
        try:
            data = reader(underlying, *args, provider=p, as_of=cutoff, **kwargs)
        except (sqlite3.Error, ValueError, TypeError):
            data = model(status='no_data', underlying=underlying)
        source = p + '_OPTION_GREEK' if source_kind == 'greeks' else rc.SOURCES[p][source_kind]
        fallback = provider is None and p == 'ANGELONE'
        meta = {'provider': p, 'source': source, 'underlying': (underlying or '').upper(), 'as_of': cutoff,
                'fallback_used': fallback, 'fallback_reason': attempts[0]['reason'] if fallback and attempts else None}
        if args:
            meta['expiry'] = args[0]
        data.provenance = {**data.provenance, **meta, 'attempts': list(attempts), 'requested_provider': provider}
        if data.status == 'ok':
            for name in ('points', 'series', 'oi_series'):
                for r in getattr(data, name, []):
                    r.update(meta)
            for r in getattr(data, 'underlying_candles', []):
                r.update({k: v for k, v in meta.items() if k != 'expiry'},
                         source=rc.SOURCES[p]['candles'])
            return data
        attempts.append({'provider': p, 'reason': data.status})
    data.provenance.update(status='UNAVAILABLE', reason=data.status, attempts=attempts)
    return data


def vol_surface(underlying: str, *, db_path=None, max_expiries=6, provider=None, as_of=None) -> VolSurface:
    """Explicit multi-expiry grid from one provider, bounded by as_of."""
    return _provider_read(_vol_surface, VolSurface, 'greeks', underlying, provider=provider,
                          as_of=as_of, db_path=db_path, max_expiries=max_expiries)


def straddle_pnl(underlying: str, expiry: str, *, db_path=None, entry_ts=None, strike=None,
                  limit=5000, provider=None, as_of=None) -> StraddlePnl:
    """Post-entry P&L through as_of; entry inputs are known at/before entry."""
    return _provider_read(_straddle_pnl, StraddlePnl, 'quotes', underlying, expiry, provider=provider,
                          as_of=as_of, db_path=db_path, entry_ts=entry_ts, strike=strike, limit=limit)


def oi_profile(underlying: str, expiry: str, *, db_path=None, top_n_strikes=8,
               limit_per_strike=1000, provider=None, as_of=None) -> OiProfile:
    return _provider_read(_oi_profile, OiProfile, 'quotes', underlying, expiry, provider=provider,
                          as_of=as_of, db_path=db_path, top_n_strikes=top_n_strikes, limit_per_strike=limit_per_strike)


def volume_spike_ratio(underlying: str, *, db_path=None, lookback_days=10,
                       provider=None, as_of=None) -> VolumeSpike:
    """Explicit whole-chain volume aggregate with separate expiry identities."""
    return _provider_read(_volume_spike_ratio, VolumeSpike, 'quotes', underlying, provider=provider,
                          as_of=as_of, db_path=db_path, lookback_days=lookback_days)
