"""ZeroHero ZHX (Pine, liquidity / stop-hunt engine) for the Arena Terminal chart: the SAME Pine script as the Smart Chart template, executed by the Smart Chart Node/PineTS runner on the chart's own candles
(underlying 5 / 15 m, or an option contract's premium candles). Read-only: no order path, nothing is written. Returns BUY CE / BUY PE + exit markers, the live trade's Entry / SL / T1-T4 levels and a tiny dashboard.
A failure never breaks the chart: the response then carries `error` and empty lists."""
from __future__ import annotations

import time

from . import terminal as TM
from ..smart_chart import runner, templates

IST_S = 19800
_CACHE: dict = {}
TTL_SEC = 15.0
MAX_BARS = 700
_SRC: dict = {}


def _source() -> str:
    if "s" not in _SRC: _SRC["s"] = templates._load_pine_file("zhx_v1.pine")
    return _SRC["s"]


def _ms(t_epoch: float) -> int: return int(round(t_epoch * 1000))


def _candles_underlying(sym: str, tf: int, now: float, path: str | None) -> list[dict]:
    d = TM._bars_from_capture(sym, tf, now, days=12, path=path)
    if d is None or len(d) < 60: return []
    d = d.tail(MAX_BARS)
    return [{"time": _ms(ts.timestamp()), "open": float(r.open), "high": float(r.high), "low": float(r.low), "close": float(r.close), "volume": float(r.volume or 0.0)} for ts, r in zip(d["timestamp"], d.itertuples())]


def _candles_option(und: str, expiry: str, strike: float, ot: str, tf: int, now: float, path: str | None) -> list[dict]:
    p = TM.premium(und, expiry, float(strike), ot, tf, None, now, path)
    return [{"time": _ms(c["time"] - IST_S), "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": 0.0} for c in (p.get("candles") or [])][-MAX_BARS:]


def shape(res: dict, times_ms: list[int]) -> dict:
    """Worker result -> what the chart draws. Pure function (unit-tested)."""
    P = {p["title"]: p for p in res.get("plots", [])}
    def hits(title):
        p = P.get(title); return [i for i, v in enumerate(p["values"]) if v] if p else []
    ax = lambda i: int(times_ms[i] // 1000) + IST_S                                               # the chart axis = epoch + IST shift, like the candles
    markers = []
    for title, side, kind in (("BUY CE Confirmed", "BUY CE", "buy"), ("BUY PE Confirmed", "BUY PE", "buy"), ("BUY CE (option chart)", "BUY CE", "buy"), ("BUY PE (option chart)", "BUY PE", "buy"),
                              ("Exit SL", "SL", "exit"), ("Exit T3", "T3", "exit"), ("Exit EOD", "EOD", "exit")):
        for i in hits(title): markers.append({"time": ax(i), "side": side, "kind": kind})
    markers.sort(key=lambda m: m["time"])
    dr = res.get("drawings", {}); last = len(times_ms) - 1; levels = []
    for title, head in (("ZHX Active Entry", "ENTRY"), ("ZHX Active SL", "SL"), ("ZHX Active T1", "T1"), ("ZHX Active T2", "T2"), ("ZHX Active T3", "T3")):         # the live trade's levels = the "active" plots of the LAST bar
        v = (P.get(title) or {"values": []})["values"]
        if last >= 0 and last < len(v) and v[last] is not None: levels.append({"title": f"{head} {round(float(v[last]), 2)}", "price": float(v[last]), "head": head})
    dash = []
    for t in dr.get("tables", []):
        if t.get("rows") == 5 and t.get("columns") == 2: dash = [[(c or {}).get("text", "") for c in row] for row in t["cells"]]
    perf = []
    return {"markers": markers, "levels": levels, "dash": dash, "perf": perf}


def view(und: str, tf: int, option: tuple | None = None, now: float | None = None, path: str | None = None, execute=None) -> dict:
    """option = (expiry_iso, strike, 'CE'|'PE') for a premium chart, else the underlying. Cached 15 s per chart."""
    now = now or time.time(); key = (und, tf, option, path)
    hit = _CACHE.get(key)
    if hit and now - hit[0] < TTL_SEC and execute is None: return hit[1]
    out = {"underlying": und, "tf": tf, "mode": "option" if option else "underlying", "script": "ZeroHero ZHX v1", "markers": [], "levels": [], "dash": [], "perf": [], "bars": 0, "error": None}
    try:
        cs = _candles_option(und, option[0], option[1], option[2], tf, now, path) if option else _candles_underlying(und, tf, now, path)
        out["bars"] = len(cs)
        if len(cs) < 40: out["error"] = "not enough candles yet (need about 40)"
        else:
            inputs = {"chartKind": ("Option CE chart" if option[2] == "CE" else "Option PE chart") if option else "Underlying / Index"}
            if tf != 5: inputs["require5m"] = False                                                 # the script's own 5-minute-only gate would hide every signal on 15 m
            res = (execute or runner.execute)({"op": "run", "source": _source(), "candles": cs, "tf": str(tf), "symbol": und if not option else f"{und}{option[0].replace('-', '')}{int(option[1])}{option[2]}", "inputs": inputs}, count=False)
            if not res.get("ok"): out["error"] = "; ".join(str(d.get("message")) for d in res.get("diagnostics", [])[:2]) or "Pine run failed"
            else: out.update(shape(res, [c["time"] for c in cs]))
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {str(e)[:120]}"
    if execute is None: _CACHE[key] = (now, out)
    return out
