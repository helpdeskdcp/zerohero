"""Read-only loader + bar builder over data/dhan_orderflow_ticks.db (engines/orderflow_store.classified_ticks). RESEARCH ONLY.

Policies (each counted in the returned report, nothing silent):
  * symbol IS NULL rows            -> excluded (`symbol_unresolved`); security_id->symbol is NOT guessed.
  * exact duplicate (sid,ltt,ltp,ltq) -> first kept, rest dropped (`duplicate_dropped`). The live engine only skips *consecutive* equal ltt, so a
    repeat of the same key can only be a re-delivered snapshot (reconnect / restart), not a new trade.
  * same-second, different (ltp|ltq) -> kept (`same_second_multi`); ltt has 1-second resolution so order inside a second is arrival order (row id).
  * out-of-order (ltt < running max) -> excluded like the live engine's `late_ticks` (`late_dropped`).
  * stale (received_ts - ltt > stale_sec) -> excluded (`stale_dropped`); an old LTP is never presented as a fresh trade.
  * gap (> gap_sec between accepted ticks) -> recorded; bars containing a gap are `gap_in_bar` and never used as order-flow evidence;
    missing bars are NOT created, filled, or interpolated.
Bar OHLC/levels are built with the SAME FootprintBar class and bucketing rule as the live engine (parity-tested)."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

from ..engines.orderflow_engine import FootprintBar
from .schema import IST_SEC, SRC_PROXY, dq_of, side_source

DB = os.environ.get("DHAN_ORDERFLOW_TICKS_DB", os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "dhan_orderflow_ticks.db"))
SESSIONS = {"NSE": (555, 930), "MCX": (540, 1410)}          # minutes-of-day IST (open, close)
MCX = {"CRUDEOIL", "NATURALGAS"}
GAP_SEC, STALE_SEC = 180, 90


def session_of(symbol: str) -> tuple[int, int]:
    return SESSIONS["MCX" if symbol in MCX else "NSE"]


def connect_ro(path: str | None = None):
    return sqlite3.connect(f"file:{os.path.abspath(path or DB)}?mode=ro", uri=True, timeout=30)


def day_bounds(date_ist: str) -> tuple[int, int]:
    d = datetime.strptime(date_ist, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    s = int(d.timestamp()) - IST_SEC
    return s, s + 86400


def load_rows(symbol: str, kind: str, date_ist: str, path: str | None = None) -> tuple[list[dict], int]:
    """(rows for symbol/kind/date, n_symbol_null_rows_that_day)."""
    lo, hi = day_bounds(date_ist)
    c = connect_ro(path)
    try:
        cur = c.execute("SELECT id,received_ts,security_id,ltp,ltq,ltt,side,classification_method,best_bid,best_ask FROM classified_ticks "
                        "WHERE symbol=? AND kind=? AND ltt>=? AND ltt<? ORDER BY id", (symbol, kind, lo, hi))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        nnull = c.execute("SELECT COUNT(*) FROM classified_ticks WHERE symbol IS NULL AND ltt>=? AND ltt<?", (lo, hi)).fetchone()[0]
    finally:
        c.close()
    return rows, nnull


def _recv_epoch(s: str) -> float | None:
    try:
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def prepare(rows: list[dict], symbol: str, *, gap_sec: int = GAP_SEC, stale_sec: int = STALE_SEC, session: tuple[int, int] | None = None) -> tuple[list[dict], dict]:
    s0, s1 = session or session_of(symbol)
    rep = {"rows_in": len(rows), "duplicate_dropped": 0, "stale_dropped": 0, "late_dropped": 0, "outside_session_dropped": 0,
           "same_second_multi": 0, "unknown_side": 0, "gaps": [], "accepted": 0}
    seen, out, mx, prev = set(), [], None, None
    for r in (x for _, x in sorted(enumerate(rows), key=lambda q: (q[1].get("id", q[0]), q[0]))):     # ARRIVAL order (row id), like the live engine
        mod = ((r["ltt"] + IST_SEC) // 60) % 1440
        if not (s0 <= mod < s1):
            rep["outside_session_dropped"] += 1; continue
        key = (r["security_id"], r["ltt"], r["ltp"], r["ltq"])
        if key in seen:
            rep["duplicate_dropped"] += 1; continue
        seen.add(key)
        if mx is not None and r["ltt"] < mx:
            rep["late_dropped"] += 1; continue
        re_ = _recv_epoch(r["received_ts"])
        if re_ is not None and re_ - r["ltt"] > stale_sec:
            rep["stale_dropped"] += 1; continue
        if prev is not None:
            if r["ltt"] == prev["ltt"]: rep["same_second_multi"] += 1
            elif r["ltt"] - prev["ltt"] > gap_sec: rep["gaps"].append([prev["ltt"], r["ltt"]])
        mx = r["ltt"] if mx is None else max(mx, r["ltt"])
        if r["side"] == "UNKNOWN": rep["unknown_side"] += 1
        out.append(r); prev = r
    rep["accepted"] = len(out)
    return out, rep


def infer_tick_size(prices) -> tuple[float | None, str]:
    """Largest candidate grid that every observed price fits within 0.006 (Dhan prices are float32). Labelled INFERRED_FROM_DATA."""
    ps = sorted({round(float(p), 4) for p in prices})
    if not ps: return None, "NO_DATA"
    for g in (1.0, 0.5, 0.25, 0.2, 0.1, 0.05, 0.01):
        if all(abs(p / g - round(p / g)) * g <= 0.006 for p in ps): return g, "INFERRED_FROM_DATA"
    return None, "NOT_INFERABLE"


def build_bars(ticks: list[dict], symbol: str, kind: str, tf_sec: int, *, tick_size: float | None = None, gaps: list | None = None,
               final_closed: bool = False) -> list[dict]:
    """Canonical bars from accepted ticks. A bar is `closed` when a LATER bar exists (== live engine: a bar closes when the next tick arrives);
    the last bar is closed only if final_closed (e.g. a finished past session)."""
    gaps = gaps or []
    fps: dict[int, FootprintBar] = {}
    extra: dict[int, dict] = {}
    for t in ticks:
        bs = (t["ltt"] // tf_sec) * tf_sec
        fp = fps.get(bs)
        if fp is None:
            fp = fps[bs] = FootprintBar(bs, tf_sec, t["ltp"], tick_size=tick_size)
            extra[bs] = {"vol": 0, "unk": 0, "spread": [], "n": 0}
        fp.add({"ltp": t["ltp"], "ltq": t["ltq"], "side": t["side"], "classification_method": t["classification_method"]})
        e = extra[bs]; e["n"] += 1; q = max(int(t["ltq"] or 0), 0); e["vol"] += q
        if t["side"] == "UNKNOWN": e["unk"] += q
        if t["best_bid"] and t["best_ask"] and t["best_ask"] > t["best_bid"]: e["spread"].append(t["best_ask"] - t["best_bid"])
    keys = sorted(fps)
    out = []
    for n, bs in enumerate(keys):
        fp, e = fps[bs], extra[bs]
        m = dict(fp.method_counts); tot = sum(m.values())
        qs = sum(v for k, v in m.items() if k.startswith("QUOTE_RULE")) / tot if tot else None
        gap_in = any(g[0] < bs + tf_sec and g[1] > bs for g in gaps)
        closed = (n < len(keys) - 1) or final_closed
        unk_frac = (e["unk"] / e["vol"]) if e["vol"] else None
        out.append({"symbol": symbol, "kind": kind, "tf_sec": tf_sec, "t": bs, "o": fp.open, "h": fp.high, "l": fp.low, "c": fp.close,
                    "volume": e["vol"], "buy": fp.buy_vol, "sell": fp.sell_vol, "unknown": e["unk"], "delta": fp.buy_vol - fp.sell_vol,
                    "n_ticks": e["n"], "quote_share": qs, "spread_mean": (sum(e["spread"]) / len(e["spread"])) if e["spread"] else None,
                    "gap_in_bar": gap_in, "closed": closed, "levels": {p: [v["buy"], v["sell"]] for p, v in sorted(fp.levels.items())},
                    "side_source": side_source(m), "dq": dq_of(e["n"], qs, unk_frac, gap_in, closed), "provenance": SRC_PROXY,
                    "stacked_imbalances": fp.stacked_imbalances() if tick_size else None})
    return out


def segments(bars: list[dict]) -> list[list[int]]:
    """Index runs that are time-contiguous and gap-free. A missing bar or a gap bar BREAKS the run (nothing carries across)."""
    runs, cur = [], []
    for i, b in enumerate(bars):
        if cur and (b["t"] - bars[cur[-1]]["t"] != b["tf_sec"] or b["gap_in_bar"] or bars[cur[-1]]["gap_in_bar"]):
            runs.append(cur); cur = []
        cur.append(i)
    if cur: runs.append(cur)
    return runs
