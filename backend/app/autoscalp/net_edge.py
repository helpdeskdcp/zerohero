"""Net-of-cost edge monitor for the autonomous scalper.

Why it exists (first run, 2026-10-24, 178 resolved paper signals 31-Aug..14-Oct): gross edge per 1-lot trade about -Rs 80, round-trip charges + half-spread slippage
about Rs 185, net about -Rs 265 per trade (90 % CI -356..-169). The stored r_multiple (+0.17) is a different, un-costed quantity and hid this. This module measures
the REAL rupee expectancy per symbol, with an alert-day-block bootstrap, and turns it into three read-only statuses:
  SUSPEND_RECOMMENDED : n >= 40 resolved trades and the 90 % upper bound of net Rs/trade is below 0
  EVIDENCE_OK         : the last 100 resolved trades have a 90 % LOWER bound above 0 (the only state a LIVE entry is allowed from)
  WATCH               : anything else (not enough trades, or a CI that includes 0)
Thresholds were fixed on economic grounds AFTER the first look at the data (so they are not tuned on it, but they were not pre-registered either: said plainly).
Nothing here places or cancels an order; the optional enforcement is in Safeguards (net_edge_enforce, default OFF) and the LIVE entry gate (fails closed)."""
from __future__ import annotations

import os
import sqlite3
import time

import numpy as np
import pandas as pd

MIN_N_SUSPEND, N_EVIDENCE, CI_Q = 40, 100, 5            # 90 % two-sided -> 5th / 95th percentile
HALF = {"NIFTY": 0.0012, "BANKNIFTY": 0.0019, "SENSEX": 0.0014, "NATURALGAS": 0.0046, "CRUDEOIL": 0.0018, "GOLDM": 0.0033}      # measured median half-spread of the traded option (arena spread_model, 21-23 Oct 2026)
UNDERLYINGS = ("BANKNIFTY", "NIFTY", "SENSEX", "BANKEX", "FINNIFTY", "MIDCPNIFTY", "NATURALGAS", "NATGASMINI", "CRUDEOILM", "CRUDEOIL", "GOLDM")      # longest prefix first for tradingsymbol -> underlying
_cache: dict = {}


def underlying_of(tradingsymbol: str) -> str | None:
    s = (tradingsymbol or "").upper()
    for u in UNDERLYINGS:
        if s.startswith(u): return u
    return None


def _lot(und: str) -> int:
    from ..instrument_profiles import get_instrument_profile
    return int(get_instrument_profile(und).lot_size.value)


def _friction_rs(und: str, entry: float, exit_: float, lot: int) -> float:
    from ..arena.settle import cost_total_rs
    cost = cost_total_rs(und, entry, exit_, lot, 1) or 0.0
    return float(cost + (entry + exit_) * HALF.get(und, 0.003) * lot)


def load_rows(db_path: str | None = None) -> pd.DataFrame:
    from .. import db as _db
    p = db_path or _db.DB_PATH
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=15)
    try:
        return pd.read_sql_query("SELECT id, symbol, created_ts, entry, exit_price, exit_reason FROM scalp_signals WHERE r_multiple IS NOT NULL AND exit_reason IS NOT NULL "
                                 "AND exit_reason!='TIME_NODATA' AND entry IS NOT NULL AND exit_price IS NOT NULL ORDER BY id", c)
    finally:
        c.close()


def _boot_ci(net: np.ndarray, days: np.ndarray, B: int = 2000, seed: int = 3) -> tuple[float, float]:
    g = pd.Series(net).groupby(days); s = g.sum().values; n = g.count().values
    if len(s) < 4: return float("nan"), float("nan")
    rng = np.random.default_rng(seed); out = [s[k].sum() / n[k].sum() for k in (rng.integers(0, len(s), len(s)) for _ in range(B))]
    return float(np.percentile(out, CI_Q)), float(np.percentile(out, 100 - CI_Q))


def analyze(rows: pd.DataFrame, lot_fn=None, friction_fn=None) -> dict:
    lot_fn = lot_fn or _lot; friction_fn = friction_fn or _friction_rs; recs = []
    for r in rows.itertuples():
        und = r.symbol
        try: lot = lot_fn(und)
        except Exception: continue
        gross = (r.exit_price - r.entry) * lot; fr = friction_fn(und, float(r.entry), float(r.exit_price), lot)
        recs.append((und, str(r.created_ts)[:10], gross, fr, gross - fr))
    d = pd.DataFrame(recs, columns=["sym", "day", "gross", "friction", "net"]); out = {}
    for sym, g in d.groupby("sym"):
        lo, hi = _boot_ci(g["net"].values, g["day"].values); last = g.tail(N_EVIDENCE); elo, _ = _boot_ci(last["net"].values, last["day"].values) if len(last) >= N_EVIDENCE else (float("nan"), float("nan"))
        status = "SUSPEND_RECOMMENDED" if (len(g) >= MIN_N_SUSPEND and hi < 0) else ("EVIDENCE_OK" if (len(last) >= N_EVIDENCE and elo > 0) else "WATCH")
        out[sym] = {"n": int(len(g)), "gross_rs_per_trade": round(float(g.gross.mean()), 1), "friction_rs_per_trade": round(float(g.friction.mean()), 1), "net_rs_per_trade": round(float(g.net.mean()), 1),
                    "net_ci90": [round(lo, 1), round(hi, 1)], "total_net_rs": round(float(g.net.sum()), 0), "status": status}
    allr = d; lo, hi = _boot_ci(allr["net"].values, allr["day"].values) if len(allr) else (float("nan"), float("nan"))
    out["ALL"] = {"n": int(len(allr)), "gross_rs_per_trade": round(float(allr.gross.mean()), 1) if len(allr) else None, "friction_rs_per_trade": round(float(allr.friction.mean()), 1) if len(allr) else None,
                  "net_rs_per_trade": round(float(allr.net.mean()), 1) if len(allr) else None, "net_ci90": [round(lo, 1), round(hi, 1)]}
    return out


FUT_COST_BP = {"NIFTY": 1.0, "BANKNIFTY": 1.0, "SENSEX": 1.5, "NATURALGAS": 4.0, "CRUDEOIL": 3.0, "GOLDM": 3.0}          # assumed round-trip cost of trading the FUTURE instead of the option (bp)
_KIND = {"NIFTY": "INDEX", "BANKNIFTY": "INDEX", "SENSEX": "INDEX", "NATURALGAS": "FUTURE", "CRUDEOIL": "FUTURE", "GOLDM": "FUTURE"}


def _underlying_price(sym: str, ts: str, hist_db: str | None = None):
    """1-minute close of the underlying (index, or the most-traded future) nearest to ts, within 150 s; None if the capture has a hole there."""
    from datetime import datetime, timezone
    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    f = lambda e: datetime.fromtimestamp(e, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    p = hist_db or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data", "market_history.db")
    c = sqlite3.connect(f"file:{os.path.abspath(p)}?mode=ro", uri=True, timeout=10)
    try: r = c.execute("SELECT bar_start, c, v FROM market_candles WHERE symbol=? AND kind=? AND tf='1m' AND bar_start BETWEEN ? AND ?", (sym, _KIND.get(sym, "INDEX"), f(t - 150), f(t + 30))).fetchall()
    finally: c.close()
    if not r: return None
    best = max(r, key=lambda x: (x[0], x[2] or 0)); bt = datetime.fromisoformat(best[0].replace("Z", "+00:00")).timestamp()
    return float(best[1]) if abs(bt - t) <= 150 else None


def underlying_edge(db_path: str | None = None, price_fn=None) -> dict:
    """Does the SIGNAL move the underlying in its direction between entry and exit? Option charges, theta and spread are irrelevant here: this is the edge a futures trade could capture,
    minus an assumed futures cost. (2026-10-24: +0.27 bp gross over ~16 min, 46 % hit, net -2.2 bp -> switching the vehicle to futures would not rescue the signal.)"""
    from .. import db as _db
    price_fn = price_fn or _underlying_price; p = db_path or _db.DB_PATH
    c = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=15)
    try:
        s = pd.read_sql_query("SELECT symbol, decision, created_ts, entry_ts, exit_ts, holding_sec FROM scalp_signals WHERE r_multiple IS NOT NULL AND exit_reason IS NOT NULL AND exit_reason!='TIME_NODATA' AND exit_ts IS NOT NULL", c)
    finally:
        c.close()
    rows = []
    for r in s.itertuples():
        try: p0, p1 = price_fn(r.symbol, r.entry_ts or r.created_ts), price_fn(r.symbol, r.exit_ts)
        except Exception: continue
        if p0 and p1: sg = 1 if r.decision == "BUY_CE" else -1; rows.append((r.symbol, str(r.entry_ts or r.created_ts)[:10], sg * (p1 / p0 - 1) * 1e4, r.holding_sec or 0))
    d = pd.DataFrame(rows, columns=["sym", "day", "bp", "hold"]); out = {}
    if d.empty: return out
    d["net"] = d["bp"] - d["sym"].map(FUT_COST_BP).fillna(3.0)
    for k, g in list(d.groupby("sym")) + [("ALL", d)]:
        lo, hi = _boot_ci(g["net"].values, g["day"].values)
        out[k] = {"n": int(len(g)), "avg_hold_min": round(float(g["hold"].mean()) / 60, 1), "gross_bp": round(float(g["bp"].mean()), 2), "hit": round(float((g["bp"] > 0).mean()), 2), "net_bp_after_futures_cost": round(float(g["net"].mean()), 2), "net_ci90": [round(lo, 2), round(hi, 2)]}
    return out


def report(db_path: str | None = None, max_age: float = 300.0) -> dict:
    k = db_path or ""; hit = _cache.get(k)
    if hit and time.time() - hit[0] < max_age: return hit[1]
    try: ue = underlying_edge(db_path)
    except Exception as e: ue = {"error": repr(e)}
    v = {"as_of": time.strftime("%F %T"), "per_symbol": analyze(load_rows(db_path)), "underlying_edge_bp": ue, "note": "Rs per 1 lot per trade, after charges + half-spread slippage, 90% day-block bootstrap CI; entries/exits are the stored signal marks"}
    _cache[k] = (time.time(), v); return v


def symbol_status(symbol: str, db_path: str | None = None) -> str:
    try: return report(db_path)["per_symbol"].get((symbol or "").upper(), {}).get("status", "WATCH")
    except Exception: return "WATCH"


def live_evidence_ok(tradingsymbol: str, db_path: str | None = None) -> tuple[bool, str]:
    """Fail-closed: a LIVE ENTRY on an underlying is allowed only from EVIDENCE_OK. CHANAKYA_NET_EDGE_OVERRIDE=1 is an explicit, logged bypass (owner's decision)."""
    und = underlying_of(tradingsymbol)
    if os.environ.get("CHANAKYA_NET_EDGE_OVERRIDE") == "1": return True, "net-edge evidence gate OVERRIDDEN by CHANAKYA_NET_EDGE_OVERRIDE=1"
    if not und: return False, f"net-edge evidence gate: unknown underlying for {tradingsymbol!r}"
    try: st = symbol_status(und, db_path); row = report(db_path)["per_symbol"].get(und)
    except Exception as e: return False, f"net-edge evidence gate: could not compute ({e!r})"
    if st == "EVIDENCE_OK": return True, "ok"
    return False, f"net-edge evidence gate: {und} is {st} ({row['n']} trades, net Rs {row['net_rs_per_trade']}/trade, 90% CI {row['net_ci90']}) — needs the last {N_EVIDENCE} trades with a positive 90% lower bound" if row else f"net-edge evidence gate: no resolved trades for {und}"
