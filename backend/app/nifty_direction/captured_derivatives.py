"""Point-in-time NIFTY futures + near-money option-chain reader over the histcap capture (market_history.db: quote_snapshots, option_greeks). Read-only.
Causality: only rows with exchange timestamp <= as_of AND received <= as_of are used. Nothing is interpolated, estimated or back-filled: a field the capture
did not store stays None and the downstream validation then marks the data unavailable. OI exists in the capture only for the near-money strikes the quote
poller covers, so the chain's OI scope is reported as NEAR_MONEY, never as full-chain."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

DB = os.environ.get("NIFTY_DIR_MARKET_DB") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "market_history.db"))
BAND_STEPS = 10                      # strikes either side of ATM read for OI/volume
STEP = {"NIFTY": 50.0}
_MASTER: dict = {"mtime": None, "futs": {}, "opt_expiries": set()}


def iso_z(epoch: float) -> str: return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(s: str | None) -> float | None:
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError: return None


def _exp_date(x: str) -> date | None:
    for f in ("%d%b%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try: return datetime.strptime(str(x).upper(), f).date()
        except ValueError: pass
    return None


def master_view(underlying: str = "NIFTY") -> dict:
    """token of each listed index future and the listed option expiries, from the current Angel instrument master (cached by file mtime)."""
    from .. import instruments as I
    path = os.path.abspath(I.MASTER_CACHE)
    try: mt = os.path.getmtime(path)
    except OSError: return {"futs": {}, "opt_expiries": set()}
    if _MASTER["mtime"] != mt or _MASTER.get("u") != underlying:
        futs, opts = {}, set()
        for r in I.master_rows():
            if str(r.get("name") or "").upper() != underlying or r.get("exch_seg") != "NFO": continue
            if r.get("instrumenttype") == "FUTIDX": futs[str(r.get("expiry")).upper()] = str(r.get("token"))
            elif r.get("instrumenttype") == "OPTIDX": opts.add(str(r.get("expiry")).upper())
        _MASTER.update(mtime=mt, futs=futs, opt_expiries=opts, u=underlying)
    return {"futs": _MASTER["futs"], "opt_expiries": _MASTER["opt_expiries"]}


class CapturedDerivatives:
    def __init__(self, as_of: float, path: str | None = None, underlying: str = "NIFTY"):
        self.as_of = as_of; self.u = underlying; self.path = path or DB; self._iso = iso_z(as_of)
        self._c = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=15); self._c.row_factory = sqlite3.Row

    def close(self): self._c.close()

    def _latest(self, symbol, kind, expiry=None, strike=None, otype=None):
        """Newest row whose exchange time AND receive time are <= as_of (exch_ts falls back to received_ts when the capture had none)."""
        if kind == "OPTION":
            sql = "SELECT * FROM quote_snapshots WHERE symbol=? AND expiry=? AND strike=? AND option_type=? AND exch_ts<=? ORDER BY exch_ts DESC LIMIT 6"; args = (symbol, expiry, strike, otype, self._iso)
        elif expiry:
            sql = "SELECT * FROM quote_snapshots WHERE symbol=? AND kind=? AND expiry=? AND exch_ts<=? ORDER BY exch_ts DESC LIMIT 6"; args = (symbol, kind, expiry, self._iso)
        else:
            sql = "SELECT * FROM quote_snapshots WHERE symbol=? AND kind=? AND exch_ts<=? ORDER BY exch_ts DESC LIMIT 6"; args = (symbol, kind, self._iso)
        for r in self._c.execute(sql, args):
            if (r["received_ts"] or "") <= self._iso and (r["quote_status"] in (None, "OK")): return r
        return None

    def _spot(self):
        """Newest INDEX capture received <= as_of (the capture stores no exchange timestamp for index rows, so the receive time is used)."""
        r = self._c.execute("SELECT ltp, received_ts FROM quote_snapshots WHERE symbol=? AND kind='INDEX' AND exch_ts IS NULL AND received_ts<=? AND (quote_status IS NULL OR quote_status='OK') ORDER BY received_ts DESC LIMIT 1", (self.u, self._iso)).fetchone()
        return (r["ltp"], _epoch(r["received_ts"])) if r and r["ltp"] else (None, None)

    # ---- futures ----
    def futures(self) -> dict | None:
        m = master_view(self.u)["futs"]
        cands = [r[0] for r in self._c.execute("SELECT DISTINCT expiry FROM quote_snapshots WHERE symbol=? AND kind='FUTURE' AND exch_ts>=? AND exch_ts<=?", (self.u, iso_z(self.as_of - 5 * 86400), self._iso)) if r[0]]
        today = datetime.fromtimestamp(self.as_of, timezone(timedelta(hours=5, minutes=30))).date()
        cands = sorted((e for e in cands if _exp_date(e) and _exp_date(e) >= today), key=_exp_date)
        for exp in cands[:1]:                                              # front month only
            r = self._latest(self.u, "FUTURE", exp)
            if r is None or r["ltp"] is None or r["oi"] is None or r["oi_change"] is None: return None
            listed = m.get(exp.upper()); tok = str(r["token"])
            ident = "LISTED_IN_MASTER" if listed and listed == tok else ("TOKEN_MISMATCH" if listed else "CAPTURED_NOT_IN_CURRENT_MASTER")
            if ident == "TOKEN_MISMATCH": return None                       # captured token is not the listed contract: refuse
            ts = _epoch(r["exch_ts"]) or _epoch(r["received_ts"])
            return {"symbol": f"{self.u}{exp}FUT", "expiry": exp, "ltp": r["ltp"], "prev_close": r["close"], "volume": r["volume"], "oi": r["oi"], "prev_oi": r["oi"] - r["oi_change"],
                    "basis": r["basis"], "ts": ts, "token": tok, "identity": ident, "source": r["source"], "snapshot_id": r["id"]}
        return None

    # ---- option chain (near-money OI/volume + whole-chain greeks) ----
    def option_chain(self) -> dict | None:
        today = datetime.fromtimestamp(self.as_of, timezone(timedelta(hours=5, minutes=30))).date()
        day = datetime.fromtimestamp(self.as_of, timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d")
        exps = [r[0] for r in self._c.execute("SELECT DISTINCT expiry FROM option_greeks INDEXED BY ix_greeks_day WHERE session_date_ist=? AND underlying=? AND received_ts<=?", (day, self.u, self._iso)) if r[0]]
        exps = sorted((e for e in exps if _exp_date(e) and _exp_date(e) >= today), key=_exp_date)
        if not exps: return None
        exp = exps[0]; listed = master_view(self.u)["opt_expiries"]
        ident = "LISTED_IN_MASTER" if exp.upper() in listed else "CAPTURED_NOT_IN_CURRENT_MASTER"
        spot, spot_ts = self._spot()
        g = self._c.execute("SELECT snap_key, received_ts FROM option_greeks INDEXED BY ix_greeks_day WHERE underlying=? AND expiry=? AND session_date_ist=? AND received_ts<=? AND broker_status='OK' ORDER BY received_ts DESC LIMIT 1",
                            (self.u, exp, day, self._iso)).fetchone()
        if g is None: return None
        greeks = {(r["strike"], r["option_type"]): r for r in self._c.execute("SELECT strike, option_type, iv, delta, received_ts FROM option_greeks INDEXED BY ix_greeks_day WHERE session_date_ist=? AND underlying=? AND expiry=? AND snap_key=? AND broker_status='OK'", (day, self.u, exp, g["snap_key"]))}
        if not greeks: return None
        step = STEP.get(self.u, 50.0); strikes = sorted({k[0] for k in greeks})
        ref = spot if spot else None
        if ref is None: return {"underlying": self.u, "expiry": exp, "ts": None, "rows": [], "identity": ident, "reason": "NO_SPOT"}
        atm = min(strikes, key=lambda s: abs(s - ref)); band = [s for s in strikes if abs(s - atm) <= BAND_STEPS * step]
        rows, oi_ts = [], []
        for s in band:
            row = {"strike": s}
            for side, p in (("ce", "CE"), ("pe", "PE")):
                q = self._latest(self.u, "OPTION", exp, s, p); gr = greeks.get((s, p))
                row[f"{side}_oi"] = q["oi"] if q else None; row[f"{side}_vol"] = q["volume"] if q else None; row[f"{side}_ltp"] = q["ltp"] if q else None
                row[f"{side}_iv"] = gr["iv"] if gr else None; row[f"{side}_delta"] = gr["delta"] if gr else None
                if q and q["oi"] is not None: oi_ts.append(_epoch(q["exch_ts"]) or _epoch(q["received_ts"]))
            rows.append(row)
        g_ts = _epoch(g["received_ts"]); used = [t for t in oi_ts + [g_ts] if t]
        return {"underlying": self.u, "expiry": exp, "ts": min(used) if used else None, "greek_ts": g_ts, "oi_ts": (min(oi_ts) if oi_ts else None), "spot": ref, "spot_ts": spot_ts, "atm": atm, "rows": rows,
                "identity": ident, "oi_scope": "NEAR_MONEY", "source": "histcap capture (ANGELONE_QUOTE_FULL + ANGELONE_OPTION_GREEK)", "snap_key": g["snap_key"]}
