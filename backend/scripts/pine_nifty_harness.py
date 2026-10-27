"""Harness for the NIFTY expiry Pine strategy: builds NIFTY candles from the stored 1-minute index bars and executes Pine through the PineTS runner
(app/smart_chart/pine_runner, PineTS 0.13.0). This is a COMPATIBLE-ENGINE check - it is NOT the TradingView compiler.
usage: pine_nifty_harness.py compile|run <file.pine> [--tf 5] [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--bars N] [--inputs '{"k":v}']"""
import argparse, json, os, sqlite3, sys
os.environ.setdefault('SMARTCHART_NODE_CPU_SEC', '1800'); os.environ.setdefault('SMARTCHART_NODE_HEAP_MB', '3072'); os.environ.setdefault('SMARTCHART_EXEC_TIMEOUT', '1500'); os.environ.setdefault('SMARTCHART_MAX_OUTPUT_BYTES', '700000000')   # offline research process only
from datetime import datetime, timedelta, timezone
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))); sys.path.insert(0, HERE)
IST = timezone(timedelta(hours=5, minutes=30))
DB = os.path.join(HERE, "data", "historical", "stocks_1m", "stocks_1m.db")
TFSEC = {"1": 60, "3": 180, "5": 300, "15": 900, "30": 1800, "60": 3600}


def candles(tf="5", d_from=None, d_to=None, sym="NIFTY", bars=None):
    sec = TFSEC[tf]; c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=30)
    lo = int(datetime.fromisoformat((d_from or "2024-10-01") + "T00:00:00+05:30").timestamp()); hi = int(datetime.fromisoformat((d_to or "2026-10-26") + "T23:59:59+05:30").timestamp())
    out, cur = [], None
    for ts, o, h, l, cl, v in c.execute("SELECT ts,o,h,l,c,v FROM bars WHERE sym=? AND ts BETWEEN ? AND ? ORDER BY ts", (sym, lo, hi)):
        day0 = datetime.fromtimestamp(ts, IST).replace(hour=9, minute=15, second=0, microsecond=0).timestamp()
        b = int(day0 + ((ts - day0) // sec) * sec)
        if cur is None or cur["t"] != b:
            if cur: out.append(cur)
            cur = {"t": b, "o": o, "h": h, "l": l, "c": cl, "v": v or 0}
        else:
            cur["h"] = max(cur["h"], h); cur["l"] = min(cur["l"], l); cur["c"] = cl; cur["v"] += v or 0
    if cur: out.append(cur)
    rows = [{"time": r["t"] * 1000, "open": r["o"], "high": r["h"], "low": r["l"], "close": r["c"], "volume": r["v"]} for r in out]
    return rows[-bars:] if bars else rows


def run_multi(source, tf="3", d_from=None, d_to=None, bars=None, inputs=None, base_rows=None):
    """Run on a `tf`-minute chart whose every timeframe (incl. request.security) is aggregated from 1-minute bars by the research worker."""
    from app.smart_chart import runner
    base = base_rows if base_rows is not None else candles("1", d_from, d_to)
    chart = candles(tf, d_from, d_to) if base_rows is None else None
    if bars and chart is not None:
        chart = chart[-bars:]; t0 = chart[0]["time"]; base = [b for b in base if b["time"] >= t0]
    old = runner.WORKER; runner.WORKER = os.path.join(os.path.dirname(runner.WORKER), "worker_research.mjs")
    try:
        res = runner.execute({"op": "run", "source": source, "candles": chart or base[:5], "base_candles": base, "base_tf": "1", "tf": tf, "symbol": "NIFTY", "inputs": inputs or {}, "props": {}}, "multi", timeout=1500, count=False)
    finally:
        runner.WORKER = old
    return res


def unpack(pl):
    """expand the packed data-window plots into the individual series the audit/tests use (see part5_output.pine)"""
    import math as _m
    n = len(next(iter(pl.values()))); nan_ = float("nan"); out = dict(pl)
    names = ["E HTF", "E gap scenario", "E levels", "E opening candle", "E structure", "E sweep", "E volume", "E momentum", "E vwap", "E premium", "E OI"]
    for k in names: out[k] = [nan_] * n
    for k in range(4): out[f"HTF vote {k + 1}"] = [nan_] * n
    for k in ("Stress bull", "Stress bear", "Stress bull zone slot", "Stress bear zone slot"): out[k] = [nan_] * n
    for i in range(n):
        c = pl["Components pack"][i]
        if not _m.isnan(c):
            c = int(round(c))
            for j, k in enumerate(names): out[k][i] = float((c // 3 ** j) % 3 - 1)
        h = pl["HTF votes pack"][i]
        if not _m.isnan(h):
            h = int(round(h))
            for j in range(4): out[f"HTF vote {j + 1}"][i] = float((h // 3 ** j) % 3 - 1)
        f = pl["Stress flags (1 bull, 2 bear)"][i]
        if not _m.isnan(f): out["Stress bull"][i] = float(int(f) & 1); out["Stress bear"][i] = float((int(f) >> 1) & 1)
        z = pl["Stress zone slots"][i]
        if not _m.isnan(z): z = int(round(z)); out["Stress bull zone slot"][i] = float(z // 10 - 1); out["Stress bear zone slot"][i] = float(z % 10 - 1)
    out["Ref prevH"] = pl["Prev-day high"]; out["Ref prevL"] = pl["Prev-day low"]; out["Ref prevC"] = pl["Prev-day close"]; out["Ref vwap/twap"] = pl["VWAP / TWAP"]
    out["HTF evidence"] = out["E HTF"]
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("op"); ap.add_argument("file"); ap.add_argument("--tf", default="5"); ap.add_argument("--from", dest="d_from"); ap.add_argument("--to", dest="d_to")
    ap.add_argument("--bars", type=int); ap.add_argument("--inputs", default="{}"); ap.add_argument("--out"); a = ap.parse_args()
    from app.smart_chart import runner
    src = open(a.file, encoding="utf-8").read()
    if a.op == "compile":
        res = runner.execute({"op": "compile", "source": src}, "harness", count=False)
    else:
        rows = candles(a.tf, a.d_from, a.d_to, bars=a.bars)
        res = runner.execute({"op": "run", "source": src, "candles": rows, "tf": a.tf, "symbol": "NIFTY", "inputs": json.loads(a.inputs), "props": {}}, "harness", timeout=600, count=False)
        res["_bars"] = len(rows)
    if a.out: json.dump(res, open(a.out, "w"), default=str)
    print(json.dumps({k: (v if k in ("ok", "kind", "title", "_bars", "diagnostics") else "...") for k, v in res.items()}, default=str)[:3000])


if __name__ == "__main__": main()
