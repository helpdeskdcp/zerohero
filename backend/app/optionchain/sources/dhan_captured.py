"""Read-only Dhan history; no authentication, network or provider substitution."""
from datetime import datetime, timezone
import os
import sqlite3

from ..chain import OptionChain, OptionLeg, StrikeRow
from ...histcap import read_contract as rc
from .angelone_chain import _db_path


def fetch(underlying, expiry, *, db_path=None, at_ts=None, expiry_dates=None):
    path = _db_path(db_path)
    if not os.path.exists(path):
        return None
    cutoff = at_ts or datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as c:
            c.row_factory = sqlite3.Row
            from ...histcap.id_window import newest_matching      # bounded id window instead of sorting every OPTION row of the symbol (was 260 s per call)
            latest = newest_matching(c, "received_ts", "symbol=? AND kind='OPTION' AND source='DHAN_CHAIN' AND julianday(received_ts)<=julianday(?)",
                                     (underlying.upper(), cutoff), cutoff, order="julianday(received_ts) DESC, id DESC",
                                     exists=("SELECT 1 FROM quote_snapshots INDEXED BY sqlite_autoindex_quote_snapshots_1 WHERE source='DHAN_CHAIN' AND symbol=? AND kind='OPTION' LIMIT 1", (underlying.upper(),)))
            if not latest:
                return None
            session = rc.instant(cutoff if at_ts else latest[0]).astimezone(rc.IST).date().isoformat()
            data = rc.options(c, underlying, session, provider="DHAN", expiry=expiry,
                              as_of=cutoff, expiry_dates=expiry_dates)
            if not data["rows"]:
                return None
            legs = {}
            for r in data["rows"]:
                leg = OptionLeg(**{k: r[k] for k in
                    ("ltp", "bid", "ask", "bid_qty", "ask_qty", "oi", "oi_change", "volume")},
                    security_id=r["token"], provenance={"provider": "DHAN", "quote_source": r["source"],
                    "calendar": data["calendar"],
                    "oi_source": r["source"], "timestamp": r["timestamp"], "expiry": r["expiry"],
                    "instrument_key": r["instrument_key"], "quote_id": r["id"], "raw_id": r["raw_id"]})
                greek = c.execute("SELECT * FROM option_greeks WHERE underlying=? AND expiry=? "
                    "AND strike=? AND option_type=? AND source='DHAN_OPTION_GREEK' AND broker_status='OK' "
                    "AND received_ts=? AND (server_ts IS NULL OR julianday(server_ts)<=julianday(?)) "
                    "ORDER BY id DESC LIMIT 1", (underlying.upper(), r["expiry"], r["strike"],
                    r["option_type"], r["received_ts"], cutoff)).fetchone()
                if greek:
                    for field in ("delta", "gamma", "theta", "vega", "iv"):
                        setattr(leg, field, greek[field])
                    leg.provenance.update(greeks_source=greek["source"], iv_source=greek["source"], greek_id=greek["id"])
                legs[(r["strike"], r["option_type"])] = leg
            rows = [StrikeRow(strike=k, ce=legs.get((k, "CE")), pe=legs.get((k, "PE")))
                    for k in sorted({k[0] for k in legs})]
            n = sum(l.delta is not None for l in legs.values())
            return OptionChain(underlying=underlying.upper(), expiry=data["expiry"],
                ts=data["rows"][-1]["timestamp"], source="dhan_captured", rows=rows,
                capability={"has_greeks": bool(n), "greek_coverage": n / (2 * len(rows)),
                            "calendar": data["calendar"],
                            "has_oi": any(l.oi is not None for l in legs.values()), "provider": "DHAN"}
                ).compute_atm().with_expiry_ctx()
    except (sqlite3.Error, ValueError, TypeError):
        return None
