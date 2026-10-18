"""Read-only research API for the synchronized Price-Action / Order-Flow chart page. No writes, no order path, SQLite opened mode=ro.
Kill switch: PA_OF_LAB=0 (main.py does not mount the router)."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from . import confirm, pa_events as ev, ticks as tk
from .schema import IST_SEC, SIDE_NOTE, SRC_CANDLE, SRC_PROXY

router = APIRouter(prefix="/api/pa_of_lab", tags=["pa-of-lab"])
MAX_BARS, STALE_ALERT_SEC = 700, 120
TFS = {60, 180, 300, 900}
DEFINITIONS = {
    "delta": "buy - sell, where buy/sell are INFERRED aggressor volumes (classified ticks); UNKNOWN-side volume is excluded and shown separately",
    "cvd_segment": "running sum of bar delta, reset at the start of every contiguous gap-free segment (never carried across a feed gap or a new session)",
    "classification": "QUOTE_RULE = trade vs OBSERVED best bid/ask; TICK_RULE = vs previous trade price (fallback). Both are INFERRED estimates, not broker-confirmed aggressor flags",
    "imbalance": "stacked imbalance (engine rule): >=3 consecutive occupied price levels with >=3:1 buy/sell (or sell/buy), per-level volume >=10; needs a tick size (INFERRED from data)",
    "data_quality": "OK needs: bar closed, no feed gap, >=30 ticks, >=60% quote-rule classified, <=10% unknown-side volume (thresholds UNCALIBRATED)",
}


def _now() -> float: return time.time()


def today_ist(now: float | None = None) -> str: return datetime.fromtimestamp((now or _now()) + IST_SEC, tz=timezone.utc).strftime("%Y-%m-%d")


def _dates_back(date: str, n: int) -> list[str]:
    d = datetime.strptime(date, "%Y-%m-%d")
    return [(d - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(0, 10)]      # days without data are skipped by the caller


def _candle_bars(symbol: str, date: str, tf_sec: int) -> list[dict]:
    from ..research_engines.smart_bos.data import load_1m
    from ..research_engines.hybrid_mp.engine import resample
    d0 = datetime.strptime(date, "%Y-%m-%d"); d1 = d0 + timedelta(days=1)
    s0, s1 = tk.session_of(symbol)
    b1 = load_1m(symbol, "INDEX", start=date, end=d1.strftime("%Y-%m-%d"), use_cache=False, prefer="ANGELONE_CANDLES")
    if len(b1["t"]) == 0: return []
    ch = resample(b1, tf_sec // 60, s0, s1)
    return [{"symbol": symbol, "kind": "INDEX", "tf_sec": tf_sec, "t": int(ch["t"][i]), "o": float(ch["o"][i]), "h": float(ch["h"][i]), "l": float(ch["l"][i]), "c": float(ch["c"][i]),
             "volume": None, "buy": None, "sell": None, "unknown": None, "delta": None, "n_ticks": None, "quote_share": None, "spread_mean": None, "gap_in_bar": False, "closed": True,
             "levels": None, "side_source": "NONE", "provenance": SRC_CANDLE,
             "dq": {"status": "UNAVAILABLE", "reasons": ["CANDLE_ONLY: no bid/ask, trades or depth in this source"]}} for i in range(len(ch["t"]))]


def available(path: str | None = None) -> list[dict]:
    c = tk.connect_ro(path)
    try:
        rows = c.execute("SELECT symbol,kind,date(ltt+19800,'unixepoch'),COUNT(*) FROM classified_ticks WHERE symbol IS NOT NULL AND kind='FUTURE' GROUP BY 1,2,3 ORDER BY 3 DESC,1").fetchall()
    finally:
        c.close()
    return [{"symbol": r[0], "kind": r[1], "date": r[2], "ticks": r[3]} for r in rows]


def build_chart(symbol: str, date: str, tf_sec: int = 300, source: str = "ticks", days: int = 1, as_of: float | None = None, path: str | None = None, now: float | None = None) -> dict:
    now = now or _now()
    if tf_sec not in TFS: raise ValueError(f"tf_sec must be one of {sorted(TFS)}")
    if not (1 <= days <= 3): raise ValueError("days must be 1..3")
    kind = "FUTURE" if source == "ticks" else "INDEX"
    s0, s1 = tk.session_of(symbol)
    bars, rep_tot, last_ltt, tick_size, ts_src = [], {}, None, None, "NO_DATA"
    got, today = 0, today_ist(now)
    for dd in _dates_back(date, days):
        if got >= days: break
        if source == "ticks":
            rows, nnull = tk.load_rows(symbol, kind, dd, path)
            if not rows: continue
            acc, rep = tk.prepare(rows, symbol)
            if not acc: continue
            if tick_size is None: tick_size, ts_src = tk.infer_tick_size([t["ltp"] for t in acc])
            fin = dd < today or ((now + IST_SEC) // 60) % 1440 >= s1
            b = tk.build_bars(acc, symbol, kind, tf_sec, tick_size=tick_size, gaps=rep["gaps"], final_closed=fin)
            rep["symbol_null_rows_same_day_excluded"] = nnull
            rep_tot[dd] = rep
            last_ltt = acc[-1]["ltt"] if dd == date else last_ltt
        else:
            b = _candle_bars(symbol, dd, tf_sec)
            if not b: continue
            rep_tot[dd] = {"candles": len(b)}
        bars = b + bars; got += 1
    bars.sort(key=lambda x: x["t"])
    if as_of is not None:                                         # replay: only what was knowable at as_of
        bars = [b for b in bars if b["t"] <= as_of]
        for b in bars:
            if b["t"] + b["tf_sec"] > as_of: b["closed"] = False
    if len(bars) > MAX_BARS: bars = bars[-MAX_BARS:]
    meta = {"symbol": symbol, "kind": kind, "tf_sec": tf_sec, "date": date, "days": days, "session_ist_min": [s0, s1], "price_source": SRC_PROXY if source == "ticks" else SRC_CANDLE,
            "price_source_note": "NIFTY FUTURE traded prices from Dhan Full-feed ticks" if source == "ticks" else "INDEX 5m candles (no volume, no bid/ask)", "side_note": SIDE_NOTE,
            "tick_size": tick_size, "tick_size_source": ts_src, "as_of": as_of, "generated_at": now, "time_axis": "epoch seconds UTC of bar START; IST = +19800"}
    if not bars:
        return {"meta": meta, "status": {"state": "NO_DATA", "message": f"no {source} data for {symbol} on {date}", "feed": None, "data_quality": {"reports": rep_tot}}, "bars": [],
                "footprint": {"available": False, "reason": "no data"}, "pa": None, "of": None, "combined": [], "definitions": DEFINITIONS}
    ev.with_day(bars)
    pv = ev.pivots(bars); prov = ev.provisional_pivots(bars)
    st = ev.structure(bars, pv)
    sl = ev.session_levels(bars, s0)
    pa_sigs = ev.signals(bars, s0, s1)
    fp_avail = source == "ticks"
    of_sigs = confirm.of_signals(bars) if fp_avail else []
    comb = confirm.combine(bars, pa_sigs, of_sigs)
    # footprint payload
    fbars, cvd_seg, cvd_day, prev_day, prev_t = [], 0, 0, None, None
    seg_start = {seg[0] for seg in tk.segments(bars)}
    for i, b in enumerate(bars):
        if fp_avail:
            if i in seg_start: cvd_seg = 0
            if b["day"] != prev_day: cvd_day = 0
            prev_day = b["day"]
            cvd_seg += b["delta"]; cvd_day += b["delta"]
            fbars.append({"t": b["t"], "idx": i, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "buy": b["buy"], "sell": b["sell"], "unknown": b["unknown"], "delta": b["delta"],
                          "cvd_segment": cvd_seg, "cvd_day_observed": cvd_day, "volume": b["volume"], "n_ticks": b["n_ticks"], "quote_share": b["quote_share"], "spread_mean": b["spread_mean"],
                          "gap_in_bar": b["gap_in_bar"], "closed": b["closed"], "side_source": b["side_source"], "dq": b["dq"], "stacked": b.get("stacked_imbalances"),
                          "levels": [[p, v[0], v[1]] for p, v in b["levels"].items()]})
    pbars = [{k: b[k] for k in ("t", "o", "h", "l", "c", "volume", "closed", "day")} for b in bars]
    age = (now - last_ltt) if (last_ltt and date == today) else None
    in_sess = s0 <= ((now + IST_SEC) // 60) % 1440 < s1
    stale = bool(date == today and in_sess and source == "ticks" and (age is None or age > STALE_ALERT_SEC))
    dqc = {}
    for b in bars: dqc[b["dq"]["status"]] = dqc.get(b["dq"]["status"], 0) + 1
    status = {"state": "STALE" if stale else "OK", "message": "feed stale: last tick %ss ago" % int(age) if stale and age else None,
              "feed": {"mode": "LIVE_TODAY" if date == today else "HISTORICAL_REPLAY", "last_tick_t": last_ltt, "age_sec": age, "stale": stale},
              "data_quality": {"bars_by_dq": dqc, "reports": rep_tot}}
    return {"meta": meta, "status": status, "bars": pbars,
            "footprint": {"available": fp_avail, "reason": None if fp_avail else "OHLCV candles cannot establish footprint/delta/absorption; no historical bid/ask or aggressor data is stored for this source. Pick a tick-covered date (see /meta).",
                          "bars": fbars},
            "pa": {"pivots": pv, "provisional": prov, "events": st["events"], "levels": sl["levels"], "opening_range": sl["opening_range"], "vwap": sl["vwap"], "vwap_note": sl["vwap_note"],
                   "signals": pa_sigs, "regime": ev.regime(bars), "baseline_note": ev.COST_NOTE},
            "of": {"signals": of_sigs}, "combined": comb, "definitions": DEFINITIONS}


@router.get("/meta")
def meta():
    if os.environ.get("PA_OF_LAB", "1") == "0": raise HTTPException(503, "disabled (PA_OF_LAB=0)")
    try: av = available()
    except Exception as e: raise HTTPException(500, f"{type(e).__name__}: {e}")
    return {"tick_sources": av, "candle_symbols": ["NIFTY", "BANKNIFTY"], "timeframes_sec": sorted(TFS), "note": "footprint exists only for tick-covered (symbol,date) pairs"}


@router.get("/chart")
def chart(symbol: str = Query("NIFTY", pattern="^[A-Z0-9]{2,20}$"), date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"), tf: int = 300, source: str = Query("ticks", pattern="^(ticks|candles)$"),
          days: int = 1, as_of: float | None = None):
    if os.environ.get("PA_OF_LAB", "1") == "0": raise HTTPException(503, "disabled (PA_OF_LAB=0)")
    try: return build_chart(symbol, date, tf, source, days, as_of)
    except ValueError as e: raise HTTPException(400, str(e))
    except Exception as e: raise HTTPException(500, f"{type(e).__name__}: {e}")
