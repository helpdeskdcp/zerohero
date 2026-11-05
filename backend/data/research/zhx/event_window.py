"""How many ZHX signals fall inside scheduled-event windows? (feasibility + descriptive; NOT a candidate test: MCX 1-minute history starts 2026-06/07).
MCX: ZHX run on 5m bars built from stored 1-minute FUTURE candles; event = NG storage Thursday 20:00 IST (until 2026-10-29; 21:00 from 2026-11-02, none yet), crude inventory Wednesday 20:00 IST. Window = release .. +60 min.
NSE/BSE: weekday breakdown of the 3-index trade set (validate_v1d.json), descriptive only."""
import json, os, sqlite3, sys
import numpy as np, pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, os.path.join(HERE, "..", "..", ".."))
from app.smart_chart import runner
SRC = open(os.path.join(HERE, "zhx_v1.pine")).read(); DB = os.path.join(HERE, "..", "..", "market_history.db"); out = {"mcx": {}}
def load(sym):
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=120)
    d = pd.read_sql_query("SELECT bar_start, expiry, o, h, l, c FROM market_candles WHERE symbol=? AND kind='FUTURE' AND tf='1m' ORDER BY bar_start", c, params=(sym,)); c.close()
    d["ts"] = pd.to_datetime(d["bar_start"], utc=True).dt.tz_convert("Asia/Kolkata"); d["day"] = d.ts.dt.strftime("%Y%m%d").astype(int)
    keep = d.groupby("day")["expiry"].agg(lambda x: x.value_counts().idxmax()); d = d[d.expiry == d.day.map(keep)]
    return d.drop_duplicates("ts").set_index("ts").sort_index().rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})
for sym, dow in (("NATURALGAS", 3), ("CRUDEOIL", 2)):
    d = load(sym); b = d.resample("5min", label="left", closed="left").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    cs = [{"time": int(t.timestamp() * 1000), "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": 0.0} for t, r in b.iterrows()]
    r = runner.execute({"op": "run", "source": SRC, "candles": cs, "tf": "5", "symbol": sym}, count=False, timeout=290)
    if not r.get("ok"): print(sym, "FAILED", r.get("diagnostics")); continue
    P = {p["title"]: p["values"] for p in r["plots"]}; sig = P["ZHX Signal"]; rr = P["ZHX Realised R"]
    idx = [i for i, v in enumerate(sig) if v]; allsig = [(b.index[i], int(sig[i])) for i in idx]
    ev = [(t, s) for t, s in allsig if t.weekday() == dow and 20 * 60 <= t.hour * 60 + t.minute < 21 * 60 and t.strftime("%Y%m%d") <= "20261029"]
    evdays = sorted({t.strftime("%Y-%m-%d") for t in b.index if t.weekday() == dow and t.strftime("%Y%m%d") <= "20261029"})
    out["mcx"][sym] = {"bars": len(b), "first": str(b.index[0].date()), "last": str(b.index[-1].date()), "signals_total": len(allsig), "event_days_in_sample": len(evdays), "signals_in_event_window": len(ev), "closed_trades_total": int(sum(1 for v in rr if v is not None)),
                       "mean_R_all_closed": round(float(np.mean([v for v in rr if v is not None])), 3) if any(v is not None for v in rr) else None}
    print(sym, out["mcx"][sym], flush=True)
v = json.load(open(os.path.join(HERE, "validate_v1d.json")))["results"]; days = {}
for s, r0 in v.items():
    out.setdefault("weekday", {})[s] = {}
    for wd in range(5):
        sel = [x for x in r0["trade_rows"] if pd.Timestamp(str(x["day"])).weekday() == wd]
        out["weekday"][s][wd] = {"n": len(sel), "grossR": round(float(np.mean([x["R"] for x in sel])), 3)} if sel else {"n": 0}
    print(s, "weekday (Mon..Fri) n/grossR:", [(out["weekday"][s][w]["n"], out["weekday"][s][w].get("grossR")) for w in range(5)])
json.dump(out, open(os.path.join(HERE, "event_window.json"), "w"), indent=1)
