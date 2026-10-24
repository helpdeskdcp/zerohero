"""/api/scanner/*: read-only views of the in-house scanner (auth'd by the normal gate)."""
from __future__ import annotations

import json
import os
import time

import pandas as pd
from fastapi import APIRouter

from . import backtest as BT, data as D, engine, intraday as I, store
from .library import SCANS

router = APIRouter(prefix="/api/scanner", tags=["scanner"])
RESULTS = os.path.join(D.ROOT, "data", "research", "scanner", "results_backtest.json")
_cache: dict = {}


def forward_report(path: str | None = None, loader=None) -> dict:
    """Realised forward result of the stored hits: entry open of D+1, exit close of D+h (h = 1, 3, 5, 10 when those bars exist), net of the spec's assumed cost."""
    loader = loader or D.load_daily
    with store.conn(path) as c: hits = [dict(r) for r in c.execute("SELECT inst, scan, side, signal_date, close FROM hits ORDER BY signal_date DESC").fetchall()]
    frames, rows = {}, []
    for h in hits:
        if h["inst"] not in frames:
            try: frames[h["inst"]] = loader(h["inst"])
            except Exception: frames[h["inst"]] = None
        df = frames[h["inst"]]
        if df is None: continue
        idx = df.index[pd.to_datetime(df["date"]) == pd.Timestamp(h["signal_date"])]
        if not len(idx): continue
        i = int(idx[0]); sg = 1.0 if h["side"] == "LONG" else -1.0; r = {k: h[k] for k in ("inst", "scan", "side", "signal_date")}
        for hz in BT.HORIZONS:
            r[f"h{hz}"] = round(sg * (df["c"].iloc[i + hz] / df["o"].iloc[i + 1] - 1) * 1e4 - BT.COST[h["inst"]], 1) if i + hz < len(df) else None
        rows.append(r)
    return {"hits": len(hits), "evaluated": rows[:200], "note": "net bps after the assumed cost; None = horizon not yet reached; the historical verdicts are in /api/scanner/backtest"}


@router.get("/scans")
def scans(): return {"scans": [{"name": k, "side": v[0], "expr": v[1]} for k, v in SCANS.items()], "instruments": D.INSTRUMENTS}


@router.get("/hits")
def hits(limit: int = 100):
    with store.conn() as c:
        rows = [dict(r) for r in c.execute("SELECT inst, scan, side, signal_date, close FROM hits ORDER BY signal_date DESC, id DESC LIMIT ?", (min(limit, 500),)).fetchall()]
        last = c.execute("SELECT ts, group_name FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    return {"hits": rows, "last_run": dict(last) if last else None}


@router.get("/backtest")
def backtest():
    try: return json.load(open(RESULTS))
    except Exception: return {"error": "results_backtest.json not found"}


@router.get("/forward")
def forward():
    if _cache.get("t", 0) > time.time() - 600: return _cache["v"]
    v = forward_report(); _cache.update(t=time.time(), v=v); return v


# ---------------------------------------------------------------- chart-scan page (frontend/static/chartscan.html)
RESULTS_5M = os.path.join(D.ROOT, "data", "research", "scanner", "results_5m.json")
_ccache: dict = {}
IST_SHIFT = 19800


def _cached(key, ttl, fn):
    hit = _ccache.get(key)
    if hit and time.time() - hit[0] < ttl: return hit[1]
    v = fn(); _ccache[key] = (time.time(), v); return v


def _daily_df(inst: str):
    from . import stocks as ST
    if inst in ST.UNIVERSE: return _cached(("d", inst), 600, lambda: ST.load_stock(inst))
    return _cached(("d", inst), 600, lambda: D.load_daily(inst))


def _fut5_df(inst: str): return _cached(("5", inst), 90, lambda: I.futures_5m(inst, since_epoch=time.time() - 12 * 86400, until_epoch=time.time()))


def _bt_summary(tf: str, scan: str) -> dict | None:
    try:
        r = json.load(open(RESULTS if tf == "1d" else RESULTS_5M)); x = (r["scans"] if tf == "1d" else r["variants"]).get(scan)
        if not x: return None
        hs = {str(h): {"n": v.get("n"), "mean_net_bps": v.get("mean_net_bps", v.get("mean_net")), "hit": v.get("hit")} for h, v in x["horizons"].items()}
        return {"verdict": x.get("verdict"), "horizons": hs}
    except Exception:
        return None


def chart_payload(inst: str, tf: str, scan: str, bars: int = 250, daily_df=None, fut5_df=None) -> dict:
    inst = inst.upper(); bars = max(20, min(int(bars), 1500))
    from . import stocks as ST
    if inst not in D.INSTRUMENTS and inst not in ST.UNIVERSE: return {"error": f"unknown instrument {inst}"}
    if tf == "5m" and inst not in D.INSTRUMENTS: return {"error": "5-minute scans run on the six futures only"}
    if tf == "1d":
        df = daily_df if daily_df is not None else _daily_df(inst)
        if df is None or df.empty: return {"error": "no data"}
        side = SCANS[scan][0] if scan in SCANS else None; sig = engine.evaluate(df, {scan: SCANS[scan]}).get(scan) if scan in SCANS else None
        t = df.tail(bars).reset_index(drop=True); off = len(df) - len(t)
        candles = [{"time": str(pd.Timestamp(r.date).date()), "open": float(r.o), "high": float(r.h), "low": float(r.l), "close": float(r.c), "volume": float(r.v or 0)} for r in t.itertuples()]
        marks = [{"time": candles[i]["time"], "side": side, "scan": scan} for i in range(len(t)) if sig is not None and bool(sig.iloc[off + i])]
        return {"inst": inst, "tf": tf, "scan": scan, "side": side, "expr": SCANS[scan][1] if scan in SCANS else None, "candles": candles, "markers": marks, "backtest": _bt_summary(tf, scan), "last_bar": candles[-1]["time"], "bars": len(candles)}
    if tf == "5m":
        df = fut5_df if fut5_df is not None else _fut5_df(inst)
        if df is None or len(df) < 40: return {"error": "not enough 5-minute data"}
        f = I.scan_flags(df); side = ("LONG" if "BULL" in scan else "SHORT") if scan in I.VARIANTS else None
        t = df.tail(bars).reset_index(drop=True); off = len(df) - len(t); ep = [int(pd.Timestamp(x).timestamp()) + IST_SHIFT for x in t["t"]]
        candles = [{"time": ep[i], "open": float(t.o[i]), "high": float(t.h[i]), "low": float(t.l[i]), "close": float(t.c[i]), "volume": float(t.v[i] or 0)} for i in range(len(t))]
        marks = [{"time": ep[i], "side": side, "scan": scan, "rsi": round(float(f["rsi"].iloc[off + i]), 1) if f["rsi"].notna().iloc[off + i] else None} for i in range(len(t)) if side and bool(f[scan].iloc[off + i])]
        return {"inst": inst, "tf": tf, "scan": scan, "side": side, "candles": candles, "markers": marks, "backtest": _bt_summary(tf, scan), "last_bar": ep[-1], "bars": len(candles), "tz_note": "times shifted to IST for display"}
    return {"error": "tf must be 1d or 5m"}


@router.get("/chart")
def chart(inst: str = "NIFTY", tf: str = "1d", scan: str = "BRK20_UP", bars: int = 250): return chart_payload(inst, tf, scan, bars)


@router.get("/instruments")
def instruments():
    from . import stocks as ST
    return {"core": D.INSTRUMENTS, "stocks": list(ST.UNIVERSE)}


@router.get("/stock-hits")
def stock_hits():
    """Latest stored hits of the stock universe (filled by the STOCKS daily run), grouped by scan."""
    from . import stocks as ST
    with store.conn() as c:
        d = c.execute("SELECT MAX(signal_date) FROM hits WHERE inst NOT IN (%s)" % ",".join("?" * len(D.INSTRUMENTS)), D.INSTRUMENTS).fetchone()[0]
        rows = [dict(r) for r in c.execute("SELECT inst, scan, side, signal_date, close FROM hits WHERE signal_date=? AND inst NOT IN (%s)" % ",".join("?" * len(D.INSTRUMENTS)), (d, *D.INSTRUMENTS)).fetchall()] if d else []
    return {"signal_date": d, "hits": rows}


@router.get("/results")
def results(tf: str = "1d"):
    """Latest-bar hits across the six instruments (Chartink-style result table); each row opens in the chart."""
    def build():
        rows = []
        for inst in D.INSTRUMENTS:
            try:
                if tf == "1d":
                    df = _daily_df(inst)
                    if df is None or len(df) < 30: continue
                    for name, sig in engine.evaluate(df).items():
                        if bool(sig.iloc[-1]): rows.append({"inst": inst, "scan": name, "side": SCANS[name][0], "bar": str(pd.Timestamp(df["date"].iloc[-1]).date()), "close": float(df["c"].iloc[-1])})
                else:
                    df = _fut5_df(inst)
                    if df is None or len(df) < 60: continue
                    f = I.scan_flags(df)
                    for v in I.VARIANTS:
                        if bool(f[v].iloc[-1]): rows.append({"inst": inst, "scan": v, "side": "LONG" if "BULL" in v else "SHORT", "bar": str(df["t"].iloc[-1]), "close": float(df["c"].iloc[-1])})
            except Exception as e:
                rows.append({"inst": inst, "error": repr(e)[:120]})
        return rows
    extra = []
    if tf == "1d":
        try: extra = [{**h, "bar": h["signal_date"]} for h in stock_hits()["hits"]]
        except Exception: extra = []
    return {"tf": tf, "results": _cached(("r", tf), 120, build) + extra, "scans": ([{"name": k, "side": v[0]} for k, v in SCANS.items()] if tf == "1d" else [{"name": v, "side": "LONG" if "BULL" in v else "SHORT"} for v in I.VARIANTS])}
