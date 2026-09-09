"""
PRIMARY source -- assemble the option chain from data IDaddy already captures.

Reads `market_history.db` READ-ONLY. Three captured tables, three roles:

  * `option_greeks`   (snap_key-batched, ~40s)  -> FULL-CHAIN delta / gamma /
                       theta / vega / iv / iv_pct / trade_volume.  No price, no OI.
                       This is the spine of the assembled chain.
  * `quote_snapshots` (kind='OPTION')           -> ltp / bid / ask / bid_qty /
                       ask_qty / oi / volume.  The live capture only follows a
                       drifting ~ATM band (a couple of legs per poll), so this
                       overlays the strikes NEAR the money, fresh.  `oi_change`
                       is ALWAYS NULL here (AngelOne's quote feed has no
                       change-in-OI field) -> we derive it as current_oi minus
                       the previous session's closing OI per contract.
  * `greek_exposure.per_strike_json`            -> per-strike CE/PE OI across the
                       WHOLE chain, plus pcr_oi / ce_oi_total / pe_oi_total.
                       Emitted by the greek engine and can lag -- used to fill OI
                       on the wings, always tagged with its own as-of stamp.
  * `quote_snapshots` (kind='INDEX', else 'FUTURE' for MCX) -> spot.

No network. No writes. CE/PE-pairing concept adapted from
markov404/AngelOneOptionChainSmartApi (MIT).
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
from datetime import datetime, timezone

from ..chain import OptionChain, StrikeRow, OptionLeg

try:
    from ...histcap.store import DB_PATH as _HIST_DB
except Exception:                                       # pragma: no cover
    _HIST_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))))), "data", "market_history.db")

# how far back from the reference stamp a `quote_snapshots` OPTION row may be and
# still count as "the current book" for its strike
_QUOTE_WINDOW_MIN = 25
# a `greek_exposure` per-strike-OI snapshot older than this (vs the reference
# stamp) is still used but flagged stale
_OI_STALE_SEC = 900


def _db_path(override: str | None) -> str:
    return override or os.environ.get("CHANAKYA_HIST_DB_PATH") or _HIST_DB


def _ro(path: str):
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def _num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _epoch(x):
    try:
        return datetime.fromisoformat(str(x).replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return None


def _expiry_date(x):
    for f in ("%d%b%Y", "%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(x).upper(), f).date()
        except ValueError:
            pass
    return None


def _pick_expiry(con, underlying: str, want: str) -> str | None:
    rows = con.execute(
        "SELECT DISTINCT expiry FROM quote_snapshots "
        "WHERE symbol=? AND kind='OPTION' AND expiry IS NOT NULL AND expiry!='' "
        "UNION SELECT DISTINCT expiry FROM option_greeks "
        "WHERE underlying=? AND expiry IS NOT NULL AND expiry!=''",
        (underlying, underlying)).fetchall()
    today = datetime.now(timezone.utc).date()
    dated = sorted({(d, r["expiry"]) for r in rows
                    if (d := _expiry_date(r["expiry"])) and d >= today})
    if not dated:
        alld = sorted({(d, r["expiry"]) for r in rows if (d := _expiry_date(r["expiry"]))})
        return alld[-1][1] if alld else None
    w = str(want or "AUTO").upper()
    if w in ("AUTO", "CURRENT"):
        return dated[0][1]
    if w == "NEXT":
        return dated[1][1] if len(dated) > 1 else dated[0][1]
    if w == "LATEST":
        return dated[-1][1]
    for _, e in dated:
        if e.upper() == w:
            return e
    return dated[0][1]


# --------------------------------------------------------------------------- #
#  spine: option_greeks, one snap_key                                          #
# --------------------------------------------------------------------------- #
def _latest_greek_snap(con, u: str, exp: str, at_ts: str | None) -> str | None:
    q = ("SELECT MAX(snap_key) FROM option_greeks WHERE underlying=? AND expiry=?")
    args = [u, exp]
    if at_ts:
        q += " AND received_ts<=?"
        args.append(at_ts)
    row = con.execute(q, args).fetchone()
    return row[0] if row and row[0] else None


def _greek_spine(con, u: str, exp: str, snap_key: str) -> tuple[dict, str | None]:
    rows_by_k: dict[float, StrikeRow] = {}
    max_rts = None
    for r in con.execute(
        "SELECT received_ts, strike, option_type, delta, gamma, theta, vega, iv, "
        "iv_pct, trade_volume FROM option_greeks "
        "WHERE underlying=? AND expiry=? AND snap_key=? AND strike IS NOT NULL",
        (u, exp, snap_key)):
        k = _num(r["strike"])
        if k is None:
            continue
        if max_rts is None or (r["received_ts"] or "") > max_rts:
            max_rts = r["received_ts"]
        leg = OptionLeg(
            delta=_num(r["delta"]), gamma=_num(r["gamma"]), theta=_num(r["theta"]),
            vega=_num(r["vega"]), iv=_num(r["iv"]), volume=_num(r["trade_volume"]))
        row = rows_by_k.setdefault(k, StrikeRow(strike=k))
        if str(r["option_type"]).upper() == "CE":
            row.ce = leg
        else:
            row.pe = leg
    return rows_by_k, max_rts


# --------------------------------------------------------------------------- #
#  near-money overlay: quote_snapshots OPTION, latest row per leg              #
# --------------------------------------------------------------------------- #
def _quote_overlay(con, u: str, exp: str, ref_ts: str | None) -> dict:
    ref = ref_ts or datetime.now(timezone.utc).isoformat()
    out: dict[tuple, sqlite3.Row] = {}
    for r in con.execute(
        "SELECT q.strike, q.option_type, q.ltp, q.bid, q.ask, q.bid_qty, q.ask_qty, "
        "       q.oi, q.oi_change, q.volume, q.received_ts "
        "FROM quote_snapshots q JOIN ("
        "  SELECT strike, option_type, MAX(received_ts) mts FROM quote_snapshots "
        "  WHERE symbol=? AND kind='OPTION' AND expiry=? AND received_ts<=? "
        "    AND received_ts>=datetime(?, ?) "
        "  GROUP BY strike, option_type"
        ") m ON q.strike=m.strike AND q.option_type=m.option_type "
        "     AND q.received_ts=m.mts "
        "WHERE q.symbol=? AND q.kind='OPTION' AND q.expiry=?",
        (u, exp, ref, ref, f"-{_QUOTE_WINDOW_MIN} minutes", u, exp)):
        k = _num(r["strike"])
        if k is None:
            continue
        out[(k, str(r["option_type"]).upper())] = r
    return out


# --------------------------------------------------------------------------- #
#  wing overlay: greek_exposure.per_strike_json                               #
# --------------------------------------------------------------------------- #
def _oi_overlay(con, u: str, exp: str, at_ts: str | None) -> tuple[dict, dict]:
    q = ("SELECT as_of_ts, underlying_price, pcr_oi, ce_oi_total, pe_oi_total, "
         "per_strike_json FROM greek_exposure "
         "WHERE underlying=? AND expiry=? AND per_strike_json IS NOT NULL "
         "  AND per_strike_json NOT IN ('', '[]')")
    args = [u, exp]
    if at_ts:
        q += " AND as_of_ts<=?"
        args.append(at_ts)
    q += " ORDER BY id DESC LIMIT 1"
    row = con.execute(q, args).fetchone()
    if not row:
        return {}, {}
    try:
        arr = json.loads(row["per_strike_json"])
    except (ValueError, TypeError):
        return {}, {}
    oi: dict[tuple, float] = {}
    for it in arr if isinstance(arr, list) else []:
        k = _num((it or {}).get("strike"))
        if k is None:
            continue
        for side in ("ce", "pe"):
            node = (it or {}).get(side) or {}
            v = _num(node.get("oi"))
            if v is not None:
                oi[(k, side.upper())] = v
    meta = {"as_of_ts": row["as_of_ts"], "underlying_price": _num(row["underlying_price"]),
            "pcr_oi": _num(row["pcr_oi"]), "ce_oi_total": _num(row["ce_oi_total"]),
            "pe_oi_total": _num(row["pe_oi_total"])}
    return oi, meta


def _prev_session_oi(con, u: str, exp: str) -> tuple[dict, str | None]:
    """{(strike, CE|PE): oi} from the LAST snapshot of the most recent session
    strictly before the current one. AngelOne's quote feed carries no
    change-in-OI field, so the standard 'Chng in OI' column is derived as
    current_oi - this baseline."""
    today = con.execute(
        "SELECT MAX(session_date_ist) FROM quote_snapshots "
        "WHERE symbol=? AND kind='OPTION' AND expiry=?", (u, exp)).fetchone()
    today = today[0] if today else None
    if not today:
        return {}, None
    prev = con.execute(
        "SELECT MAX(session_date_ist) FROM quote_snapshots "
        "WHERE symbol=? AND kind='OPTION' AND expiry=? AND session_date_ist<? "
        "AND oi IS NOT NULL", (u, exp, today)).fetchone()
    prev = prev[0] if prev and prev[0] else None
    if not prev:
        return {}, None
    out: dict[tuple, float] = {}
    for r in con.execute(
        "SELECT q.strike, q.option_type, q.oi FROM quote_snapshots q JOIN ("
        "  SELECT strike, option_type, MAX(received_ts) mts FROM quote_snapshots "
        "  WHERE symbol=? AND kind='OPTION' AND expiry=? AND session_date_ist=? "
        "    AND oi IS NOT NULL GROUP BY strike, option_type"
        ") m ON q.strike=m.strike AND q.option_type=m.option_type AND q.received_ts=m.mts "
        "WHERE q.symbol=? AND q.kind='OPTION' AND q.expiry=?",
        (u, exp, prev, u, exp)):
        k = _num(r["strike"])
        if k is not None and _num(r["oi"]) is not None:
            out[(k, str(r["option_type"]).upper())] = _num(r["oi"])
    return out, prev


def _cadence_sec(con, u: str, exp: str) -> float | None:
    ts = [r[0] for r in con.execute(
        "SELECT DISTINCT snap_key FROM option_greeks "
        "WHERE underlying=? AND expiry=? ORDER BY snap_key DESC LIMIT 40", (u, exp))]
    if len(ts) < 3:
        return None
    gaps = [a - b for a, b in ((_epoch(ts[i]), _epoch(ts[i + 1])) for i in range(len(ts) - 1))
            if a and b and 0 < a - b < 3600]
    return round(statistics.median(gaps), 1) if gaps else None


def fetch(underlying: str, expiry: str = "AUTO", *, db_path: str | None = None,
          at_ts: str | None = None) -> OptionChain | None:
    u = str(underlying or "").upper()
    path = _db_path(db_path)
    if not os.path.exists(path):
        return None
    con = _ro(path)
    try:
        exp = _pick_expiry(con, u, expiry)
        if not exp:
            return None

        snap = _latest_greek_snap(con, u, exp, at_ts)
        notes = [f"expiry '{expiry}' -> {exp}"]
        if snap:
            rows_by_k, spine_ts = _greek_spine(con, u, exp, snap)
            ref_ts = spine_ts or at_ts
            notes.append(f"greek spine snap_key={snap} ({len(rows_by_k)} strikes)")
        else:
            rows_by_k, ref_ts = {}, at_ts
            notes.append("no option_greeks capture -> thin quote-only chain")

        # near-money price/oi overlay from the live quote capture
        q_ov = _quote_overlay(con, u, exp, ref_ts)
        n_ltp = 0
        for (k, side), r in q_ov.items():
            row = rows_by_k.get(k)
            if row is None:
                row = rows_by_k.setdefault(k, StrikeRow(strike=k))
            leg = getattr(row, side.lower())
            if leg is None:
                leg = OptionLeg()
                setattr(row, side.lower(), leg)
            leg.ltp = _num(r["ltp"]) if leg.ltp is None else leg.ltp
            leg.bid, leg.ask = _num(r["bid"]), _num(r["ask"])
            leg.bid_qty, leg.ask_qty = _num(r["bid_qty"]), _num(r["ask_qty"])
            if _num(r["oi"]) is not None:
                leg.oi = _num(r["oi"])
            if _num(r["oi_change"]) is not None:
                leg.oi_change = _num(r["oi_change"])
            if _num(r["volume"]) is not None:
                leg.volume = _num(r["volume"])
            if leg.ltp is not None:
                n_ltp += 1

        # wing OI overlay from the greek engine's per-strike snapshot
        oi_ov, oi_meta = _oi_overlay(con, u, exp, at_ts)
        n_oi_filled = 0
        for (k, side), v in oi_ov.items():
            row = rows_by_k.get(k)
            if row is None:
                continue
            leg = getattr(row, side.lower())
            if leg is not None and leg.oi is None:
                leg.oi = v
                n_oi_filled += 1
        oi_stale = False
        if oi_meta.get("as_of_ts") and ref_ts:
            a, b = _epoch(oi_meta["as_of_ts"]), _epoch(ref_ts)
            oi_stale = bool(a and b and (b - a) > _OI_STALE_SEC)

        # ---- change-in-OI: broker sends none, so derive current_oi - the last
        # OI of the previous session (the standard 'Chng in OI' column) ----
        prev_oi, prev_date = _prev_session_oi(con, u, exp)
        n_doi = 0
        for k, row in rows_by_k.items():
            for side, leg in (("CE", row.ce), ("PE", row.pe)):
                if leg is None or leg.oi is None or leg.oi_change is not None:
                    continue
                base = prev_oi.get((k, side))
                if base is not None:
                    leg.oi_change = round(leg.oi - base, 0)
                    n_doi += 1

        if not rows_by_k:
            return None

        # ---- spot: freshest captured index print; for MCX (no index) the front
        # future is the reference; then the greek-engine underlying; else a
        # put-call-parity proxy off the chain itself ----
        def _last_ltp(kind):
            r = con.execute(
                f"SELECT ltp FROM quote_snapshots WHERE symbol=? AND kind='{kind}' "
                + ("AND received_ts<=? " if ref_ts else "")
                + "ORDER BY received_ts DESC LIMIT 1",
                ((u, ref_ts) if ref_ts else (u,))).fetchone()
            return _num(r["ltp"]) if r else None

        spot, spot_src = _last_ltp("INDEX"), "captured_index"
        if spot is None:
            spot = _last_ltp("FUTURE")
            spot_src = "captured_future"
        if spot is None and oi_meta.get("underlying_price") is not None:
            spot, spot_src = oi_meta["underlying_price"], "greek_engine_underlying"
        if spot is None:
            spot_src = "parity_proxy"

        n = len(rows_by_k)
        legs = [l for r in rows_by_k.values() for l in (r.ce, r.pe) if l is not None]
        n_greek = sum(1 for l in legs if l.delta is not None)
        n_oi = sum(1 for l in legs if l.oi is not None)
        chain = OptionChain(
            underlying=u, expiry=exp, ts=ref_ts or datetime.now(timezone.utc).isoformat(),
            source="angelone_captured", spot=spot, rows=list(rows_by_k.values()),
            capability={
                "has_greeks": n_greek > 0,
                "greek_coverage": round(n_greek / max(1, len(legs)), 3),
                "has_iv": any(l.iv is not None for l in legs),
                "has_oi": n_oi > 0,
                "oi_coverage": round(n_oi / max(1, len(legs)), 3),
                "has_oi_change": any(l.oi_change is not None for l in legs),
                "oi_change_coverage": round(n_doi / max(1, n_oi), 3) if n_oi else 0.0,
                "oi_change_source": "derived_prev_session_close" if n_doi else None,
                "oi_change_baseline_date": prev_date if n_doi else None,
                "has_ltp": n_ltp > 0,
                "ltp_coverage": round(n_ltp / max(1, len(legs)), 3),
                "ltp_band_strikes": len({k for (k, _s) in q_ov}),
                "spot_source": spot_src,
                "cadence_sec": _cadence_sec(con, u, exp),
                "oi_source": ("greek_exposure" if n_oi_filled else
                              "quote_snapshots" if n_oi else None),
                "oi_as_of": oi_meta.get("as_of_ts"),
                "oi_stale": oi_stale,
                "greek_snap_key": snap,
                "pcr_oi": oi_meta.get("pcr_oi"),
            },
            notes=notes + [
                f"price/oi overlay: {n_ltp} legs priced, "
                f"{len({k for (k, _s) in q_ov})} strikes in the live band",
                f"wing-oi overlay: +{n_oi_filled} legs from greek_exposure "
                f"@ {oi_meta.get('as_of_ts') or 'none'}"
                + (" (STALE)" if oi_stale else ""),
                (f"chg-in-oi: derived on {n_doi} legs vs the {prev_date} close "
                 f"(broker sends no chg-in-OI field)" if n_doi
                 else "chg-in-oi: no prior-session OI baseline -> Δ unavailable"),
                f"spot {spot} ({spot_src})",
            ],
        )
        return chain.sort().compute_atm()
    finally:
        con.close()
