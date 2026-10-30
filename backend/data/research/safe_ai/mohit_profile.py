"""Profile of the INSTITUTION_TRADER (Mohit) signals against the index at the moment each was posted. Read-only. Output: data/research/safe_ai/mohit_profile.json + a printed summary."""
import json, sqlite3, statistics as st, collections, os, sys
from datetime import datetime, timezone, timedelta
sys.path.insert(0, ".")
from app.arena import run as R
IST = timezone(timedelta(hours=5, minutes=30))
db = sqlite3.connect("file:data/market_history.db?mode=ro", uri=True, timeout=60)
_idx_cache = {}

def idx_series(sym, day):
    k = (sym, day)
    if k not in _idx_cache:
        rows = db.execute("SELECT received_ts, ltp FROM quote_snapshots WHERE session_date_ist=? AND symbol=? AND kind='INDEX' AND ltp IS NOT NULL ORDER BY received_ts", (day, sym)).fetchall()
        ser = []
        for ts, p in rows:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
            ser.append((t, float(p)))
        _idx_cache[k] = ser
    return _idx_cache[k]

def at(ser, t):
    lo = None
    for x in ser:
        if x[0] <= t: lo = x
        else: break
    return lo

from app.arena import learning as LRN
sigs = LRN.for_learning([s for s in R.human_signals(include_practice=True) if s["participant"] == "INSTITUTION_TRADER"])          # days the owner excluded from learning (see data/arena/learning_exclusions.json) are not profiled
res = {r["id"]: r for r in R.run(sigs, market_db="data/market_history.db")["INSTITUTION_TRADER"]["results"]}
out = []
for s in sorted(sigs, key=lambda x: x["t"]):
    dt = datetime.fromtimestamp(s["t"], IST); day = dt.strftime("%Y-%m-%d"); und = s["underlying"]
    ser = idx_series(und, day)
    row = {"id": s["id"], "ist": dt.strftime("%d-%b %H:%M"), "und": und, "ot": s["option_type"], "strike": s["strike"], "expiry": s["expiry"], "dte": (datetime.fromisoformat(s["expiry"]).date() - dt.date()).days,
           "entry": s["entry"], "mode": s["entry_mode"], "sl": s["sl"], "t1": (s["targets"] or [None])[0], "lots": s.get("lots") or 1}
    if s["sl"]: row["sl_pct"] = round((s["entry"] - s["sl"]) / s["entry"] * 100, 1)
    if s["sl"] and s["targets"]: row["rr1"] = round((s["targets"][0] - s["entry"]) / (s["entry"] - s["sl"]), 2)
    p = at(ser, s["t"])
    if p and s["t"] - p[0] < 600:
        spot = p[1]; row["spot"] = spot; row["otm_pct"] = round(((s["strike"] - spot) / spot * 100) * (1 if s["option_type"] == "CE" else -1), 2)
        day_ser = [x for x in ser if x[0] <= s["t"]]
        if day_ser:
            o = day_ser[0][1]; hi = max(x[1] for x in day_ser); lo = min(x[1] for x in day_ser)
            row["from_open_pct"] = round((spot / o - 1) * 100, 2); row["range_pos"] = round((spot - lo) / (hi - lo), 2) if hi > lo else None
            row["mins_from_first_tick"] = round((s["t"] - day_ser[0][0]) / 60)
        for m in (5, 15, 30, 60):
            q = at(ser, s["t"] - m * 60)
            if q and (s["t"] - m * 60) - q[0] < 300: row[f"chg{m}m_pct"] = round((spot / q[1] - 1) * 100, 3)
        # with / against the last 15-min index move
        c15 = row.get("chg15m_pct")
        if c15 is not None and abs(c15) > 0.02: row["with_trend15"] = (c15 > 0) == (s["option_type"] == "CE")
    r = res.get(s["id"], {}); row["status"] = r.get("status"); row["net"] = round(r["net_rs"]) if r.get("status") == "SETTLED" else None; row["why"] = r.get("exit_reason")
    out.append(row)
json.dump(out, open("data/research/safe_ai/mohit_profile.json", "w"), indent=1)
print(f"{'id':7}{'ist':13}{'und':10}{'ot':3}{'dte':>4}{'entry':>8}{'otm%':>7}{'sl%':>6}{'rr1':>6}{'chg15':>7}{'rngpos':>7}{'trend':>6}{'net':>8} why")
for r in out:
    print(f"{r['id']:7}{r['ist']:13}{r['und']:10}{r['ot']:3}{r['dte']:>4}{r['entry']:>8}{str(r.get('otm_pct','')):>7}{str(r.get('sl_pct','')):>6}{str(r.get('rr1','')):>6}{str(r.get('chg15m_pct','')):>7}{str(r.get('range_pos','')):>7}{str(r.get('with_trend15','')):>6}{str(r['net']):>8} {r['why'] or r['status']}")
